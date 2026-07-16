#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VOICEVOX_SCRIPT="$ROOT_DIR/scripts/start_voicevox_engine.sh"
SERVER_SCRIPT="$ROOT_DIR/scripts/start_stackchan_server.sh"
FIRMWARE_CONFIG="$ROOT_DIR/firmware/include/config.h"
RUNTIME_DIR="/private/tmp/x68-stackchan-services-$(id -u)"
LOG_DIR="${HOME:?HOME is not set}/Library/Logs/X68-Stackchan"
MANAGER_PID_FILE="$RUNTIME_DIR/manager.pid"
VOICEVOX_PID_FILE="$RUNTIME_DIR/voicevox.pid"
SERVER_PID_FILE="$RUNTIME_DIR/server.pid"
VOICEVOX_LOG="$LOG_DIR/voicevox.log"
SERVER_LOG="$LOG_DIR/server.log"

usage() {
  cat <<'EOF'
Usage: scripts/mac_stackchan_services.sh COMMAND

Commands:
  start     Start both services in the foreground (Ctrl-C stops both)
  stop      Stop services from another terminal
  restart   Stop existing services, then start in the foreground
  status    Show process and HTTP readiness
  logs      Follow both service logs (Ctrl-C to stop viewing)
EOF
}

prepare_dirs() {
  /bin/mkdir -p "$RUNTIME_DIR" "$LOG_DIR"
}

read_pid() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(<"$pid_file")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$pid"
}

pid_is_running() {
  local pid_file="$1"
  local pid
  pid="$(read_pid "$pid_file")" || return 1
  /bin/kill -0 "$pid" 2>/dev/null
}

http_ready() {
  /usr/bin/curl -fsS --max-time 3 "$1" >/dev/null 2>&1
}

expected_server_host() {
  [[ -f "$FIRMWARE_CONFIG" ]] || return 1
  /usr/bin/sed -n \
    's/.*SERVER_HOST[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
    "$FIRMWARE_CONFIG" | /usr/bin/head -n 1
}

check_demo_network() {
  local expected_host
  expected_host="$(expected_server_host)" || {
    echo "Cannot read SERVER_HOST from $FIRMWARE_CONFIG" >&2
    return 1
  }
  if [[ -z "$expected_host" ]]; then
    echo "SERVER_HOST is empty in $FIRMWARE_CONFIG" >&2
    return 1
  fi
  if ! /sbin/ifconfig | /usr/bin/grep -q "inet $expected_host "; then
    echo "Mac is not using the firmware's SERVER_HOST address: $expected_host" >&2
    echo "Connect the Mac to the demo Wi-Fi / iPhone hotspot, then run start again." >&2
    return 1
  fi
  echo "Demo network: ready (Mac $expected_host)"
}

ensure_ollama() {
  if http_ready http://127.0.0.1:11434/api/tags; then
    echo "Ollama: already ready"
    return
  fi
  if ! /usr/bin/open -Ra Ollama; then
    echo "Ollama.app is not installed and its API is not running" >&2
    return 1
  fi
  echo "Ollama: launching app"
  /usr/bin/open -g -j -a Ollama
  wait_for_http "Ollama" http://127.0.0.1:11434/api/tags 60
}

wait_for_http() {
  local name="$1"
  local url="$2"
  local timeout_seconds="$3"
  local deadline=$((SECONDS + timeout_seconds))

  until http_ready "$url"; do
    if (( SECONDS >= deadline )); then
      echo "$name did not become ready within ${timeout_seconds}s" >&2
      return 1
    fi
    /bin/sleep 1
  done
  echo "$name: ready"
}

stop_pid() {
  local name="$1"
  local pid_file="$2"
  local pid

  if ! pid="$(read_pid "$pid_file")"; then
    return
  fi
  if ! /bin/kill -0 "$pid" 2>/dev/null; then
    /bin/rm -f "$pid_file"
    return
  fi

  /bin/kill "$pid" 2>/dev/null || true
  for _ in {1..40}; do
    if ! /bin/kill -0 "$pid" 2>/dev/null; then
      /bin/rm -f "$pid_file"
      echo "$name: stopped"
      return
    fi
    /bin/sleep 0.25
  done
  echo "$name: did not stop; pid remains $pid" >&2
}

