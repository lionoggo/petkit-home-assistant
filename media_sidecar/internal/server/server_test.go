package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestAllowedHLSFile(t *testing.T) {
	for _, name := range []string{
		"video.m3u8",
		"audio.m3u8",
		"audio-init.mp4",
		"video-00001.ts",
		"audio-00001.m4s",
	} {
		if !allowedHLSFile(name) {
			t.Fatalf("%q should be allowed", name)
		}
	}
	for _, name := range []string{"../secret", "segment-x.m4s", "stream.ts", ""} {
		if allowedHLSFile(name) {
			t.Fatalf("%q should be rejected", name)
		}
	}
}

func TestValidateAccessRejectsIncompleteInput(t *testing.T) {
	access := CameraAccess{
		AppID:           "app",
		ChannelID:       "channel",
		RTCToken:        "token",
		RTCUID:          "123",
		MediaEncryption: MediaEncryption{Key: "key", Salt: "salt"},
	}
	if err := validateAccess(access); err != nil {
		t.Fatal(err)
	}
	access.RTCToken = ""
	if err := validateAccess(access); err == nil {
		t.Fatal("missing RTC token was accepted")
	}
}

type blockingFactory struct {
	started chan struct{}
	release chan struct{}
	creates atomic.Int32
}

func (f *blockingFactory) Create(
	context.Context,
	string,
	CameraAccess,
) (Session, error) {
	if f.creates.Add(1) == 1 {
		close(f.started)
		<-f.release
	}
	return &testSession{expiresAt: time.Now().Add(time.Hour)}, nil
}

func (f *blockingFactory) Close() error { return nil }

type testSession struct {
	expiresAt time.Time
}

func (s *testSession) ID() string             { return "session" }
func (s *testSession) Directory() string      { return "" }
func (s *testSession) ExpiresAt() time.Time   { return s.expiresAt }
func (s *testSession) Status() map[string]any { return map[string]any{"id": "session"} }
func (s *testSession) Stop()                  {}

func TestSessionLimitReservesCapacityDuringCreate(t *testing.T) {
	factory := &blockingFactory{
		started: make(chan struct{}),
		release: make(chan struct{}),
	}
	server, err := New(strings.Repeat("t", 24), factory, 1)
	if err != nil {
		t.Fatal(err)
	}
	httpServer := httptest.NewServer(server.Handler())
	defer httpServer.Close()

	body := `{"app_id":"app","channel_id":"channel","rtc_token":"token","rtc_uid":"123","media_encryption":{"key":"key","salt":"salt"}}`
	request := func() *http.Response {
		req, err := http.NewRequest(
			http.MethodPost,
			httpServer.URL+"/v1/sessions",
			strings.NewReader(body),
		)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+strings.Repeat("t", 24))
		response, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		return response
	}

	firstDone := make(chan *http.Response, 1)
	go func() { firstDone <- request() }()
	<-factory.started

	second := request()
	if second.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("second status = %d, want 429", second.StatusCode)
	}
	second.Body.Close()
	if factory.creates.Load() != 1 {
		t.Fatalf("factory creates = %d, want 1", factory.creates.Load())
	}

	close(factory.release)
	first := <-firstDone
	if first.StatusCode != http.StatusCreated {
		t.Fatalf("first status = %d, want 201", first.StatusCode)
	}
	first.Body.Close()
}
