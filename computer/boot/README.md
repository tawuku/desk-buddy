# Boot key -- hold Space for 3 seconds

After you turn the Mac on and log in, JARVIS is off (nothing heavy starts at
login). **Hold Space for 3 seconds** and JARVIS boots: a chime, then a
full-screen intro on every display (`computer/screen/boot.html`) -- particles
assembling into the orb, HUD rings, a boot log and a live systems list that
follows the real startup -- and once the voice app, speech recognition and
the local model are all up, "Good morning, <your name>. JARVIS is online." in the
Piper voice. The intro fades out and JARVIS is listening.

- `bootkey` (`bootkey.swift`, built with `swiftc -O -o bootkey bootkey.swift`)
  -- a tiny listen-only keyboard monitor, the `com.jarvis.boot` LaunchAgent
  (starts at login, ~5 MB, no mic, no network). It never blocks or changes
  a key press; any other key while Space is down cancels (so typing never
  triggers it). Log: `logs/boot.log`.
- `boot.sh` -- starts the pet and JARVIS exactly like PA does and asks the
  pet to play the intro (`POST 127.0.0.1:8092/boot`). Does nothing if JARVIS
  is already on; `bash boot.sh --intro` replays the intro anyway (so does the
  pet tray's *Play boot intro*).

**Permission:** macOS requires Input Monitoring for any key listener. Allow
**bootkey** in System Settings -> Privacy & Security -> Input Monitoring
(it asks once; until then it waits and retries every 10 s).

Turn the boot key off: `launchctl bootout gui/$(id -u)/com.jarvis.boot`
(on again: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jarvis.boot.plist`).
