package config

import (
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// BootstrapContainer initializes a missing state file. Existing operator data
// always wins over environment variables, including password and upstream keys.
func BootstrapContainer(path, seedPath string, output io.Writer) error {
	if _, err := os.Stat(path); err == nil {
		return nil
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("inspect container configuration: %w", err)
	}
	cfg, err := Load(seedPath)
	if err != nil {
		return fmt.Errorf("load container seed: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	cfg.Listen = containerAddress()
	cfg.WebUI.Listen = cfg.Listen
	if value := os.Getenv("WEBUI_LISTEN_ADDRESS"); value != "" {
		cfg.WebUI.Listen = value
	}
	if value := os.Getenv("SERVER_API_KEY"); value != "" {
		cfg.ServerKeys = []string{value}
	} else if len(cfg.ServerKeys) == 1 && cfg.ServerKeys[0] == "change-this-local-key" {
		key, err := containerToken(32)
		if err != nil {
			return err
		}
		cfg.ServerKeys = []string{"sk-oc2a-" + key}
	}
	cfg.ZenKeys = removeSeedKey(cfg.ZenKeys)
	cfg.GoKeys = removeSeedKey(cfg.GoKeys)
	if keys, set := containerKeys("ZEN_API_KEYS", "ZEN_API_KEY"); set {
		cfg.ZenKeys = keys
	}
	if keys, set := containerKeys("GO_API_KEYS", "GO_API_KEY"); set {
		cfg.GoKeys = keys
	}
	if len(cfg.ZenKeys)+len(cfg.GoKeys) == 0 {
		cfg.Anonymous = true
	}
	if value, set := os.LookupEnv("ANONYMOUS"); set && value != "" {
		cfg.Anonymous, err = strconv.ParseBool(value)
		if err != nil {
			return errors.New("ANONYMOUS must be true or false")
		}
	}
	if value := os.Getenv("ADMIN_USERNAME"); value != "" {
		cfg.WebUI.Username = value
	}
	password := os.Getenv("ADMIN_PASSWORD")
	generatedPassword := false
	if password == "" && cfg.WebUI.Password != "change-this-admin-password" {
		password = cfg.WebUI.Password
	}
	if password == "" && cfg.WebUI.PasswordHash == "" {
		if os.Getenv("ZEABUR") != "" || os.Getenv("ZEABUR_SERVICE_ID") != "" {
			password = os.Getenv("PASSWORD")
		}
		if password == "" {
			password, err = containerToken(24)
			if err != nil {
				return err
			}
			generatedPassword = true
		}
	}
	if password != "" {
		cfg.WebUI.PasswordHash, err = HashPassword(password)
		if err != nil {
			return fmt.Errorf("initialize admin password: %w", err)
		}
	}
	cfg.WebUI.Password = ""
	cfg, err = Normalize(path, cfg)
	if err != nil {
		return err
	}
	credentials := fmt.Sprintf("Management username: %s\n", cfg.WebUI.Username)
	if generatedPassword {
		credentials += "Initial management password: " + password + "\n"
	}
	credentials += "Local API key: " + cfg.FirstEnabledServerKey() + "\n"
	credentialsPath := filepath.Join(filepath.Dir(path), "initial-credentials.txt")
	if err := os.WriteFile(credentialsPath, []byte(credentials), 0600); err != nil {
		return fmt.Errorf("save initial credentials: %w", err)
	}
	if err := SaveAtomic(path, cfg); err != nil {
		return err
	}
	fmt.Fprintf(output, "Initialized %s; management username: %s\n", path, cfg.WebUI.Username)
	if generatedPassword {
		fmt.Fprintf(output, "Initial management password: %s\n", password)
	} else {
		fmt.Fprintln(output, "Management password initialized from environment or seed configuration.")
	}
	fmt.Fprintf(output, "Local API key is available in the management UI or %s\n", credentialsPath)
	return nil
}

func containerAddress() string {
	if address := os.Getenv("LISTEN_ADDRESS"); address != "" {
		return address
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	return "0.0.0.0:" + port
}

func containerToken(size int) (string, error) {
	value := make([]byte, size)
	if _, err := rand.Read(value); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(value), nil
}

func containerKeys(plural, singular string) ([]string, bool) {
	value, set := os.LookupEnv(plural)
	if !set {
		value, set = os.LookupEnv(singular)
	}
	if !set {
		return nil, false
	}
	return strings.FieldsFunc(value, func(r rune) bool { return r == ',' || r == '\n' || r == '\r' || r == ' ' || r == '\t' }), true
}

func removeSeedKey(keys []string) []string {
	result := make([]string, 0, len(keys))
	for _, key := range keys {
		if key != "sk-your-zen-key" {
			result = append(result, key)
		}
	}
	return result
}
