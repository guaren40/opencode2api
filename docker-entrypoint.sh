#!/bin/sh
set -eu
umask 077

if [ "$(apk --print-arch)" != aarch64 ] || [ "$(uname -m)" != aarch64 ]; then
    echo "opencode2api requires an ARM64 image and ARM64 runtime." >&2
    exit 1
fi

state_dir=${STATE_DIR:-/var/lib/opencode2api}
config_path=${CONFIG_PATH:-$state_dir/config.json}
config_seed_path=${CONFIG_SEED_PATH:-/app/config.example.json}
listen_address=${LISTEN_ADDRESS:-0.0.0.0:${PORT:-8080}}
webui_listen_address=${WEBUI_LISTEN_ADDRESS:-$listen_address}

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$state_dir" "$(dirname "$config_path")"
    chown opencode2api:opencode2api "$state_dir" "$(dirname "$config_path")"
    exec su-exec opencode2api:opencode2api "$0" "$@"
fi

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

/usr/local/bin/opencode2api -init-container-config -config "$config_path" -config-seed "$config_seed_path"
exec /usr/local/bin/opencode2api -config "$config_path" -listen "$listen_address" -web-listen "$webui_listen_address"
