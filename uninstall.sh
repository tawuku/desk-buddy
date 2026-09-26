#!/bin/bash
# desk-buddy uninstaller: stops and removes the pet / JARVIS services and the
# PA app. Your files in this folder (settings, goals, models) are kept --
# delete the folder too to remove everything.
set -uo pipefail
UID_NUM="$(id -u)"
for label in com.jarvis.boot com.jarvis.voice-wake com.jarvis.pet; do
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null && echo "stopped $label"
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
done
rm -rf "$HOME/Applications/PA.app"
echo "Done. (Settings, goals and downloaded models are still in this folder.)"
