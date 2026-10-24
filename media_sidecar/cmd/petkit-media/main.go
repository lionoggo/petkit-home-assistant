package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/AgoraIO-Extensions/Agora-Golang-Server-SDK/v2/petkit_media/internal/agora"
	"github.com/AgoraIO-Extensions/Agora-Golang-Server-SDK/v2/petkit_media/internal/server"
)

func main() {
	log.SetFlags(log.Ldate | log.Ltime | log.LUTC)
	token := os.Getenv("PETKIT_MEDIA_TOKEN")
	address := envOrDefault("PETKIT_MEDIA_ADDR", ":8099")
	ffmpegPath := envOrDefault("PETKIT_MEDIA_FFMPEG", "ffmpeg")
	maxSessions := envInt("PETKIT_MEDIA_MAX_SESSIONS", 4)

	factory := agora.NewFactory(ffmpegPath)
	api, err := server.New(token, factory, maxSessions)
	if err != nil {
		log.Fatal(err)
	}

	httpServer := &http.Server{
		Addr:              address,
		Handler:           api.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	errCh := make(chan error, 1)
	go func() {
		log.Printf("PETKIT media sidecar listening on %s", address)
		errCh <- httpServer.ListenAndServe()
	}()

	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGINT, syscall.SIGTERM)
	select {
	case received := <-signals:
		log.Printf("received %s; stopping sidecar", received)
	case err := <-errCh:
		if !errors.Is(err, http.ErrServerClosed) {
			log.Printf("HTTP server failed: %v", err)
		}
	}

	shutdownContext, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := httpServer.Shutdown(shutdownContext); err != nil {
		log.Printf("HTTP shutdown failed: %v", err)
	}
	if err := api.Close(); err != nil {
		log.Printf("media cleanup failed: %v", err)
	}
}

func envOrDefault(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func envInt(name string, fallback int) int {
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil {
		log.Fatalf("%s must be an integer", name)
	}
	return parsed
}
