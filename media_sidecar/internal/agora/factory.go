package agora

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"

	agoraservice "github.com/AgoraIO-Extensions/Agora-Golang-Server-SDK/v2/go_sdk/rtc"

	"github.com/AgoraIO-Extensions/Agora-Golang-Server-SDK/v2/petkit_media/internal/hls"
	"github.com/AgoraIO-Extensions/Agora-Golang-Server-SDK/v2/petkit_media/internal/server"
)

const (
	cameraDuration     = 15 * time.Minute
	connectionTimeout  = 15 * time.Second
	customPayloadType  = 69
	encryptionModeGCM2 = 8
	lanAccelerateParam = `{"rtc.lan_accelerate_enable": true}`
)

// Factory owns the process-wide Agora Server SDK lifecycle.
type Factory struct {
	ffmpegPath  string
	initMu      sync.Mutex
	initialized bool
	closed      bool
	appID       string
}

// NewFactory returns a native PETKIT media factory.
func NewFactory(ffmpegPath string) *Factory {
	return &Factory{ffmpegPath: ffmpegPath}
}

// Create starts one encrypted RTC subscription and HLS remuxer.
func (f *Factory) Create(
	_ context.Context,
	sessionID string,
	access server.CameraAccess,
) (server.Session, error) {
	if err := f.initialize(access.AppID); err != nil {
		return nil, err
	}
	salt, err := base64.StdEncoding.DecodeString(access.MediaEncryption.Salt)
	if err != nil {
		return nil, errors.New("media encryption salt is not valid base64")
	}

	sessionContext, cancel := context.WithCancel(context.Background())
	muxer, err := hls.Start(sessionContext, f.ffmpegPath)
	if err != nil {
		cancel()
		return nil, err
	}
	session := &session{
		id:          sessionID,
		startedAt:   time.Now(),
		expiresAt:   time.Now().Add(cameraDuration),
		muxer:       muxer,
		cancel:      cancel,
		events:      make(chan event, 16),
		users:       make(map[string]bool),
		audioCodecs: make(map[int]int),
	}
	if err := session.start(access, salt); err != nil {
		session.Stop()
		return nil, err
	}
	return session, nil
}

func (f *Factory) initialize(appID string) error {
	f.initMu.Lock()
	defer f.initMu.Unlock()
	if f.closed {
		return errors.New("media factory is closed")
	}
	if f.initialized {
		if f.appID != appID {
			return errors.New("sidecar cannot mix Agora app IDs")
		}
		return nil
	}

	config := agoraservice.NewAgoraServiceConfig()
	config.AppId = appID
	config.EnableAudioProcessor = true
	config.EnableAudioDevice = false
	config.EnableVideo = true
	config.AudioScenario = agoraservice.AudioScenarioGameStreaming
	config.ShouldCallbackWhenMuted = 1
	config.LogPath = "/tmp/petkit-media-agora.log"
	config.ConfigDir = "/tmp"
	config.DataDir = "/tmp"
	if result := agoraservice.Initialize(config); result != 0 {
		return fmt.Errorf("initialize Agora SDK: %d", result)
	}
	if result := agoraservice.GetAgoraParameter().SetParameters(
		fmt.Sprintf(`{"che.audio.custom_payload_type":%d}`, customPayloadType),
	); result != 0 {
		_ = agoraservice.Release()
		return fmt.Errorf("set custom payload type: %d", result)
	}
	f.initialized = true
	f.appID = appID
	return nil
}

// Close releases the process-wide Agora SDK.
func (f *Factory) Close() error {
	f.initMu.Lock()
	defer f.initMu.Unlock()
	if f.closed {
		return nil
	}
	f.closed = true
	if f.initialized {
		if result := agoraservice.Release(); result != 0 {
			return fmt.Errorf("release Agora SDK: %d", result)
		}
		f.initialized = false
	}
	return nil
}

type event struct {
	kind    string
	uid     string
	code    int
	message string
}

type session struct {
	id        string
	startedAt time.Time
	expiresAt time.Time
	muxer     *hls.Muxer
	conn      *agoraservice.RtcConnection
	cancel    context.CancelFunc
	events    chan event

	mu            sync.Mutex
	users         map[string]bool
	audioCodecs   map[int]int
	audioFrames   int64
	videoFrames   int64
	videoBytes    int64
	audioBytes    int64
	videoCodec    int
	width         int
	height        int
	keyFrames     int64
	lastError     string
	firstAudioPTS int64
	lastAudioPTS  int64
	firstVideoPTS int64
	lastVideoPTS  int64

	stopped    atomic.Bool
	stopOnce   sync.Once
	connected  atomic.Bool
	subscribed atomic.Bool
	manifestOK atomic.Bool
}

