#!/bin/bash
# desk-buddy installer (macOS)
#
#   ./install.sh             JARVIS + Pet: the local voice assistant ("wake up Jarvis"),
#                            its full-screen briefing, and the desktop pet
#   ./install.sh --pet-only  Just the desktop pet: goals, reminders, pop-ups
#
# Safe to run again (it updates what's there). Undo with ./uninstall.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE="full"
[ "${1:-}" = "--pet-only" ] && MODE="pet"
AGENTS="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

say()  { printf "\n\033[1;34m==>\033[0m %s\n" "$*"; }
ok()   { printf "    \033[32m✓\033[0m %s\n" "$*"; }
die()  { printf "\n\033[1;31mError:\033[0m %s\n" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

[ "$(uname)" = "Darwin" ] || die "desk-buddy runs on macOS."
mkdir -p "$ROOT/logs" "$ROOT/database/pet" "$AGENTS"

load_agent() {  # label
  launchctl bootout "gui/$UID_NUM/$1" 2>/dev/null || true
  launchctl enable "gui/$UID_NUM/$1" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$AGENTS/$1.plist"
}

write_agent() {  # label, run_at_load (true|false), keep_alive (true|false), workdir, program args...
  local label="$1" atload="$2" alive="$3" workdir="$4"; shift 4
  local args=""
  for a in "$@"; do args="$args		<string>$a</string>
"; done
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$label</string>
	<key>ProgramArguments</key>
	<array>
$args	</array>
	<key>WorkingDirectory</key>
	<string>$workdir</string>
	<key>RunAtLoad</key>
	<$atload/>
	<key>KeepAlive</key>
	<$alive/>
	<key>ProcessType</key>
	<string>Interactive</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
	</dict>
	<key>StandardOutPath</key>
	<string>$ROOT/logs/${label#com.jarvis.}.log</string>
	<key>StandardErrorPath</key>
	<string>$ROOT/logs/${label#com.jarvis.}.log</string>
</dict>
</plist>
PLIST
}

download() {  # url, destination
  if [ -s "$2" ]; then ok "$(basename "$2") (already downloaded)"; return; fi
  echo "    downloading $(basename "$2")..."
  curl -fL --progress-bar -o "$2.part" "$1" && mv "$2.part" "$2"
  ok "$(basename "$2")"
}

# --- 1. the desktop pet (both modes) ------------------------------------------
say "Desktop pet"
have node || die "Node.js is needed for the pet. Install it from https://nodejs.org (or: brew install node), then run this again."
(cd "$ROOT/computer/pet" && npm install --no-audit --no-fund --loglevel=error)
ok "Electron and the pet's packages"
ELECTRON="$ROOT/computer/pet/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"
[ -x "$ELECTRON" ] || die "Electron didn't install -- see the npm output above."
echo "$MODE" > "$ROOT/config/mode"
# Both modes: the pet starts with your Mac (with JARVIS, so does JARVIS --
# it listens for "wake up Jarvis" from login; the first wake after a restart
# plays the boot intro). Switch either off in PA.
PET_ATLOAD=true
write_agent com.jarvis.pet "$PET_ATLOAD" false "$ROOT/computer/pet" "$ELECTRON" "$ROOT/computer/pet"
ok "LaunchAgent com.jarvis.pet"

install_bootkey() {
  if have swiftc && swiftc -O -o "$ROOT/computer/boot/bootkey" "$ROOT/computer/boot/bootkey.swift" 2>/dev/null; then
    write_agent com.jarvis.boot true true "$ROOT/computer/boot" "$ROOT/computer/boot/bootkey" "$ROOT/computer/boot/boot.sh"
    load_agent com.jarvis.boot
    ok "Boot key: hold Space for 3 s to wake everything with the intro (allow 'bootkey' in System Settings > Privacy > Input Monitoring)"
  else
    echo "    (no Swift compiler -- skipping the Space-bar boot key; install Xcode Command Line Tools to get it)"
  fi
}

if [ "$MODE" = "pet" ]; then
  load_agent com.jarvis.pet
  launchctl kickstart "gui/$UID_NUM/com.jarvis.pet" 2>/dev/null || true
  install_bootkey
  say "Done! Your pet is on screen."
  cat <<'EOF'
    • Click the 🐾 in the menu bar -> "Goals & reminders…" to add goals, people
      to stay in touch with, reminder settings -- and pick your pet.
    • The pet starts with your Mac -- or hold Space for 3 s for its wake-up intro.
      Remove everything with ./uninstall.sh.
    • Want the voice assistant too? Run ./install.sh (without --pet-only).
EOF
  exit 0
fi

# --- 2. JARVIS: engines, Python app, models -----------------------------------
say "Speech + language engines (Homebrew)"
have brew || die "Homebrew is needed for JARVIS: https://brew.sh -- then run this again. (Or use ./install.sh --pet-only.)"
if [ -x "$ROOT/engines/bin/llama-server" ] || have llama-server; then ok "llama.cpp"; else brew install llama.cpp; fi
if [ -x "$ROOT/engines/bin/whisper-server" ] || have whisper-server; then ok "whisper.cpp"; else brew install whisper-cpp; fi

say "JARVIS voice app (Python)"
PY=""
for c in python3.12 python3.13 python3.11 python3; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  have uv || die "Python 3.10-3.13 is needed. Easiest: install uv (https://docs.astral.sh/uv/) and run this again."
  uv python install 3.12 >/dev/null
  PY="$(uv python find 3.12)"
fi
VENV="$ROOT/computer/voice/venv"
[ -x "$VENV/bin/python3" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python3" -m pip install --quiet --upgrade pip
"$VENV/bin/python3" -m pip install --quiet -r "$ROOT/computer/voice/requirements.txt"
"$VENV/bin/python3" -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])" >/dev/null 2>&1 || true
ok "Python packages + 'hey Jarvis' wake-word model"

say "Models (~1.3 GB, one time)"
mkdir -p "$ROOT/models/piper"
download "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf" "$ROOT/models/Qwen3-1.7B-Q4_K_M.gguf"
download "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin" "$ROOT/models/ggml-base.en.bin"
download "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx" "$ROOT/models/piper/en_GB-alan-medium.onnx"
download "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json" "$ROOT/models/piper/en_GB-alan-medium.onnx.json"
N=en_GB-northern_english_male-medium
download "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium/$N.onnx" "$ROOT/models/piper/$N.onnx"
download "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium/$N.onnx.json" "$ROOT/models/piper/$N.onnx.json"

# --- 3. your config ---------------------------------------------------------------
say "Your settings"
ENVF="$ROOT/config/.env.local_model"
if [ ! -f "$ENVF" ]; then
  KEY="$(openssl rand -hex 24)"
  sed -e "s|^LLAMA_SERVER_API_KEY=.*|LLAMA_SERVER_API_KEY=$KEY|" "$ROOT/config/local_model.env.example" > "$ENVF"
  grep -q '^LLAMA_SERVER_API_KEY=' "$ENVF" || echo "LLAMA_SERVER_API_KEY=$KEY" >> "$ENVF"
  chmod 600 "$ENVF"
  ok "config/.env.local_model (random local API key)"
fi
SRC="$ROOT/config/jarvis_sources.json"
if [ ! -f "$SRC" ]; then
  cp "$ROOT/config/jarvis_sources.example.json" "$SRC"
  if [ -t 0 ]; then
    read -r -p "    Your first name (JARVIS greets you with it): " NAME || NAME=""
    read -r -p "    Your city, for the weather (e.g. Hamburg): " CITY || CITY=""
    "$VENV/bin/python3" - "$SRC" "$NAME" "$CITY" <<'PY'
import json, sys, urllib.parse, urllib.request
path, name, city = sys.argv[1], sys.argv[2].strip(), sys.argv[3].strip()
cfg = json.load(open(path))
cfg["user_name"] = name
if city:
    try:
        q = urllib.parse.urlencode({"name": city, "count": 1})
        r = json.load(urllib.request.urlopen(f"https://geocoding-api.open-meteo.com/v1/search?{q}", timeout=8))["results"][0]
        cfg["city"] = {"name": r["name"], "lat": r["latitude"], "lon": r["longitude"], "timezone": r.get("timezone", "auto")}
    except Exception:
        print(f"    (couldn't look up '{city}' -- edit config/jarvis_sources.json later)")
json.dump(cfg, open(path, "w"), indent=2, ensure_ascii=False)
PY
  fi
  ok "config/jarvis_sources.json -- edit it to add your projects and news feeds"
fi

# --- 4. services ----------------------------------------------------------------------
say "Services"
write_agent com.jarvis.voice-wake true false "$ROOT/computer/voice" "$VENV/bin/python3" "$ROOT/computer/voice/wake_listener.py"
load_agent com.jarvis.voice-wake
load_agent com.jarvis.pet
ok "JARVIS and the pet (start with your Mac; switch off in PA)"

install_bootkey

# PA: the control app (start/stop, voice, threads) in ~/Applications
PA="$HOME/Applications/PA.app"
mkdir -p "$PA/Contents/MacOS" "$PA/Contents/Resources"
cat > "$PA/Contents/MacOS/PA" <<EOF
#!/usr/bin/env bash
exec "$ELECTRON" "$ROOT/computer/pa"
EOF
chmod +x "$PA/Contents/MacOS/PA"
cp "$ROOT/computer/pa/PA.icns" "$PA/Contents/Resources/PA.icns"
cat > "$PA/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>PA</string>
  <key>CFBundleIdentifier</key><string>com.jarvis.pa</string>
  <key>CFBundleExecutable</key><string>PA</string>
  <key>CFBundleIconFile</key><string>PA</string>
  <key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
EOF
ok "PA control app in ~/Applications"

say "Done!"
cat <<'EOF'
    • JARVIS starts with your Mac and listens for "wake up Jarvis" (the first wake
      after a restart plays the boot intro). Stop/start it in PA
      (Spotlight: "PA"). First start loads the model (~30 s).
    • Then say "wake up Jarvis". Try: "give me an update", "remind me in 10 minutes
      to stretch", "remember that ...", "open my CV", "how did I sleep?"
    • macOS will ask once for the microphone, and later for Notes / Mail /
      Music when you first use those.
    • Your settings: config/jarvis_sources.json. Goals & your pet: 🐾 menu.
EOF
