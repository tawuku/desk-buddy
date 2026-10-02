#!/bin/bash
# Point this Mac at the JARVIS brain server (the Windows PC):
#   bash computer/server/connect.sh <pc-ip> <token>     # token printed by setup-windows.ps1
#   bash computer/server/connect.sh off                  # go back to running everything locally
#   bash computer/server/connect.sh status
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CFG="$ROOT/config/remote.json"
restart() { launchctl kickstart -k "gui/$(id -u)/com.jarvis.voice-wake" 2>/dev/null || true; }

case "${1:-}" in
  off)
    rm -f "$CFG"; restart; echo "Back to running everything on this Mac." ;;
  status)
    [ -f "$CFG" ] || { echo "local mode (no remote brain configured)"; exit 0; }
    python3 - "$CFG" <<'PY'
import json, sys, urllib.request
c = json.load(open(sys.argv[1]))
req = urllib.request.Request(f"http://{c['host']}:{c.get('port', 8090)}/health", headers={"Authorization": f"Bearer {c['token']}"})
try:
    print(f"remote brain {c['host']}:", urllib.request.urlopen(req, timeout=4).read().decode())
except Exception as exc:
    print(f"remote brain {c['host']} NOT reachable: {exc}")
PY
    ;;
  "")
    sed -n 2,6p "$0"; exit 1 ;;
  *)
    HOST="$1"; TOKEN="${2:?need the token printed by setup-windows.ps1}"
    printf '{"host": "%s", "port": 8090, "token": "%s"}\n' "$HOST" "$TOKEN" > "$CFG"
    chmod 600 "$CFG"
    if curl -sf -m 5 -H "Authorization: Bearer $TOKEN" "http://$HOST:8090/health" >/dev/null; then
      echo "Connected to the brain at $HOST. Restarting JARVIS to use it..."; restart
    else
      rm -f "$CFG"; echo "Couldn't reach http://$HOST:8090 -- is the PC on, on the same Wi-Fi, and setup finished?" >&2; exit 1
    fi ;;
esac