func (s *session) start(access server.CameraAccess, salt []byte) error {
	connectionConfig := agoraservice.RtcConnectionConfig{
		AutoSubscribeAudio:            false,
		AutoSubscribeVideo:            false,
		EnableAudioRecordingOrPlayout: true,
		ClientRole:                    agoraservice.ClientRoleBroadcaster,
		ChannelProfile:                agoraservice.ChannelProfileLiveBroadcasting,
		AudioRecvEncodedFrame:         true,
	}
	publishConfig := agoraservice.NewRtcConPublishConfig()
	publishConfig.AudioScenario = agoraservice.AudioScenarioGameStreaming
	publishConfig.AudioProfile = agoraservice.AudioProfileDefault
	publishConfig.IsPublishAudio = false
	publishConfig.IsPublishVideo = false
	publishConfig.AudioPublishType = agoraservice.AudioPublishTypeNoPublish
	publishConfig.VideoPublishType = agoraservice.VideoPublishTypeNoPublish

	s.conn = agoraservice.NewRtcConnection(&connectionConfig, publishConfig)
	if s.conn == nil {
		return errors.New("create RTC connection")
	}
	if err := configureRTCConnectionParameters(s.conn.GetAgoraParameter()); err != nil {
		return err
	}
	if result := s.conn.EnableEncryption(1, &agoraservice.EncryptionConfig{
		EncryptionMode:    encryptionModeGCM2,
		EncryptionKey:     access.MediaEncryption.Key,
		EncryptionKdfSalt: salt,
	}); result != 0 {
		return fmt.Errorf("enable RTC encryption: %d", result)
	}

	s.conn.RegisterObserver(&agoraservice.RtcConnectionObserver{
		OnConnected: func(_ *agoraservice.RtcConnection, _ *agoraservice.RtcConnectionInfo, _ int) {
			s.connected.Store(true)
			s.sendEvent(event{kind: "connected"})
		},
		OnDisconnected: func(_ *agoraservice.RtcConnection, _ *agoraservice.RtcConnectionInfo, reason int) {
			s.connected.Store(false)
			s.setError(fmt.Sprintf("RTC disconnected: %d", reason))
			s.sendEvent(event{kind: "disconnected", code: reason})
		},
		OnUserJoined: func(_ *agoraservice.RtcConnection, uid string) {
			s.mu.Lock()
			s.users[uid] = true
			s.mu.Unlock()
			s.sendEvent(event{kind: "user", uid: uid})
		},
		OnUserLeft: func(_ *agoraservice.RtcConnection, uid string, reason int) {
			s.setError(fmt.Sprintf("camera user left: uid=%s reason=%d", uid, reason))
		},
		OnError: func(_ *agoraservice.RtcConnection, code int, message string) {
			s.setError(fmt.Sprintf("RTC error %d: %s", code, message))
			s.sendEvent(event{kind: "error", code: code, message: message})
		},
		OnEncryptionError: func(_ *agoraservice.RtcConnection, code int) {
			s.setError(fmt.Sprintf("RTC encryption error: %d", code))
			s.sendEvent(event{kind: "encryption", code: code})
		},
	})

	s.conn.RegisterAudioEncodedFrameObserver(&agoraservice.AudioEncodedFrameObserver{
		OnEncodedAudioFrameReceived: func(_ string, packet []byte, sendTS int64, codec int) {
			s.mu.Lock()
			s.audioFrames++
			s.audioBytes += int64(len(packet))
			s.audioCodecs[codec]++
			if s.firstAudioPTS == 0 {
				s.firstAudioPTS = sendTS
			}
			s.lastAudioPTS = sendTS
			s.mu.Unlock()
			s.muxer.WriteAudio(packet)
		},
	})
	s.conn.RegisterVideoEncodedFrameObserver(&agoraservice.VideoEncodedFrameObserver{
		OnEncodedVideoFrame: func(_ string, image []byte, info *agoraservice.EncodedVideoFrameInfo) bool {
			s.mu.Lock()
			s.videoFrames++
			s.videoBytes += int64(len(image))
			s.videoCodec = int(info.CodecType)
			s.width = info.Width
			s.height = info.Height
			if info.FrameType == agoraservice.VideoFrameTypeKeyFrame {
				s.keyFrames++
			}
			if s.firstVideoPTS == 0 {
				s.firstVideoPTS = info.CaptureTimeMs
			}
			s.lastVideoPTS = info.CaptureTimeMs
			s.mu.Unlock()
			return s.muxer.WriteVideo(image)
		},
	})
	s.conn.RegisterLocalUserObserver(&agoraservice.LocalUserObserver{
		OnUserAudioTrackSubscribed: func(_ *agoraservice.LocalUser, uid string, _ *agoraservice.RemoteAudioTrack) {
			s.sendEvent(event{kind: "audio_subscribed", uid: uid})
		},
		OnUserAudioTrackStateChanged: func(_ *agoraservice.LocalUser, uid string, _ *agoraservice.RemoteAudioTrack, state int, reason int, _ int) {
			if state == 0 && (reason == 5 || reason == 7) {
				s.setError(fmt.Sprintf("camera audio muted: uid=%s reason=%d", uid, reason))
			}
		},
	})

	if result := s.conn.Connect(access.RTCToken, access.ChannelID, access.RTCUID); result != 0 {
		return fmt.Errorf("connect RTC: %d", result)
	}

	uid, err := s.waitForUser(connectionTimeout)
	if err != nil {
		return err
	}
	localUser := s.conn.GetLocalUser()
	if result := localUser.SubscribeAudio(uid); result != 0 {
		return fmt.Errorf("subscribe camera audio: %d", result)
	}
	if result := localUser.SubscribeVideo(uid, &agoraservice.VideoSubscriptionOptions{
		StreamType:       agoraservice.VideoStreamHigh,
		EncodedFrameOnly: true,
	}); result != 0 {
		return fmt.Errorf("subscribe camera video: %d", result)
	}
	s.subscribed.Store(true)
	_ = s.conn.SendIntraRequest(uid)
	return nil
}

