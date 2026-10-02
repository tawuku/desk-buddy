# PA -- the JARVIS control app

A small Electron window for turning JARVIS and the pet on and off and changing
a few settings. It is not a background service: closing it stops nothing.

## Opening it

`./install.sh` puts it at `~/Applications/PA.app` -- Spotlight-searchable
("PA"), double-clickable, no Terminal needed.

## What it shows

- **JARVIS** -- one switch (and a Restart button) for the voice app
  (`computer/voice/wake_listener.py`, the `com.jarvis.voice-wake` LaunchAgent).
- **Desktop pet (HUD)** -- its own switch (`com.jarvis.pet`).
- **Brain** -- only when this Mac is connected to a brain server
  (`config/remote.json`, see `computer/server/`): whether the other computer
  answers, how fast, and where each part runs -- Thinking (the language
  model), Hearing (speech recognition), Voice. Red means that part isn't
  running over there.
- **Voice** -- the Piper voice and speed (`config/voice.json`). The list is the
  voices in `models/piper/`, or the brain server's when the voice runs there.
  Applies to JARVIS's next sentence, no restart.
- **Performance** -- only when the language model runs on this Mac: its CPU
  thread count (`JARVIS_MODEL_THREADS` in `config/.env.local_model`), applied
  the next time JARVIS starts.
- **Advanced** -- Chat with JARVIS, Goals & reminders, Brief me on all screens
  (all three are windows of the pet app, so the pet must be on), Open logs.

## How service control works

"Off" has to survive a restart, so PA stops a service with `launchctl disable`
+ `launchctl bootout` and starts it with `launchctl enable` + `bootstrap` +
`kickstart -k`. Running/stopped comes from `launchctl list <label>`, which
prints a `"PID" = 1234;` line only while the job is actually running.

## Files

- `main.js` -- Electron main process: launchctl control, voice config,
  thread count, brain status (`../pet/brain.js`), IPC handlers.
- `preload.js` -- the `window.pa.*` bridge (`contextIsolation` on,
  `nodeIntegration` off).
- `index.html` / `renderer.js` -- the UI; status every 4 s, brain every 15 s.

PA has no Electron of its own: `PA.app` is a small script that runs the pet's
Electron binary (`computer/pet/node_modules/electron/...`) on `computer/pa/`.
If you move the repo folder, run `./install.sh` again to fix the paths.
