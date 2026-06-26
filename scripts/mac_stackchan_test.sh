#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIRMWARE_DIR="$ROOT_DIR/firmware"
ENV_NAME="m5stack-cores3"
PORT="${STACKCHAN_PORT:-}"
DO_BUILD=1
DO_UPLOAD=0
DO_UPLOADFS=0
DO_MONITOR=0
MONITOR_ONLY=0

usage() {
  cat <<'EOF'
Usage: scripts/mac_stackchan_test.sh [options]

Mac + USB-connected M5Stack CoreS3 SE test helper.

Options:
  --build-only       Build firmware only (default)
  --upload          Upload firmware
  --uploadfs        Upload LittleFS face assets
  --flash-all       Build, upload LittleFS, upload firmware, then monitor
  --monitor         Open serial monitor after requested actions
  --monitor-only    Detect port and open serial monitor only
  --port PATH       Serial port, e.g. /dev/cu.usbmodemXXXX
  -h, --help        Show this help

Environment:
  STACKCHAN_PORT=/dev/cu.usbmodemXXXX  Override serial port detection
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

detect_port() {
  if [[ -n "$PORT" ]]; then
    [[ -e "$PORT" ]] || die "serial port does not exist: $PORT"
    return
  fi

  local candidates=()
  while IFS= read -r path; do
    candidates+=("$path")
  done < <(
    {
      ls /dev/cu.usbmodem* 2>/dev/null || true
      ls /dev/cu.usbserial* 2>/dev/null || true
      ls /dev/cu.SLAB_USBtoUART* 2>/dev/null || true
      ls /dev/cu.wchusbserial* 2>/dev/null || true
    } | sort -u
  )

  if [[ ${#candidates[@]} -eq 0 ]]; then
    echo "No USB serial device found." >&2
    echo "Reconnect CoreS3 with a data-capable USB-C cable, then try again." >&2
    echo "If it still does not appear, hold the CoreS3 boot button and tap reset." >&2
    echo >&2
    platformio device list || true
    exit 2
  fi

  if [[ ${#candidates[@]} -gt 1 ]]; then
    echo "Multiple USB serial devices found:" >&2
    printf '  %s\n' "${candidates[@]}" >&2
    die "pass --port PATH or set STACKCHAN_PORT"
  fi

  PORT="${candidates[0]}"
}

pio_run() {
  (cd "$FIRMWARE_DIR" && platformio run -e "$ENV_NAME" "$@")
}

pio_monitor() {
  (cd "$FIRMWARE_DIR" && platformio device monitor -e "$ENV_NAME" --port "$PORT")
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-only)
      DO_BUILD=1
      DO_UPLOAD=0
      DO_UPLOADFS=0
      DO_MONITOR=0
      MONITOR_ONLY=0
      ;;
    --upload)
      DO_BUILD=1
      DO_UPLOAD=1
      ;;
    --uploadfs)
      DO_BUILD=1
      DO_UPLOADFS=1
      ;;
    --flash-all)
      DO_BUILD=1
      DO_UPLOAD=1
      DO_UPLOADFS=1
      DO_MONITOR=1
      ;;
    --monitor)
      DO_MONITOR=1
      ;;
    --monitor-only)
      DO_BUILD=0
      DO_UPLOAD=0
      DO_UPLOADFS=0
      DO_MONITOR=1
      MONITOR_ONLY=1
      ;;
    --port)
      shift
      [[ $# -gt 0 ]] || die "--port requires a path"
      PORT="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown option: $1"
      ;;
  esac
  shift
done

command -v platformio >/dev/null 2>&1 || die "platformio is not installed or not in PATH"
[[ -f "$FIRMWARE_DIR/include/config.h" ]] || die "missing firmware/include/config.h"

if [[ "$DO_UPLOAD" -eq 1 || "$DO_UPLOADFS" -eq 1 || "$DO_MONITOR" -eq 1 ]]; then
  detect_port
  echo "Using serial port: $PORT"
fi

if [[ "$DO_BUILD" -eq 1 ]]; then
  pio_run
fi

if [[ "$DO_UPLOADFS" -eq 1 ]]; then
  pio_run -t uploadfs --upload-port "$PORT"
fi

if [[ "$DO_UPLOAD" -eq 1 ]]; then
  pio_run -t upload --upload-port "$PORT"
fi

if [[ "$DO_MONITOR" -eq 1 ]]; then
  if [[ "$MONITOR_ONLY" -eq 0 ]]; then
    echo "Opening serial monitor. Press Ctrl-C to exit."
  fi
  pio_monitor
fi
