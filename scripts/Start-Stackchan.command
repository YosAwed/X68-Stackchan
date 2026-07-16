#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Starting X68-Stackchan demo services..."
echo "Press Ctrl-C in this window to stop VOICEVOX and the Stackchan server."
echo

exec "$ROOT_DIR/scripts/mac_stackchan_services.sh" start
