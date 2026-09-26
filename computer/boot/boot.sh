#!/bin/bash
# Boot JARVIS + the pet (or just the pet, on --pet-only installs) with the
# intro -- run by bootkey when Space is held for 3s
# (or by hand: `bash boot.sh`, or `bash boot.sh --intro` to replay the intro
# even though JARVIS is already on).
#
# Starts the desktop pet and JARVIS the same way PA does (launchctl enable +
# bootstrap + kickstart), and has the pet play the boot intro on every
# display; the intro follows the real startup and greets you once the voice
# app, speech recognition and the local model are all up.
set -u
UID_NUM=$(id -u)
AGENTS="$HOME/Library/LaunchAgents"
LOCK="${TMPDIR:-/tmp}/jarvis-boot.lock"

# Already running: just replay the intro.
if curl -s -m 1 -X POST http://127.0.0.1:8092/boot >/dev/null 2>&1 && \
   { [ "$(cat "$(dirname "$0")/../../config/mode" 2>/dev/null)" = "pet" ] || launchctl list com.jarvis.voice-wake >/dev/null 2>&1; }; then
  echo "[boot] already on -- replayed the intro"
  exit 0
fi
mkdir "$LOCK" 2>/dev/null || { echo "[boot] already booting"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

afplay /System/Library/Sounds/Hero.aiff >/dev/null 2>&1 &

start() {
  launchctl enable "gui/$UID_NUM/$1" 2>/dev/null
  launchctl list "$1" >/dev/null 2>&1 || launchctl bootstrap "gui/$UID_NUM" "$AGENTS/$1.plist" 2>/dev/null
  launchctl kickstart "gui/$UID_NUM/$1" 2>/dev/null
}

echo "[boot] starting the pet (HUD + screens)"
start com.jarvis.pet
for _ in $(seq 1 60); do
  curl -s -m 1 -X POST http://127.0.0.1:8092/boot >/dev/null 2>&1 && { echo "[boot] intro playing"; break; }
  sleep 0.25
done

# Pet-only installs have no JARVIS to start.
if [ "$(cat "$(dirname "$0")/../../config/mode" 2>/dev/null)" != "pet" ]; then
  echo "[boot] starting JARVIS"
  start com.jarvis.voice-wake
fi