type parameterSetter interface {
	SetParameters(string) int
}

func configureRTCConnectionParameters(setter parameterSetter) error {
	parameters := []string{
		fmt.Sprintf(`{"che.audio.custom_payload_type":%d}`, customPayloadType),
		lanAccelerateParam,
	}
	for _, parameter := range parameters {
		if result := setter.SetParameters(parameter); result != 0 {
			return fmt.Errorf("set connection RTC parameter: %d", result)
		}
	}
	return nil
}

func (s *session) waitForUser(timeout time.Duration) (string, error) {
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	for {
		s.mu.Lock()
		for uid := range s.users {
			s.mu.Unlock()
			return uid, nil
		}
		errMessage := s.lastError
		s.mu.Unlock()
		if errMessage != "" {
			return "", errors.New(errMessage)
		}
		select {
		case <-timer.C:
			return "", errors.New("timed out waiting for the camera RTC user")
		case <-s.events:
		}
	}
}

func (s *session) sendEvent(value event) {
	if s.stopped.Load() {
		return
	}
	select {
	case s.events <- value:
	default:
	}
}

func (s *session) setError(message string) {
	s.mu.Lock()
	s.lastError = message
	s.mu.Unlock()
}

func (s *session) ID() string {
	return s.id
}

func (s *session) Directory() string {
	return s.muxer.Directory()
}

func (s *session) ExpiresAt() time.Time {
	return s.expiresAt
}

func (s *session) Status() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := os.Stat(filepath.Join(s.muxer.Directory(), "video.m3u8")); err == nil {
		if _, audioErr := os.Stat(filepath.Join(s.muxer.Directory(), "audio.m3u8")); audioErr == nil {
			s.manifestOK.Store(true)
		}
	}
	videoDrops, audioDrops := s.muxer.Drops()
	muxerError := ""
	if err := s.muxer.Error(); err != nil {
		muxerError = err.Error()
	}
	return map[string]any{
		"id":                s.id,
		"connected":         s.connected.Load(),
		"subscribed":        s.subscribed.Load(),
		"manifest_ready":    s.manifestOK.Load(),
		"active_seconds":    int(time.Since(s.startedAt).Seconds()),
		"expires_in":        maxInt(0, int(time.Until(s.expiresAt).Seconds())),
		"users":             len(s.users),
		"audio_frames":      s.audioFrames,
		"audio_bytes":       s.audioBytes,
		"audio_codecs":      copyCodecs(s.audioCodecs),
		"first_audio_pts":   s.firstAudioPTS,
		"last_audio_pts":    s.lastAudioPTS,
		"video_frames":      s.videoFrames,
		"video_bytes":       s.videoBytes,
		"video_codec":       s.videoCodec,
		"width":             s.width,
		"height":            s.height,
		"key_frames":        s.keyFrames,
		"first_video_pts":   s.firstVideoPTS,
		"last_video_pts":    s.lastVideoPTS,
		"video_queue_drops": videoDrops,
		"audio_queue_drops": audioDrops,
		"muxer_error":       muxerError,
		"last_error":        s.lastError,
	}
}

func copyCodecs(source map[int]int) map[int]int {
	result := make(map[int]int, len(source))
	for codec, count := range source {
		result[codec] = count
	}
	return result
}

func maxInt(first, second int) int {
	if first > second {
		return first
	}
	return second
}

func (s *session) Stop() {
	s.stopOnce.Do(func() {
		s.stopped.Store(true)
		if s.conn != nil {
			_ = s.conn.Disconnect()
			time.Sleep(200 * time.Millisecond)
			s.conn.Release()
			s.conn = nil
		}
		s.cancel()
		s.muxer.Stop()
	})
}
