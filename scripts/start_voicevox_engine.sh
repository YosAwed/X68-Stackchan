#!/usr/bin/env bash
set -euo pipefail

ENGINE_DIR="${VOICEVOX_ENGINE_DIR:-/Users/awed/Applications/VOICEVOX_ENGINE/macos-arm64}"
HOST="${VOICEVOX_HOST_ADDR:-127.0.0.1}"
PORT="${VOICEVOX_PORT:-50021}"

if [[ ! -x "$ENGINE_DIR/run" ]]; then
  echo "VOICEVOX ENGINE executable not found: $ENGINE_DIR/run" >&2
  exit 1
fi

cd "$ENGINE_DIR"
exec ./run --host "$HOST" --port "$PORT"