cleanup_children() {
  trap - EXIT INT TERM
  stop_pid "Stackchan server" "$SERVER_PID_FILE" || true
  stop_pid "VOICEVOX" "$VOICEVOX_PID_FILE" || true
  /bin/rm -f "$MANAGER_PID_FILE"
}

start_services() {
  prepare_dirs
  [[ -x "$VOICEVOX_SCRIPT" ]] || {
    echo "Not executable: $VOICEVOX_SCRIPT" >&2
    exit 1
  }
  [[ -x "$SERVER_SCRIPT" ]] || {
    echo "Not executable: $SERVER_SCRIPT" >&2
    exit 1
  }
  if pid_is_running "$MANAGER_PID_FILE"; then
    echo "Stackchan services are already managed by pid $(read_pid "$MANAGER_PID_FILE")" >&2
    exit 1
  fi
  if http_ready http://127.0.0.1:50021/version ||
     http_ready http://127.0.0.1:8000/ready; then
    echo "A service is already using port 50021 or 8000; stop it first" >&2
    exit 1
  fi

  printf '%s\n' "$$" >"$MANAGER_PID_FILE"
  trap cleanup_children EXIT INT TERM

  check_demo_network
  ensure_ollama

  "$VOICEVOX_SCRIPT" >>"$VOICEVOX_LOG" 2>&1 &
  printf '%s\n' "$!" >"$VOICEVOX_PID_FILE"
  echo "VOICEVOX: starting (pid $!)"
  wait_for_http "VOICEVOX" http://127.0.0.1:50021/version 120

  "$SERVER_SCRIPT" >>"$SERVER_LOG" 2>&1 &
  printf '%s\n' "$!" >"$SERVER_PID_FILE"
  echo "Stackchan server: starting (pid $!)"
  wait_for_http "Stackchan server" http://127.0.0.1:8000/ready 120

  echo "Stackchan services are running. Press Ctrl-C to stop both."
  while pid_is_running "$VOICEVOX_PID_FILE" && pid_is_running "$SERVER_PID_FILE"; do
    /bin/sleep 2
  done
  echo "A managed service exited unexpectedly" >&2
  return 1
}

stop_services() {
  local manager_pid
  if manager_pid="$(read_pid "$MANAGER_PID_FILE")" &&
     /bin/kill -0 "$manager_pid" 2>/dev/null; then
    /bin/kill "$manager_pid"
    echo "Stop requested from manager pid $manager_pid"
    return
  fi

  stop_pid "Stackchan server" "$SERVER_PID_FILE"
  stop_pid "VOICEVOX" "$VOICEVOX_PID_FILE"
  /bin/rm -f "$MANAGER_PID_FILE"
}

show_process_status() {
  local name="$1"
  local pid_file="$2"
  if pid_is_running "$pid_file"; then
    echo "$name process: running (pid $(read_pid "$pid_file"))"
  else
    echo "$name process: not running"
  fi
}

status_services() {
  show_process_status "Manager" "$MANAGER_PID_FILE"
  show_process_status "VOICEVOX" "$VOICEVOX_PID_FILE"
  show_process_status "Stackchan server" "$SERVER_PID_FILE"

  if http_ready http://127.0.0.1:50021/version; then
    echo "VOICEVOX HTTP: ready"
  else
    echo "VOICEVOX HTTP: not ready"
  fi
  if http_ready http://127.0.0.1:11434/api/tags; then
    echo "Ollama HTTP: ready"
  else
    echo "Ollama HTTP: not ready"
  fi
  if http_ready http://127.0.0.1:8000/ready; then
    echo "Stackchan HTTP: ready"
  else
    echo "Stackchan HTTP: not ready"
  fi
}

follow_logs() {
  prepare_dirs
  /usr/bin/touch "$VOICEVOX_LOG" "$SERVER_LOG"
  /usr/bin/tail -n 50 -F "$VOICEVOX_LOG" "$SERVER_LOG"
}

command="${1:-}"
case "$command" in
  start)   start_services ;;
  stop)    stop_services ;;
  restart) stop_services; start_services ;;
  status)  status_services ;;
  logs)    follow_logs ;;
  -h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
