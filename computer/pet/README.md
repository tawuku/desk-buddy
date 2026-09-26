# JARVIS desktop pet

> **2026-09-26 (later):** five characters to pick from (Little man, Tux the
> cat, Bao the panda, Bolt the robot, Fin the fox), front/side/back views,
> and **Goals & reminders** -- pop-up cards above the pet for goals, water,
> mood, CPU, calling friends & family and girlfriend ideas (`reminders.js`,
> `goals.html`, `popup.html`; tray -> *Goals & reminders…*).
>
> **2026-09-26:** the pet is now a **little man** (not a cat) -- he walks and
> jogs along the screen, does gestures for whatever you're doing (laptop
> while coding, phone while browsing, headset on calls, dancing to music,
> dozing when you're away), reacts to clicks and drags, and cracks the odd
> joke in a speech bubble. See the end of `DEVELOPMENT_LOG.md` for the full
> list; poses in `sprite.js`, behaviour in `renderer.js`, try it in
> `preview.html`. Older sections below still say "cat".


A small pixel-art cat that hovers on screen, always on top, and reacts to
whichever app is frontmost -- follows you across monitors, nudges you about
time spent and open tasks, and fidgets/shifts posture on its own. Its
*animation* has no dependency on the OpenClaw Gateway or the local model --
purely animated (see `DEVELOPMENT_LOG.md` for why: this hardware's
local-model latency makes live, model-driven reactions infeasible) -- but its
tray menu is also the control point for the other three JARVIS services (see
"Controlling JARVIS from the tray" below), and saying "hey Jarvis" pops up a
small status/todos card next to it (see "Wake panel" below).

## Run it

```sh
cd computer/pet
npm install     # first time only, ~300MB (Electron itself)
npm start
```

It also auto-starts at login via a `launchd` LaunchAgent (see "Login item"
below) -- `npm start` is only for manual runs/testing.

No dock icon, no menu bar window -- look for it in the bottom-right corner of
your screen. It's controlled from a menu-bar item titled 🐾 (Pause reactions,
Reset position, Quit).

## What it reacts to

Polls the frontmost app's process name every 1.5s (via `osascript`/System
Events -- no special macOS permission needed) and maps it to a mood:

| Mood        | Triggered by (substring match, case-insensitive)                    |
| ----------- | --------------------------------------------------------------------- |
| `coding`    | Code, Cursor, Terminal, iTerm, Xcode, Vim, Zed, Sublime, JetBrains IDEs |
| `browsing`  | Safari, Chrome, Arc, Firefox, Brave, Edge                             |
| `listening` | Zoom, FaceTime, Slack, Teams, Discord, Meet                           |
| `vibing`    | Music, Spotify, Podcasts                                              |
| `asleep`    | no keyboard/mouse input for 5+ minutes (`powerMonitor.getSystemIdleTime()`) |
| `idle`      | anything else -- see "Postures" below                                 |

Edit `APP_MOOD_RULES` in `main.js` to add more apps or moods.

Click the cat for a quick reaction (happy bounce / meow / surprised, chosen
at random); drag it anywhere on screen and its position is remembered across
restarts, per monitor (`database/pet/window-position.json`).

## Postures and fidgeting

When idle (no particular app-mood applies), the cat rotates through a few
postures every 20-40s instead of one static pose: sitting, a stretch-and-yawn,
grooming a paw, a flattened "loaf", and idly looking around. On top of
whatever mood/posture is active, small fidgets (an ear twitch, a tail flick,
a quick extra blink) fire every 5-12s for variety -- all closed-form
animation, no state to manage beyond a couple of timers. See `POSTURES` and
the fidget block in `renderer.js`.

## Following you across monitors

Every 2s, it also checks which monitor the *frontmost window* (not just the
frontmost app) is on, via `osascript`/System Events window position+size, and
if that's a different monitor than the one it's currently on, it plays a fast
"running" pose while the window animates across to that monitor's saved (or
default bottom-right) position over ~700ms.

**This needs macOS Accessibility permission** (System Settings -> Privacy &
Security -> Accessibility -> allow the app running this -- Electron, or
Terminal in dev mode) -- reading another app's *window position* is a step up
from just reading its *name* (which needs no permission at all, and is all
the mood-detection above uses). Without that permission, this feature just
silently does nothing -- the pet stays on its current monitor, no crash, no
repeated error dialogs. It was **not granted** as of this app's initial
build; grant it if you want the cross-monitor following to actually work.

## Controlling JARVIS from the tray

