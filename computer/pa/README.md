# PA -- the JARVIS control app

Nothing in JARVIS auto-starts at login anymore (see Build 008 in the root
`DEVELOPMENT_LOG.md`). PA is how you turn it on, turn it off, and change
settings -- a small Electron app with a window, not a background service
itself.

## Opening it

It's installed at `~/Applications/PA.app` -- Spotlight-searchable ("PA"),
double-clickable, no Terminal needed. Closing its window doesn't stop
anything it started; JARVIS keeps running until you tell PA to stop it.

## What it controls

- **JARVIS** (one switch): the Gateway, model server, TTS server, and
  voice wake together -- everything except the desktop pet.
- **Desktop pet (HUD)** (its own switch): separate from JARVIS on purpose,
  same as the original request -- you might want the model running without
  the on-screen pet, or vice versa.
- **Services** list: the same four JARVIS services individually, each with
  its own Start/Stop and Restart, live status dot (green = running).
  Useful when only one thing needs a kick instead of the whole stack.
- **Voice**: speaker, language, and speed, pulled live from the TTS
  server's own `/speakers` and `/languages` endpoints when it's running
  (falls back to just showing the currently-saved values if it's off).
  "Apply" writes `computer/tts/config.yaml`'s `voice:` block and restarts
  the TTS server so the new default takes effect immediately.
- **Performance**: the model server's CPU thread count
  (`config/.env.local_model`'s `JARVIS_MODEL_THREADS`). This is the knob
  that caused real reliability problems when set too high (see Build 007
  in `DEVELOPMENT_LOG.md`) -- PA shows how many CPU threads the machine
  has as a reference point. Takes effect on the model server's next
  restart, not live.
- **Advanced**: opens OpenClaw's own web Control UI (same as the pet's
  tray menu), and opens the `logs/` folder in Finder.

## How service control actually works

Every LaunchAgent plist (`~/Library/LaunchAgents/com.jarvis.*.plist`) has
`RunAtLoad` set to `false` -- login does nothing on its own. PA starts a
service with `launchctl bootstrap` (loads the job definition, dormant)
followed by `launchctl kickstart -k` (actually runs it), and stops one
with `launchctl bootout` (fully unloads it, which is what actually
prevents `KeepAlive: true` services -- gateway, model server, TTS server,
voice wake all have it -- from immediately relaunching themselves; a plain
`stop`/`kill` would not survive that). This mirrors the same commands the
pet's own tray menu and the various READMEs already documented for manual
use; PA is just a GUI over the same operations, run automatically instead
of typed by hand.

## Why the plist-parsing in `main.js` looks unusual

`launchctl list <exact-label>` (one specific job) prints a different,
older dict-style format than bare `launchctl list` (the full table) --
`"PID" = 1234;` only appears while the job is actually running, absent
while loaded-but-stopped. `main.js`'s `isRunning()` was originally written
against the wrong format (copied from a mental model of the table output)
and gave false readings until this was caught and fixed by testing against
the real command output, not assumed from memory -- see the code comment
there if this ever needs touching again.

## Reusing Electron instead of a second install

`~/Applications/PA.app/Contents/MacOS/PA` is a small shell script that
execs the *pet's* already-downloaded Electron binary
(`computer/pet/node_modules/electron/...`) pointed at `computer/pa/`
instead of `computer/pet/`. No separate `npm install` for PA, no second
~200MB Electron download. If you ever move the whole `JARVIS/` repo, both
that script's hardcoded path and the pet's LaunchAgent plists break the
same way -- ask JARVIS to regenerate them.

## Files

- `main.js` -- Electron main process: all launchctl control, voice-config
  read/write, thread-count read/write, IPC handlers.
- `preload.js` -- `contextBridge` API (`window.pa.*`), `contextIsolation`
  on, `nodeIntegration` off, same pattern as the pet app.
- `index.html` / `renderer.js` -- the UI: toggles, service list, voice
  dropdowns, thread input, status polling every 4s.
- `package.json` -- present for `npm start` during development; the real
  `.app` launcher doesn't use it (see above).
