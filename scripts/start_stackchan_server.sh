#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$ROOT_DIR/server"
UVICORN="$SERVER_DIR/.venv/bin/uvicorn"
VOICEVOX_READY_URL="${VOICEVOX_READY_URL:-http://127.0.0.1:50021/version}"
VOICEVOX_WAIT_SECONDS="${VOICEVOX_WAIT_SECONDS:-120}"

if [[ ! -x "$UVICORN" ]]; then
  echo "Stackchan server executable not found: $UVICORN" >&2
  exit 1
fi

if [[ ! -f "$SERVER_DIR/.env" ]]; then
  echo "Stackchan server config not found: $SERVER_DIR/.env" >&2
  exit 1
fi

echo "Waiting for VOICEVOX Engine: $VOICEVOX_READY_URL"
deadline=$((SECONDS + VOICEVOX_WAIT_SECONDS))
until /usr/bin/curl -fsS --max-time 2 "$VOICEVOX_READY_URL" >/dev/null; do
  if (( SECONDS >= deadline )); then
    echo "VOICEVOX Engine did not become ready within ${VOICEVOX_WAIT_SECONDS}s" >&2
    exit 1
  fi
  /bin/sleep 2
done

echo "VOICEVOX Engine is ready; starting Stackchan server"
cd "$SERVER_DIR"
exec "$UVICORN" main:app --host 0.0.0.0 --port 8000
