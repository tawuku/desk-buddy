#!/bin/bash
# Point this Mac at the JARVIS brain server (the Windows PC):
#   bash computer/server/connect.sh <pc-ip> <token>     # token printed by setup-windows.ps1
#   bash computer/server/connect.sh services llm         # only the language model runs on the PC;
#                                                        # speech recognition + voice stay on the Mac
#                                                        # (best for a slow PC). "services all" = everything
#   bash computer/server/connect.sh off                  # go back to running everything locally
#   bash computer/server/connect.sh status
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CFG="$ROOT/config/remote.json"
restart() { launchctl kickstart -k "gui/$(id -u)/com.jarvis.voice-wake" 2>/dev/null || true; }
# A Mac set up as a thin client (./install.sh --remote) has no local models:
# refuse to move a part back here that it can't run, rather than break JARVIS.
has_local() {  # llm | stt | tts
  case "$1" in
    llm) ls "$ROOT"/models/*.gguf >/dev/null 2>&1 ;;
    stt) [ -f "$ROOT/models/ggml-base.en.bin" ] ;;
    tts) ls "$ROOT"/models/piper/*.onnx >/dev/null 2>&1 ;;
  esac
}
need_local() {  # parts that would run on this Mac
  for part in "$@"; do
    has_local "$part" || { echo "This Mac has no local files for '$part' -- run ./install.sh --local first to download them." >&2; exit 1; }
  done
}

case "${1:-}" in
  off)
    need_local llm stt tts
    rm -f "$CFG"; restart; echo "Back to running everything on this Mac." ;;
  services)
    [ -f "$CFG" ] || { echo "Not connected yet -- run: connect.sh <pc-ip> <token>" >&2; exit 1; }
    WANT="$(printf '%s' "${2:-}" | tr 'A-Z' 'a-z')"
    if [ -n "$WANT" ] && [ "$WANT" != "all" ]; then
      for part in llm stt tts; do
        case ",$WANT," in *",$part,"*) ;; *) need_local "$part" ;; esac
      done
    fi
    python3 - "$CFG" "${2:?say which: llm | stt | tts (comma-separated), or all}" <<'PY'
import json, sys
path, want = sys.argv[1], sys.argv[2].lower()
c = json.load(open(path))
if want == "all":
    c.pop("services", None)
else:
    picked = [s for s in want.replace(" ", "").split(",") if s in ("llm", "stt", "tts")]
    if not picked:
        sys.exit("services must be llm, stt, tts (comma-separated) or all")
    c["services"] = picked
json.dump(c, open(path, "w"))
print("On the PC:", ", ".join(c.get("services", ["llm", "stt", "tts"])), "| everything else stays on this Mac.")
PY
    chmod 600 "$CFG"; restart ;;
  status)
    [ -f "$CFG" ] || { echo "local mode (no remote brain configured)"; exit 0; }
    python3 - "$CFG" <<'PY'
import json, sys, urllib.request
c = json.load(open(sys.argv[1]))
req = urllib.request.Request(f"http://{c['host']}:{c.get('port', 8090)}/health", headers={"Authorization": f"Bearer {c['token']}"})
try:
    print(f"remote brain {c['host']}:", urllib.request.urlopen(req, timeout=4).read().decode(),
          "| used for:", ", ".join(c.get("services", ["llm", "stt", "tts"])))
except Exception as exc:
    print(f"remote brain {c['host']} NOT reachable: {exc}")
PY
    ;;
  "")
    sed -n 2,6p "$0"; exit 1 ;;
  *)
    HOST="$1"; TOKEN="${2:?need the token printed by setup-windows.ps1}"
    # Reconnecting keeps the services choice (connect.sh services ...).
    KEEP="$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])).get("services")))' "$CFG" 2>/dev/null || echo null)"
    if [ "$KEEP" != "null" ] && [ -n "$KEEP" ]; then
      printf '{"host": "%s", "port": 8090, "token": "%s", "services": %s}\n' "$HOST" "$TOKEN" "$KEEP" > "$CFG"
    else
      printf '{"host": "%s", "port": 8090, "token": "%s"}\n' "$HOST" "$TOKEN" > "$CFG"
    fi
    chmod 600 "$CFG"
    if curl -sf -m 5 -H "Authorization: Bearer $TOKEN" "http://$HOST:8090/health" >/dev/null; then
      echo "Connected to the brain at $HOST. Restarting JARVIS to use it..."; restart
    else
      rm -f "$CFG"; echo "Couldn't reach http://$HOST:8090 -- is the PC on, on the same Wi-Fi, and setup finished?" >&2; exit 1
    fi ;;
esac