The 🐾 menu is also the control point for the other two persistent JARVIS
services (see the root `README.md`'s "How to use it day to day"):

  (`openclaw dashboard --yes`), which opens OpenClaw's real web chat UI in
  your default browser, pre-authenticated. This is the normal way to talk to
  JARVIS.
- **Restart Gateway** / **Restart Model Server** -- `launchctl kickstart -k`
  on `com.jarvis.gateway` / `com.jarvis.llama-server` respectively, with a
  native notification when issued. Takes ~10-20s to fully come back (model
  load / database checks) -- these fire-and-forget rather than confirm
  success; check `logs/gateway.log` / `logs/llama-server.log` if unsure.

These three `.plist` files (`com.jarvis.pet`, `com.jarvis.llama-server`,
`com.jarvis.gateway`) are independent LaunchAgents; restarting one doesn't
touch the others. No start/stop toggle in the tray (would need live-state
polling before every menu open) -- use `launchctl unload`/`load -w` on the
relevant plist directly for that.

## Nudges

Two kinds of proactive native macOS notifications (`Notification` API), both
respecting the tray's "Pause reactions" toggle:

- **Time spent**: if you stay on one nudge-worthy mood continuously (default:
  45 min for `browsing`/`vibing`, 90 min for `coding`; `listening` is
  deliberately exempt so it never interrupts a call), you get "You've been
  \<mood\> for about N min -- worth a break?", then again every additional
  threshold-worth of time if you don't switch. Edit `TIME_NUDGE_THRESHOLD_MIN`
  in `main.js`.
- **Still open**: every ~2 hours you're actually active (the clock pauses
  while `asleep`), if `TASKS.md` (JARVIS repo root) has any `- [ ]` line, you
  get a notification naming the *oldest* one. Ask JARVIS in normal
  conversation to add/check off/remove tasks there -- this app only reads the
  file, it never writes to it.

## Wake panel (the HUD)

Say "hey Jarvis" and a HUD-style card appears (top-center of your primary
display): an animated arc-reactor ring, a scanline overlay, and a live
readout of the conversation itself -- not just a status snapshot. It tracks
the voice pipeline stage by stage:

- **listening** (green ring) -- recording your follow-up.
- **heard** -- shows the transcribed text once whisper.cpp finishes.
- **thinking** (amber ring, ticking elapsed-time counter) -- waiting on the
  local model. This can legitimately sit here for up to ~30 minutes on a
  cold session -- see the root `DEVELOPMENT_LOG.md`'s "Performance on this
  hardware" -- the panel stays up the whole time rather than timing out.
  There's no other feedback during this stretch otherwise, so the ticking
  counter is the only signal anything is still happening.
- **reply** (back to cyan) -- the actual reply, typed out. The reply is
  still spoken separately (XTTS-v2, `computer/tts/`) and shown as its own
  native notification, exactly as before; the panel is a visual companion to
  that, not a replacement.
- **error** (red) -- couldn't get a reply; shows why.

Below that, the same **Todos** (open `TASKS.md` items) and **Updates**
(build line + live service dots) as before -- now secondary information
under the conversation instead of the whole card.

**How it's triggered**: `computer/voice/wake_listener.py` (a separate
Python process from this Electron app) POSTs one stage at a time to a tiny
loopback-only HTTP server this app runs on `127.0.0.1:8092`
(`POST /state`, JSON body `{"stage": "...", "text": "..."}`) -- the only
channel between the two processes, since they share no other IPC. A stage
with nothing left to say (`reply`/`error`/`empty`) gets a hold-then-fade
timer on the renderer side; every other stage stays up until the next one
arrives. Best-effort throughout: if the pet isn't running, each POST just
fails silently and voice-wake continues exactly as it would otherwise (the
panel is a visual bonus, never a dependency of the actual reply). You can
also trigger a static preview from the tray (🐾 -> **Show JARVIS panel**)
without saying anything, e.g. to check service status at a glance -- that
one still auto-hides after 15s since there's no live pipeline behind it.
`panel-preview.html` (open via `python3 -m http.server` in this directory)
mocks the Electron bridge and cycles through every stage, for
designing/tuning the HUD without going through the real voice pipeline.

## Login item

**As of Build 008, this does not auto-start at login** -- the LaunchAgent's
`RunAtLoad` is off, same as JARVIS's other four services; see
`computer/pa/README.md`. Open **PA** (`~/Applications/PA.app`) and flip its
"Desktop pet" switch instead. The rest of this section covers how the
underlying launchd control still works, which PA is a GUI over.

Process management is still via the `launchd` LaunchAgent, **not**
Electron's `app.setLoginItemSettings` -- that API needs a properly
packaged, installed `.app` and reliably fails (`Operation not permitted`)
for a dev-mode app run straight out of `node_modules/electron`, which is
what this is. The LaunchAgent points directly at the Electron binary this
`npm install` downloaded:

