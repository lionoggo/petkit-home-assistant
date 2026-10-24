package server

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// MediaEncryption matches the PETKIT camera access payload.
type MediaEncryption struct {
	Key  string `json:"key"`
	Salt string `json:"salt"`
}

// CameraAccess contains short-lived credentials passed from Home Assistant.
type CameraAccess struct {
	AppID           string          `json:"app_id"`
	ChannelID       string          `json:"channel_id"`
	RTCToken        string          `json:"rtc_token"`
	RTCUID          string          `json:"rtc_uid"`
	MediaEncryption MediaEncryption `json:"media_encryption"`
}

// Session is one active native media stream.
type Session interface {
	ID() string
	Directory() string
	ExpiresAt() time.Time
	Status() map[string]any
	Stop()
}

// Factory creates provider-specific sessions.
type Factory interface {
	Create(context.Context, string, CameraAccess) (Session, error)
	Close() error
}

// Server exposes authenticated session lifecycle and HLS endpoints.
type Server struct {
	token        string
	factory      Factory
	maxSessions  int
	mu           sync.Mutex
	sessions     map[string]Session
	expiryTimers map[string]*time.Timer
	pending      int
}

// New creates a sidecar API server.
func New(token string, factory Factory, maxSessions int) (*Server, error) {
	if len(token) < 24 {
		return nil, errors.New("PETKIT_MEDIA_TOKEN must contain at least 24 characters")
	}
	if factory == nil {
		return nil, errors.New("media factory is required")
	}
	if maxSessions < 1 {
		return nil, errors.New("max sessions must be positive")
	}
	return &Server{
		token:        token,
		factory:      factory,
		maxSessions:  maxSessions,
		sessions:     make(map[string]Session),
		expiryTimers: make(map[string]*time.Timer),
	}, nil
}

// Handler returns the authenticated HTTP handler.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", s.onlyMethod(http.MethodGet, s.handleHealth))
	mux.HandleFunc("/v1/sessions", s.onlyMethod(http.MethodPost, s.handleCreate))
	mux.HandleFunc("/v1/sessions/", s.handleSession)
	return s.authenticate(mux)
}

