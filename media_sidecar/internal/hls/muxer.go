package hls

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

const (
	videoQueueSize = 256
	audioQueueSize = 512
)

type processError struct {
	mu  sync.Mutex
	err error
}

func (p *processError) set(err error) {
	p.mu.Lock()
	p.err = err
	p.mu.Unlock()
}

func (p *processError) get() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.err
}

// Muxer exposes encoded video and audio as two independent HLS renditions.
// Keeping the pipelines separate avoids FFmpeg interleaving stalls between
// timestamp-less H.264 access units and custom PT69 AAC frames.
type Muxer struct {
	directory string
	videoCmd  *exec.Cmd
	audioCmd  *exec.Cmd
	videoPipe *os.File
	audioPipe *os.File
	videoQ    chan []byte
	audioQ    chan []byte
	done      chan struct{}
	exited    chan struct{}
	closeOnce sync.Once
	videoErr  processError
	audioErr  processError
	videoDrop atomic.Int64
	audioDrop atomic.Int64
}

// Start launches independent FFmpeg HLS pipelines.
func Start(ctx context.Context, ffmpegPath string) (*Muxer, error) {
	directory, err := os.MkdirTemp("", "petkit-media-hls-*")
	if err != nil {
		return nil, fmt.Errorf("create HLS directory: %w", err)
	}
	if err := os.Chmod(directory, 0o700); err != nil {
		_ = os.RemoveAll(directory)
		return nil, fmt.Errorf("secure HLS directory: %w", err)
	}

	videoRead, videoWrite, err := os.Pipe()
	if err != nil {
		_ = os.RemoveAll(directory)
		return nil, fmt.Errorf("create video pipe: %w", err)
	}
	audioRead, audioWrite, err := os.Pipe()
	if err != nil {
		_ = videoRead.Close()
		_ = videoWrite.Close()
		_ = os.RemoveAll(directory)
		return nil, fmt.Errorf("create audio pipe: %w", err)
	}

	videoArgs := []string{
		"-hide_banner",
		"-loglevel", "warning",
		"-fflags", "+genpts",
		"-r", "25",
		"-f", "h264",
		"-i", "/dev/fd/3",
		"-c:v", "libx264",
		"-preset", "ultrafast",
		"-tune", "zerolatency",
		"-pix_fmt", "yuv420p",
		"-vf", "setpts=N/(25*TB)",
		"-g", "25",
		"-keyint_min", "25",
		"-sc_threshold", "0",
		"-b:v", "2500k",
		"-f", "hls",
		"-hls_time", "1",
		"-hls_list_size", "6",
		"-hls_flags", "delete_segments+omit_endlist+independent_segments",
		"-hls_segment_type", "mpegts",
		"-hls_segment_filename", filepath.Join(directory, "video-%05d.ts"),
		filepath.Join(directory, "video.m3u8"),
	}
	videoCmd := exec.CommandContext(ctx, ffmpegPath, videoArgs...)
	videoCmd.ExtraFiles = []*os.File{videoRead}
	videoCmd.Stderr = os.Stderr

	audioArgs := []string{
		"-hide_banner",
		"-loglevel", "warning",
		"-fflags", "+genpts",
		"-f", "aac",
		"-probesize", "32",
		"-analyzeduration", "0",
		"-i", "/dev/fd/3",
		"-c:a", "aac",
		"-ar", "16000",
		"-ac", "1",
		"-b:a", "32k",
		"-af", "volume=45dB,aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS",
		"-f", "hls",
		"-hls_time", "1",
		"-hls_list_size", "6",
		"-hls_flags", "delete_segments+omit_endlist+split_by_time",
		"-hls_segment_type", "fmp4",
		"-hls_fmp4_init_filename", "audio-init.mp4",
		"-hls_segment_filename", filepath.Join(directory, "audio-%05d.m4s"),
		filepath.Join(directory, "audio.m3u8"),
	}
	audioCmd := exec.CommandContext(ctx, ffmpegPath, audioArgs...)
	audioCmd.ExtraFiles = []*os.File{audioRead}
	audioCmd.Stderr = os.Stderr

	if err := videoCmd.Start(); err != nil {
		_ = videoRead.Close()
		_ = videoWrite.Close()
		_ = audioRead.Close()
		_ = audioWrite.Close()
		_ = os.RemoveAll(directory)
		return nil, fmt.Errorf("start video ffmpeg: %w", err)
	}
	_ = videoRead.Close()
	if err := audioCmd.Start(); err != nil {
		_ = videoWrite.Close()
		_ = audioRead.Close()
		_ = audioWrite.Close()
		_ = videoCmd.Process.Kill()
		_ = os.RemoveAll(directory)
		return nil, fmt.Errorf("start audio ffmpeg: %w", err)
	}
	_ = audioRead.Close()

	muxer := &Muxer{
		directory: directory,
		videoCmd:  videoCmd,
		audioCmd:  audioCmd,
		videoPipe: videoWrite,
		audioPipe: audioWrite,
		videoQ:    make(chan []byte, videoQueueSize),
		audioQ:    make(chan []byte, audioQueueSize),
		done:      make(chan struct{}),
		exited:    make(chan struct{}),
	}
	go muxer.writeLoop(videoWrite, muxer.videoQ)
	go muxer.writeLoop(audioWrite, muxer.audioQ)
	go muxer.wait()
	return muxer, nil
}

