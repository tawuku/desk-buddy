# desk-buddy

A little companion that lives on your Mac's screen -- and, if you want, a
fully local voice assistant behind it. No cloud, no account, no subscription.

**Two ways to use it:**

| | Pet only | JARVIS + Pet |
|---|---|---|
| Desktop pet (5 characters, walks around, reacts to what you're doing) | ✓ | ✓ |
| Goals, water breaks, mood check-ins, "call Mom" nudges, CPU alerts | ✓ | ✓ |
| Apple Health from your iPhone (via a Shortcut) | ✓ | ✓ |
| "Hey Jarvis" voice assistant, running 100% on your Mac | | ✓ |
| Full-screen daily briefing on every display | | ✓ |
| Reminders & timers, memory, Apple Notes, documents, web previews by voice | | ✓ |
| Needs | macOS + Node.js | + Homebrew, ~2 GB disk |

## Install

```bash
git clone https://github.com/<you>/desk-buddy.git
cd desk-buddy
./install.sh --pet-only      # just the pet
# or
./install.sh                 # JARVIS + pet
```

Remove it again with `./uninstall.sh`.

## The pet

Pick **Little man, Tux the cat, Bao the panda, Bolt the robot or Fin the fox**
(🐾 menu bar icon -> *Goals & reminders…* -> Pet). It walks around your
screen in every direction and does something fitting for what you're doing:
types on a laptop while you code, scrolls a phone while you browse, wears a
headset on calls, dances to music, dozes when you're away -- and cracks the
odd joke. Click it, drag it, see what happens.

**Goals & reminders** (🐾 menu): log daily / weekly / monthly goals and the
people you want to stay in touch with. Your pet pops up small cards: goal
check-ins, water breaks, a mood check-in, "you haven't called Mom in 12 days",
ideas to surprise your partner, and a heads-up when your Mac's CPU is maxed
out. Quiet hours, and never while you're on a call.

**Apple Health**: an iPhone Shortcut drops your steps / sleep / activity into
iCloud Drive; the pet nudges you to walk, JARVIS reviews it in your briefing.
See [computer/health/README.md](computer/health/README.md).

## JARVIS

Say **"hey Jarvis"**, then talk. Everything runs locally: wake word
(openWakeWord), speech recognition (whisper.cpp), a small language model
(Qwen3 1.7B on llama.cpp) and a natural voice (Piper).

- "Give me an update" -- a full-screen briefing on all your screens:
  priorities, goals, health, notes, weather, inbox, projects, news.
- "Remind me to call Mom at 6", "set a timer for 10 minutes"
- "Remember that Anna loves sunflowers" (used in later answers)
- "I worked out today", "add a goal to read 3 times a week"
- "Open my CV" (and it tells you what's in it), "summarize it",
  "preview example.com", "read my latest note"
- "Thanks" / "that's all" / "go to sleep" ends the conversation.

Start it from **PA** (the control app in ~/Applications) or hold **Space for
3 seconds** after turning on your Mac for the boot intro. Settings:
`config/jarvis_sources.json` (your name, city, news feeds, project folders).

## Privacy

Everything stays on your Mac. The only network calls are the lookups you ask
for (weather, news, web search, the one-time model downloads). Your settings,
goals, memories and reminders live in `config/` and `database/`, which are
never committed.

## Layout

```
computer/pet/     desktop pet, goals & reminders (Electron)
computer/voice/   JARVIS voice app (Python)
computer/screen/  full-screen briefing + boot intro
computer/boot/    hold-Space boot key (Swift)
computer/pa/      PA control app (Electron)
computer/health/  Apple Health Shortcut guide
config/           settings (examples are committed; your own copies are not)
```

## License

MIT
