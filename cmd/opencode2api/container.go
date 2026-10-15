package main

import (
	"fmt"
	"net"
	"net/http"
	"os"
	"time"
)

// The public API and management interface share one listener when configured
// with the same address. ServeMux delegates streaming directly to the gateway.
func publicHandler(api, management http.Handler) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /livez", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		_, _ = w.Write([]byte("{\"status\":\"ok\"}\n"))
	})
	if management != nil {
		mux.Handle("/v1/", api)
		mux.Handle("/healthz", api)
		mux.Handle("/", management)
	} else {
		mux.Handle("/", api)
	}
	return mux
}

func containerHealthcheck() error {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	host := "127.0.0.1"
	if address := os.Getenv("LISTEN_ADDRESS"); address != "" {
		var err error
		host, port, err = net.SplitHostPort(address)
		if err != nil {
			return fmt.Errorf("invalid LISTEN_ADDRESS: %w", err)
		}
		if host == "" || host == "0.0.0.0" {
			host = "127.0.0.1"
		} else if host == "::" {
			host = "::1"
		}
	}
	client := &http.Client{Timeout: 3 * time.Second, Transport: &http.Transport{Proxy: nil}}
	response, err := client.Get("http://" + net.JoinHostPort(host, port) + "/livez")
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("liveness returned HTTP %d", response.StatusCode)
	}
	return nil
}
