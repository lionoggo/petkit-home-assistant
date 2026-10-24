package hls

import (
	"sync/atomic"
	"testing"
)

func TestEnqueueCopiesAndDropsWhenFull(t *testing.T) {
	queue := make(chan []byte, 1)
	done := make(chan struct{})
	var drops atomic.Int64
	source := []byte{1, 2, 3}
	if !enqueue(queue, source, done, &drops) {
		t.Fatal("first packet was not queued")
	}
	source[0] = 9
	if enqueue(queue, source, done, &drops) {
		t.Fatal("full queue accepted a packet")
	}
	if drops.Load() != 1 {
		t.Fatalf("drops = %d, want 1", drops.Load())
	}
	queued := <-queue
	if queued[0] != 1 {
		t.Fatal("packet was not copied before queueing")
	}
}

func TestWriteAudioRejectsInvalidADTS(t *testing.T) {
	muxer := &Muxer{
		audioQ: make(chan []byte, 1),
		done:   make(chan struct{}),
	}
	if muxer.WriteAudio([]byte{0xff, 0xf1, 0x60}) {
		t.Fatal("truncated ADTS frame was queued")
	}
	if muxer.audioDrop.Load() != 1 {
		t.Fatalf("invalid drops = %d, want 1", muxer.audioDrop.Load())
	}

	frame := []byte{0xff, 0xf1, 0x60, 0x40, 0x01, 0x7f, 0xfc, 1, 2, 3, 4}
	if !muxer.WriteAudio(append(append([]byte(nil), frame...), frame...)) {
		t.Fatal("valid ADTS frames were rejected")
	}
}
