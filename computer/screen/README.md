# JARVIS screen

The full-screen briefing: a particle orb that talks, next to a "daily
findings" panel of six categories. Implemented from the Claude Design
project "Jarvis Screen" (`Jarvis Screen.dc.html`), rewritten as one
dependency-free page, `index.html`.

## When it appears

- **Only when you ask** for your updates or briefing ("hey Jarvis... give me
  an update", "any updates?", "brief me", "what's new", "catch me up"): it
  takes over *every* connected display and presents the day's findings.
  ("Updates on <a project>" stays a normal project answer.) Say "hey Jarvis"
  during it to cut it short -- all six cards stay up and JARVIS listens.
  To have it on every wake instead, set `brief_on_wake` in
  `config/screen.json`.
- **Pet tray menu** -> *Open JARVIS screen* / *Brief me on all screens*, or
  **PA** -> Advanced -> *Brief me on all screens*.

## Kill switch / minimize

| | Close | Minimize ⇄ restore |
|---|---|---|
| On screen | ✕ (top right), DISMISS, Esc | — (top right); click the mini-player to restore |
| Keyboard, anywhere | ⌘⇧⎋ | ⌘⇧M |
| Voice (while it's up) | "close the screen", "dismiss", "stop the briefing" | "minimize" / "hide it" · "full screen" / "bring it back" |
| Pet tray | Close JARVIS screen | Minimize JARVIS screen |

Minimized, it's a small corner mini-player (orb + caption) that keeps
talking; the other displays are freed. It also closes by itself 2 minutes
after the briefing (15 s after the conversation goes back to sleep). If the
pet isn't running, JARVIS speaks the briefing the old way.

## Look

Particle orb with HUD rings that brighten with the voice, a drifting grid
and scan line; each category card wipes in with a sweep, types its
headline, and decodes its values; a results feed under the caption lists
every finding as it's reported.

## Boot intro

`boot.html` -- see `computer/boot/README.md` (hold Space for 3 s after
turning the Mac on).

## How it's wired

```
wake_listener.py --POST 8092/screen--> pet (main.js): one frameless window per display
      |                                   primary display = lead, others ?role=mirror
      |                                             |
 screen_server.py (8094, inside JARVIS) <-----------+  GET /  /findings, POST /tts (Piper WAV)
      ^  lead POSTs /screen/status (phase, step, caption...) ; mirrors poll it
      |  JARVIS waits for phase != "briefing"; on barge-in queues "stop"
      +- findings.py -> cache/findings.json (rebuilt every 30 min while you're active)
```

- **Findings** (`computer/voice/findings.py`) come from the same sources as
  the voice skills: PRIORITY (open tasks in TASKS.md / project task files),
  WEATHER, INBOX (Mail.app), PROJECTS (folder activity), WORLD (RSS; only
  `"lang": "en"` feeds in `config/jarvis_sources.json` are read aloud),
  SYSTEMS (model / speech servers, time on the Mac). No model is involved --
  it's fast and can't invent anything. `python3 findings.py` builds and
  prints it.
- **Voice**: each line is rendered by Piper (same voice/speed as PA's Voice
  card) and played by the page through an analyser, so the orb follows the
  real audio level. The next lines render while the current one plays.
- **Conversation**: after the briefing, JARVIS's HUD stages (heard /
  thinking / speaking) show in the screen's caption instead of the HUD card.
- Opened as a plain file (or in a browser tab without the pet) it runs on
  its own; as a file it uses the design's demo data and the browser voice.

URL options: `?palette=blue|cyan|amber`, `?density=1200..6000`, `?voice=0`,
`?autostart=1`, `?embedded=1` (pet window), `?role=mirror`.
