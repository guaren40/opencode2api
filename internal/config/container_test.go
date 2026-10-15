package config

import (
	"bytes"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func containerTestEnvironment(t *testing.T) {
	t.Helper()
	for _, name := range []string{"ADMIN_PASSWORD", "ADMIN_USERNAME", "SERVER_API_KEY", "ZEN_API_KEY", "ZEN_API_KEYS", "GO_API_KEY", "GO_API_KEYS", "ANONYMOUS", "ZEABUR", "ZEABUR_SERVICE_ID", "PASSWORD", "LISTEN_ADDRESS", "WEBUI_LISTEN_ADDRESS", "PORT"} {
		t.Setenv(name, "")
	}
}

func TestContainerBootstrapGeneratesValidCredentialsAndPreservesState(t *testing.T) {
	containerTestEnvironment(t)
	t.Setenv("PORT", "9123")
	path := filepath.Join(t.TempDir(), "config.json")
	var output bytes.Buffer
	if err := BootstrapContainer(path, "../../config.example.json", &output); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Listen != "0.0.0.0:9123" || cfg.WebUI.Listen != cfg.Listen {
		t.Fatalf("listeners were not initialized for the public port: %s / %s", cfg.Listen, cfg.WebUI.Listen)
	}
	if !cfg.Anonymous || len(cfg.ZenKeys) != 0 || len(cfg.GoKeys) != 0 {
		t.Fatal("bootstrap should use the existing anonymous lane without placeholder upstream keys")
	}
	if len(cfg.ServerKeys) != 1 || !strings.HasPrefix(cfg.ServerKeys[0], "sk-oc2a-") {
		t.Fatal("a unique local API key was not created")
	}
	credentials, err := os.ReadFile(filepath.Join(filepath.Dir(path), "initial-credentials.txt"))
	if err != nil {
		t.Fatal(err)
	}
	password := strings.Split(strings.Split(string(credentials), "Initial management password: ")[1], "\n")[0]
	if !VerifyPassword(cfg.WebUI.PasswordHash, password) || cfg.WebUI.Password != "" {
		t.Fatal("the generated password must authenticate against the stored hash")
	}
	before, _ := os.ReadFile(path)
	if bytes.Contains(before, []byte(password)) || bytes.Contains(before, []byte("change-this")) || bytes.Contains(before, []byte("sk-your-zen-key")) {
		t.Fatal("configuration retained a plaintext admin password or placeholder credentials")
	}
	for _, file := range []string{path, filepath.Join(filepath.Dir(path), "initial-credentials.txt")} {
		info, err := os.Stat(file)
		if err != nil || info.Mode().Perm() != 0600 {
			t.Fatalf("credentials must be private: %s", file)
		}
	}
	t.Setenv("ADMIN_PASSWORD", "replacement-password")
	t.Setenv("SERVER_API_KEY", "replacement-api-key")
	if err := BootstrapContainer(path, "missing-seed.json", io.Discard); err != nil {
		t.Fatal(err)
	}
	after, _ := os.ReadFile(path)
	if !bytes.Equal(before, after) {
		t.Fatal("restarts must never replace existing credentials or configuration")
	}
}

func TestContainerBootstrapAcceptsZeaburPasswordAndUpstreamKeys(t *testing.T) {
	containerTestEnvironment(t)
	t.Setenv("ZEABUR", "1")
	t.Setenv("PASSWORD", "zeabur-service-password")
	t.Setenv("ZEN_API_KEYS", "first-key, second-key\nthird-key")
	path := filepath.Join(t.TempDir(), "config.json")
	var output bytes.Buffer
	if err := BootstrapContainer(path, "../../config.example.json", &output); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil || !VerifyPassword(cfg.WebUI.PasswordHash, "zeabur-service-password") {
		t.Fatal("Zeabur's service password must initialize the management account")
	}
	if len(cfg.ZenKeys) != 3 || cfg.Anonymous {
		t.Fatal("explicit upstream credentials should retain the keyed lane")
	}
	if strings.Contains(output.String(), "zeabur-service-password") {
		t.Fatal("provided passwords must not be printed")
	}
}

func TestContainerBootstrapRejectsInvalidPasswordWithoutSaving(t *testing.T) {
	containerTestEnvironment(t)
	t.Setenv("ADMIN_PASSWORD", "short")
	path := filepath.Join(t.TempDir(), "config.json")
	if err := BootstrapContainer(path, "../../config.example.json", io.Discard); err == nil {
		t.Fatal("short passwords must be rejected")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("a rejected bootstrap must not create the active configuration")
	}
}