func (m *Muxer) writeLoop(pipe *os.File, queue <-chan []byte) {
	for {
		select {
		case packet := <-queue:
			if _, err := pipe.Write(packet); err != nil {
				return
			}
		case <-m.done:
			return
		}
	}
}

func (m *Muxer) wait() {
	var waitGroup sync.WaitGroup
	waitGroup.Add(2)
	go func() {
		defer waitGroup.Done()
		m.videoErr.set(m.videoCmd.Wait())
	}()
	go func() {
		defer waitGroup.Done()
		m.audioErr.set(m.audioCmd.Wait())
	}()
	waitGroup.Wait()
	close(m.exited)
}

// Directory returns the private directory containing HLS files.
func (m *Muxer) Directory() string {
	return m.directory
}

// WriteVideo queues one encoded access unit without blocking SDK callbacks.
func (m *Muxer) WriteVideo(packet []byte) bool {
	return enqueue(m.videoQ, packet, m.done, &m.videoDrop)
}

// WriteAudio queues one complete ADTS access unit without blocking callbacks.
func (m *Muxer) WriteAudio(packet []byte) bool {
	if !validADTS(packet) {
		m.audioDrop.Add(1)
		return false
	}
	return enqueue(m.audioQ, packet, m.done, &m.audioDrop)
}

func validADTS(packet []byte) bool {
	if len(packet) == 0 {
		return false
	}
	for offset := 0; offset < len(packet); {
		if offset+7 > len(packet) ||
			packet[offset] != 0xff ||
			packet[offset+1]&0xf6 != 0xf0 {
			return false
		}
		frameLength := int(packet[offset+3]&0x03)<<11 |
			int(packet[offset+4])<<3 |
			int(packet[offset+5]>>5)
		if frameLength < 7 || offset+frameLength > len(packet) {
			return false
		}
		offset += frameLength
	}
	return true
}

func enqueue(queue chan []byte, packet []byte, done <-chan struct{}, drops *atomic.Int64) bool {
	if len(packet) == 0 {
		return false
	}
	copyOfPacket := append([]byte(nil), packet...)
	select {
	case <-done:
		return false
	default:
	}
	select {
	case queue <- copyOfPacket:
		return true
	default:
		drops.Add(1)
		return false
	}
}

// WaitManifest waits until both audio and video playlists are available.
func (m *Muxer) WaitManifest(ctx context.Context, timeout time.Duration) error {
	deadline := time.NewTimer(timeout)
	defer deadline.Stop()
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		if fileReady(filepath.Join(m.directory, "video.m3u8")) &&
			fileReady(filepath.Join(m.directory, "audio.m3u8")) {
			return nil
		}
		if err := m.Error(); err != nil {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-deadline.C:
			return errors.New("timed out waiting for HLS playlists")
		case <-ticker.C:
		}
	}
}

func fileReady(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Size() > 0
}

// Error reports a failed media pipeline.
func (m *Muxer) Error() error {
	if err := m.videoErr.get(); err != nil {
		return fmt.Errorf("video ffmpeg stopped: %w", err)
	}
	if err := m.audioErr.get(); err != nil {
		return fmt.Errorf("audio ffmpeg stopped: %w", err)
	}
	return nil
}

// Drops returns the number of frames dropped by the bounded queues.
func (m *Muxer) Drops() (video int, audio int) {
	return int(m.videoDrop.Load()), int(m.audioDrop.Load())
}

// Stop terminates both pipelines and removes their HLS files.
func (m *Muxer) Stop() {
	m.closeOnce.Do(func() {
		close(m.done)
		_ = m.videoPipe.Close()
		_ = m.audioPipe.Close()
		select {
		case <-m.exited:
		case <-time.After(3 * time.Second):
			_ = m.videoCmd.Process.Kill()
			_ = m.audioCmd.Process.Kill()
			<-m.exited
		}
		_ = os.RemoveAll(m.directory)
	})
}