func (s *Server) onlyMethod(method string, next http.HandlerFunc) http.HandlerFunc {
	return func(response http.ResponseWriter, request *http.Request) {
		if request.Method != method {
			response.Header().Set("Allow", method)
			http.Error(response, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		next(response, request)
	}
}

func (s *Server) handleSession(response http.ResponseWriter, request *http.Request) {
	parts := strings.Split(strings.TrimPrefix(request.URL.Path, "/v1/sessions/"), "/")
	if len(parts) == 1 && parts[0] != "" && request.Method == http.MethodDelete {
		s.handleDelete(response, parts[0])
		return
	}
	if len(parts) == 2 && parts[1] == "status" && request.Method == http.MethodGet {
		s.handleStatus(response, parts[0])
		return
	}
	if len(parts) == 2 && request.Method == http.MethodGet {
		s.handleFile(response, request, parts[0], parts[1])
		return
	}
	http.NotFound(response, request)
}

func (s *Server) authenticate(next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		provided := strings.TrimPrefix(request.Header.Get("Authorization"), "Bearer ")
		if subtle.ConstantTimeCompare([]byte(provided), []byte(s.token)) != 1 {
			http.Error(response, "unauthorized", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(response, request)
	})
}

func (s *Server) handleHealth(response http.ResponseWriter, _ *http.Request) {
	s.mu.Lock()
	active := len(s.sessions)
	s.mu.Unlock()
	writeJSON(response, http.StatusOK, map[string]any{
		"ok":              true,
		"active_sessions": active,
		"max_sessions":    s.maxSessions,
	})
}

func (s *Server) handleCreate(response http.ResponseWriter, request *http.Request) {
	request.Body = http.MaxBytesReader(response, request.Body, 64*1024)
	var access CameraAccess
	decoder := json.NewDecoder(request.Body)
	if err := decoder.Decode(&access); err != nil {
		http.Error(response, "invalid camera access", http.StatusBadRequest)
		return
	}
	if err := validateAccess(access); err != nil {
		http.Error(response, err.Error(), http.StatusBadRequest)
		return
	}

	sessionID, err := randomID()
	if err != nil {
		http.Error(response, "cannot allocate session", http.StatusInternalServerError)
		return
	}

	s.mu.Lock()
	if len(s.sessions)+s.pending >= s.maxSessions {
		s.mu.Unlock()
		http.Error(response, "session limit reached", http.StatusTooManyRequests)
		return
	}
	s.pending++
	s.mu.Unlock()

	session, err := s.factory.Create(request.Context(), sessionID, access)
	s.mu.Lock()
	s.pending--
	if err != nil {
		s.mu.Unlock()
		log.Printf("create media session failed: %v", err)
		http.Error(response, "camera media session failed", http.StatusBadGateway)
		return
	}
	if len(s.sessions) >= s.maxSessions {
		s.mu.Unlock()
		session.Stop()
		http.Error(response, "session limit reached", http.StatusTooManyRequests)
		return
	}
	s.sessions[sessionID] = session
	expiresIn := time.Until(session.ExpiresAt())
	if expiresIn <= 0 {
		expiresIn = time.Second
	}
	s.expiryTimers[sessionID] = time.AfterFunc(expiresIn, func() {
		s.stopSession(sessionID)
	})
	s.mu.Unlock()

	writeJSON(response, http.StatusCreated, map[string]any{
		"session_id": sessionID,
		"expires_in": int(expiresIn.Seconds()),
		"status":     session.Status(),
	})
}

func (s *Server) handleDelete(response http.ResponseWriter, sessionID string) {
	if !s.stopSession(sessionID) {
		http.Error(response, "session not found", http.StatusNotFound)
		return
	}
	response.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleStatus(response http.ResponseWriter, sessionID string) {
	s.mu.Lock()
	session := s.sessions[sessionID]
	s.mu.Unlock()
	if session == nil {
		http.Error(response, "session not found", http.StatusNotFound)
		return
	}
	writeJSON(response, http.StatusOK, session.Status())
}

func (s *Server) handleFile(
	response http.ResponseWriter,
	request *http.Request,
	sessionID string,
	filename string,
) {
	if !allowedHLSFile(filename) {
		http.NotFound(response, request)
		return
	}
	s.mu.Lock()
	session := s.sessions[sessionID]
	s.mu.Unlock()
	if session == nil {
		http.NotFound(response, request)
		return
	}
	path := filepath.Join(session.Directory(), filename)
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		http.NotFound(response, request)
		return
	}
	if strings.HasSuffix(filename, ".m3u8") {
		response.Header().Set("Content-Type", "application/vnd.apple.mpegurl")
		response.Header().Set("Cache-Control", "no-store")
	} else if strings.HasSuffix(filename, ".ts") {
		response.Header().Set("Content-Type", "video/mp2t")
		response.Header().Set("Cache-Control", "private, max-age=60")
	} else {
		response.Header().Set("Content-Type", "video/mp4")
		response.Header().Set("Cache-Control", "private, max-age=60")
	}
	file, err := os.Open(path)
	if err != nil {
		http.NotFound(response, request)
		return
	}
	defer file.Close()
	http.ServeContent(response, request, filename, info.ModTime(), file)
}

func (s *Server) stopSession(sessionID string) bool {
	s.mu.Lock()
	session := s.sessions[sessionID]
	if session == nil {
		s.mu.Unlock()
		return false
	}
	delete(s.sessions, sessionID)
	if timer := s.expiryTimers[sessionID]; timer != nil {
		timer.Stop()
		delete(s.expiryTimers, sessionID)
	}
	s.mu.Unlock()
	session.Stop()
	return true
}

// Close stops every session and releases the native factory.
func (s *Server) Close() error {
	s.mu.Lock()
	sessions := make([]Session, 0, len(s.sessions))
	for _, session := range s.sessions {
		sessions = append(sessions, session)
	}
	for _, timer := range s.expiryTimers {
		timer.Stop()
	}
	s.sessions = make(map[string]Session)
	s.expiryTimers = make(map[string]*time.Timer)
	s.mu.Unlock()
	for _, session := range sessions {
		session.Stop()
	}
	return s.factory.Close()
}

func validateAccess(access CameraAccess) error {
	switch {
	case access.AppID == "":
		return errors.New("app_id is required")
	case access.ChannelID == "":
		return errors.New("channel_id is required")
	case access.RTCToken == "":
		return errors.New("rtc_token is required")
	case access.RTCUID == "":
		return errors.New("rtc_uid is required")
	case access.MediaEncryption.Key == "":
		return errors.New("media encryption key is required")
	case access.MediaEncryption.Salt == "":
		return errors.New("media encryption salt is required")
	default:
		return nil
	}
}

func allowedHLSFile(name string) bool {
	if name == "video.m3u8" || name == "audio.m3u8" || name == "audio-init.mp4" {
		return true
	}
	prefix := ""
	extension := ""
	switch {
	case strings.HasPrefix(name, "video-") && strings.HasSuffix(name, ".ts"):
		prefix, extension = "video-", ".ts"
	case strings.HasPrefix(name, "audio-") && strings.HasSuffix(name, ".m4s"):
		prefix, extension = "audio-", ".m4s"
	default:
		return false
	}
	sequence := strings.TrimSuffix(strings.TrimPrefix(name, prefix), extension)
	if sequence == "" {
		return false
	}
	for _, character := range sequence {
		if character < '0' || character > '9' {
			return false
		}
	}
	return true
}

func randomID() (string, error) {
	buffer := make([]byte, 16)
	if _, err := io.ReadFull(rand.Reader, buffer); err != nil {
		return "", err
	}
	return hex.EncodeToString(buffer), nil
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	if err := json.NewEncoder(response).Encode(value); err != nil {
		log.Printf("write media API response: %v", err)
	}
}
