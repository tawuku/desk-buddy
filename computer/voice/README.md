# JARVIS voice wake

Always-on "hey Jarvis" wake-word listening, fully local: openWakeWord for
detection, whisper.cpp for transcription, the running JARVIS Gateway for the
actual reply, the local XTTS-v2 server (`computer/tts/`, Build 006) to speak
it back -- falls back to macOS `say` if that server isn't reachable. No
cloud STT, no cloud wake-word service, no account/API key for any of it.

## Turn it on/off

Through the pet's tray menu (🐾 -> **Voice wake ("hey Jarvis")** checkbox) --
that's the intended way, and what "settings" means here: there's no other UI.
It's a real on/off, not just pause/resume: toggling it loads/unloads the
`com.jarvis.voice-wake` LaunchAgent, so it also stops actually listening (no
mic activity) when off, and stays off across restarts until toggled back on.

Command line, if you want it instead:

```sh
launchctl load -w ~/Library/LaunchAgents/com.jarvis.voice-wake.plist    # on
launchctl unload -w ~/Library/LaunchAgents/com.jarvis.voice-wake.plist  # off
tail -f logs/voice-wake.log                                            # what it's doing
```

## How it works

1. `wake_listener.py` opens a continuous microphone stream and feeds 80ms
   frames to openWakeWord's pretrained `hey_jarvis` model (no custom
   training needed -- this exact wake word ships as one of its built-in
   models).
2. On a detection (score >= 0.5), it tells the desktop pet app's HUD panel
   to switch to "listening" (see `computer/pet/README.md`'s "Wake panel"
   section -- best-effort HTTP POST, doesn't block if the pet isn't
   running), plays a short cue sound, then records your follow-up utterance
   -- stops on ~1.2s of trailing silence, or after 12s regardless.
3. The recording is transcribed locally with `whisper-cli` (`ggml-base.en.bin`,
   in `models/`) -- the HUD panel switches to "heard" and shows the
   transcript.