```sh
# Installed at ~/Library/LaunchAgents/com.jarvis.pet.plist, already loaded
# (dormant -- RunAtLoad is off). PA uses these same commands:
launchctl kickstart -k gui/$(id -u)/com.jarvis.pet   # start/restart
launchctl bootout gui/$(id -u)/com.jarvis.pet         # stop for real

# To reinstall/reload after moving this repo or reinstalling Electron:
launchctl bootout gui/$(id -u)/com.jarvis.pet
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jarvis.pet.plist
```

Its logs go to `logs/pet.log` (JARVIS repo root) rather than a terminal,
since nothing launches it interactively anymore. The tray's "Quit" and
PA's "Desktop pet" switch both do the same thing; `KeepAlive` is off, so
quitting doesn't relaunch it -- open PA (or the tray, if the pet's already
up) when you want it back.

If you ever move this whole `JARVIS/` folder, the plist's hardcoded paths
break -- ask JARVIS to regenerate it at the new location.

## How it's built

- `sprite.js` -- pure canvas drawing function, `drawCat(ctx, size, pose)`.
  A "pose" is a plain object (eye openness, pupil direction, ear angle, paw
  lift, tail angle, body squash/bob, mouth shape, blush) with sensible
  defaults for anything omitted. Shared as-is between the preview page and
  the real app; every posture/mood/reaction/fidget added since the first
  version reused these same fields creatively (e.g. a negative `squash`
  stretches the cat taller instead of flattening it) rather than needing new
  drawing code.
- `preview.html` -- side-by-side pose gallery, used to design/tune the sprite
  visually before wiring it into the app. Open it directly (`python3 -m
  http.server` from this directory, then visit the page -- `file://` doesn't
  work with some browser tooling) whenever you want to tweak a pose without
  relaunching Electron.
- `renderer.js` -- the animation state machine: per-mood ambient motion,
  idle postures, fidgets, the transient `running` pose used during a
  cross-monitor move, and click-triggered reactions -- priority order
  reaction > running > posture/mood > fidget-overlay. All poses are
  closed-form functions of elapsed time -- no external calls, so everything
  is instant.
- `main.js` -- the Electron main process: window creation; frontmost-app
  polling (mood), frontmost-window-display polling (monitor following),
  idle polling (asleep), a once-a-minute tick (time/task nudges); the tray
  menu; per-monitor window-position persistence; the wake panel's HTTP
  server and data-gathering (todos, build status, live service checks).
- `preload.js` -- the only bridge between the cat's renderer and main (mood
  updates in, drag deltas out), via `contextBridge` with
  `contextIsolation: true` and `nodeIntegration: false`.
- `panel.html` / `panel-renderer.js` / `panel-preload.js` -- the wake panel
  (HUD): a second, independent `BrowserWindow` (own preload/bridge --
  `onData`/`onState`/`dismiss`) recreated fresh on the first stage of each
  "hey Jarvis" rather than reused, to sidestep any race between sending its
  data and the renderer's listener being ready; later stages of the same
  conversation just push into the already-open window -- see "Wake panel"
  above. `panel-preview.html` is a browser-only harness (mocks
  `window.panel`) for iterating on the HUD's look without Electron.

## Adding another mood, posture, or sprite state

1. Add a pose-generator function to `MOODS` (app-driven) or `POSTURES`
   (idle-only rotation) in `renderer.js` -- it just returns a partial pose
   object; anything you omit falls back to `sprite.js`'s defaults.
2. Preview it: add it to `preview.html`'s `poses` object, open the preview
   page, screenshot/inspect, iterate.
3. If it should be triggered by an app rather than a click or idle rotation,
   add a rule to `APP_MOOD_RULES` in `main.js`.

## Known limitations

- Two-way voice/model integration into the *cat's own animation*:
  deliberately not connected. See the main `DEVELOPMENT_LOG.md`'s
  "Performance on this hardware" entry for why a 10-30 minute local-model
  reply time makes that a bad fit for a live on-screen reaction. The wake
  panel (above) is the model-aware surface instead -- it's fine to sit in
  "thinking" for a long time since it says so explicitly.
- Wake panel: no queueing -- a second "hey Jarvis" while a conversation is
  still in progress just overwrites the current stage with the new one
  (harmless, but you'll lose visibility into the interrupted turn's state).
- Cross-monitor following needs Accessibility permission, not yet granted --
  see "Following you across monitors" above.
- Window-title-level context ("which browser tab") was deliberately left out
  -- it would need macOS Screen Recording permission; everything here needs
  at most Accessibility.
- `TASKS.md` nudges are read-only from this app's side; it never edits the
  file. Ask JARVIS (in conversation, not the pet) to change it.