4. The transcript is sent to the **already-running** JARVIS Gateway as a chat
   message (`openclaw agent --agent jarvis --session-key agent:jarvis:voice
   --message "..."`) -- a stable session key, so repeated voice turns reuse
   the same prompt-prefix caching regular chat sessions benefit from (see
   the root `DEVELOPMENT_LOG.md`'s "Performance on this hardware"). The HUD
   panel switches to "thinking" with a live elapsed-time counter for
   however long this takes.
5. The HUD panel switches to "reply" (or "error" if the agent call failed)
   and shows the text. The reply is spoken via the local XTTS-v2 server
   (`computer/tts/`, a direct HTTP call to its already-warm `/speak`
   endpoint -- not through OpenClaw's own `tts-local-cli` config, since this
   script talks to the Gateway over its CLI, not through whatever plays
   audio for it) and shown as a notification.
6. Back to listening.

## Setup (already done on this machine, documented for a fresh one)

openWakeWord's dependency `onnxruntime` had no wheel for this Mac's Python
3.14 (Intel + very new Python -- ML packages lag on that combination).
Rather than compile a compatible Python via Homebrew (tried `python@3.12`
first; it started a from-scratch `--enable-optimizations` build, the same
kind of multi-hour rabbit hole as the `llama.cpp`/`pnpm` Homebrew builds
documented in `DEVELOPMENT_LOG.md`), this uses
[`uv`](https://docs.astral.sh/uv/) to fetch a **prebuilt** Python 3.12
instead -- seconds, not minutes:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs to ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12

cd computer/voice
uv venv --python 3.12 venv
source venv/bin/activate
uv pip install -r requirements.txt

python3 -c "from openwakeword import utils; utils.download_models()"
```

`whisper-cli` and its model are separate, no Python involved:

```sh
brew install whisper-cpp portaudio   # portaudio: sounddevice's mic backend
curl -L -o ../../models/ggml-base.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
```

## What's verified vs. what needs you to actually speak

Verified directly, without relying on a live human voice (nothing in this
environment can actually speak into a physical microphone):

- **Wake-word discrimination**: fed the model `say`-synthesized "Hey Jarvis"
  audio (converted to 16kHz mono via `afconvert`) -- scored **0.799** (well
  above the 0.5 threshold). Fed it synthesized unrelated speech ("Hello, how
  are you today...") -- scored **0.0001**. Strong separation, not a coin flip.
- **Transcription**: `whisper-cli` on a synthesized "What time is it right
  now?" clip returned that exact text, in ~5 seconds.
- **Full pipeline end-to-end** (transcribe -> ask the real running Gateway ->
  get a reply): tested directly, bypassing only the live wake-detection loop
  (already verified separately above). See `DEVELOPMENT_LOG.md` for the
  actual transcript/timing.
- **Live mic recording** works in this shell context without a permission
  prompt (already granted, inherited from Terminal).

**Not verified, and can't be from here**: whether *your actual voice*, at
normal speaking volume/distance from this laptop, reliably triggers
detection and transcribes accurately. The acoustic test above used
synthesized speech fed through file/array data, not a real
speaker-to-microphone round trip (the system output happened to be muted
when this was built, so a real loopback test wasn't attempted rather than
changing your system volume/mute state without asking). Try it once it's
on; if `hey_jarvis` doesn't trigger reliably from a normal distance/volume,
the fixes are: lower `DETECTION_THRESHOLD` in `wake_listener.py` (default
`0.5`), or check `system_profiler SPAudioDataType` / System Settings ->
Sound -> Input to confirm the right mic and a reasonable input level.

The launchd-run process (not just this interactive shell) was confirmed
separately: loaded via `launchctl load -w`, logged `ready, listening for
'hey Jarvis'`, held an open `InputStream` at ~11% CPU with no error -- so
whatever microphone permission is needed, this Mac already had it (no
separate prompt blocked startup here). If a *different* machine blocks on
this, it'll show up as the process erroring or hanging right after "loading
wake-word model..." in `logs/voice-wake.log`; the fix is System Settings ->
Privacy & Security -> Microphone -> grant it to that Python binary.

## Tuning

All in `wake_listener.py`:

| Constant                  | Default | What it does                                    |
| -------------------------- | ------- | ------------------------------------------------ |
| `DETECTION_THRESHOLD`      | `0.5`   | Lower = more sensitive (also more false triggers) |
| `COOLDOWN_SECONDS`         | `2.0`   | Ignore repeat detections for this long after one fires |
| `RECORD_MAX_SECONDS`       | `12`    | Hard cap on a single recorded utterance           |
| `SILENCE_TRAIL_SECONDS`    | `1.2`   | Trailing silence needed to stop recording early   |
| `SILENCE_RMS_THRESHOLD`    | `150`   | int16 RMS below this counts as "silence"          |
| `SESSION_KEY`              | `agent:jarvis:voice` | Which JARVIS session voice turns land in |

## Known limitations

- English wake word/transcription only (`hey_jarvis` + `ggml-base.en.bin`).
  openWakeWord ships a few other pretrained words (`alexa`, `hey_mycroft`,
  `hey_rhasspy`) if you want to experiment; a non-English Whisper model
  (`ggml-base.bin`, drop the `.en`) would be needed for anything beyond
  English commands.
- One request at a time -- while JARVIS is thinking/replying, the listener
  isn't scoring new audio for the wake word (matches how a real assistant
  behaves: it can't be interrupted mid-answer either, in this build).
- Cold-session latency: the *first* voice turn after a Gateway restart is
  slow for the same reason every fresh chat session is slow on this hardware
  (see the root `DEVELOPMENT_LOG.md`) -- minutes, not seconds. Using a
  stable `SESSION_KEY` means that cost is paid once per Gateway restart, not
  once per wake.
