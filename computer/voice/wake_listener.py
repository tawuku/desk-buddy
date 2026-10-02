#!/usr/bin/env python3
"""
JARVIS Lite -- the whole assistant in one local process.

Hears "hey Jarvis" (openWakeWord, with a Whisper second opinion for unsure
scores), greets you, then holds a conversation: each utterance is
transcribed by a resident whisper-server, answered instantly from
config/quick_replies.json when it's small talk (quick.py), otherwise routed
to live data (skills.py: weather, news, web, Mail, project folders) and the
local Qwen model (a llama-server child process), and spoken with Piper
(tts.py). Progress and replies are pushed to the desktop pet's HUD panel.
The morning briefing is prepared in the background here too. Waking JARVIS
(or asking for the briefing) opens the full-screen JARVIS screen on every
display (screen_server.py, port 8094; config/screen.json), which presents
the day's findings and speaks them with Piper before the conversation.

No gateway / agent framework: this replaced the OpenClaw + XTTS setup,
which needed ~7.7GB of RAM and five background services.

Toggle: run as the `com.jarvis.voice-wake` LaunchAgent; PA's JARVIS switch
starts/stops it (and with it, its child servers).

Everything runs locally. The only network calls are the lookups in
skills.py (weather, news, web search), made only when a question needs them.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import signal
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
from openwakeword.model import Model

import activity
import business
import findings
import mac_control
import quick
import screen_server
import skills
import smarts
import tts

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280  # openWakeWord's expected chunk size (80ms @ 16kHz)
# Wake instantly only on a confident score; everything from CONFIRM_MIN_SCORE
# up to this needs the Whisper second opinion below. Was a flat 0.3 (after
# 0.4 missed real, quieter attempts at 0.16-0.26), but 0.3+ also let nearby
# conversation wake JARVIS ("You did it. I threw water at us.").
DETECTION_THRESHOLD = 0.8
# Second opinion for unsure detections: openWakeWord's score depends on
# voice/accent/room, not loudness (tested: 5% volume still scores 1.0), and
# Quieter real attempts scored 0.16-0.26. Anything from CONFIRM_MIN_SCORE to
# DETECTION_THRESHOLD gets the last CONFIRM_WINDOW_SECONDS transcribed by
# Whisper (~1s); only if it heard "Jarvis" does that count as a wake.
CONFIRM_MIN_SCORE = 0.1
CONFIRM_WINDOW_SECONDS = 2.5
CONFIRM_TAIL_SECONDS = 0.4  # let the phrase finish before transcribing
# The wake phrase is "wake up Jarvis". openWakeWord only ships a "hey jarvis"
# model, which scores "wake up Jarvis" ~0.4, so it acts as the acoustic gate
# and Whisper decides: every detection (even a confident one) must be heard
# as "wake up" + Jarvis. Plain "hey Jarvis" no longer wakes it.
CONFIRM_PATTERN = re.compile(r"\bwake[\s,.\-]*up\b[\s,.\-]*(jarvis|jarvi|jervis|javis|charvis)\b", re.I)  # not "travis": a real name
NEAR_MISS_LOG_SCORE = 0.15  # log weaker detections too, to tune the threshold
HEARTBEAT_SECONDS = 300  # periodic "still alive, mic level X" log line
COOLDOWN_SECONDS = 2.0  # ignore further detections right after firing once

RECORD_MAX_SECONDS = 15  # max length of one question, counted from when you start talking
RECORD_MIN_SECONDS = 0.6
SILENCE_TRAIL_SECONDS = 1.2
SILENCE_RMS_THRESHOLD = 150  # int16 RMS; tune if it cuts off too early/late
# How long to wait for you to *start* talking after the greeting. Trailing
# silence only ends the recording once speech has actually been heard --
# before this, a quiet first second cut recordings off at ~1.5s.
# After "hey Jarvis" (and after every answer) JARVIS keeps listening for
# the next question; it goes back to sleep after this long with no speech.
CONVERSATION_IDLE_SECONDS = 60.0
# Speech = louder than the room's background noise by this factor (noise
# floor is measured continuously while idle). The fixed 150 was below this
# room's ~185 background level, so "silence" never registered.
SPEECH_OVER_NOISE_FACTOR = 2.0
SLEEP_CUE = "/System/Library/Sounds/Bottle.aiff"
_noise_floor_rms = float(SILENCE_RMS_THRESHOLD) / SPEECH_OVER_NOISE_FACTOR

JARVIS_DIR = Path(__file__).resolve().parent.parent.parent
WHISPER_MODEL = JARVIS_DIR / "models" / "ggml-base.en.bin"
# whisper.cpp rebuilt from Homebrew's cached source with -march=native
# (binaries kept in engines/bin/, like llama-server): Homebrew's ggml has no
# AVX2/FMA. whisper-server keeps the model loaded: ~0.8s per transcription
# vs 5.5s for Homebrew's whisper-cli reloading the model every call.
WHISPER_NATIVE_BIN = JARVIS_DIR / "engines" / "bin"
if not (WHISPER_NATIVE_BIN / "whisper-server").exists() and shutil.which("whisper-server"):
    WHISPER_NATIVE_BIN = Path(shutil.which("whisper-server")).parent  # Homebrew's whisper-cpp
WHISPER_SERVER_PORT = 8093
WHISPER_SERVER_URL = f"http://127.0.0.1:{WHISPER_SERVER_PORT}/inference"
LOCAL_MODEL_ENV_FILE = JARVIS_DIR / "config" / ".env.local_model"

# The model server is a child of this process (JARVIS Lite: one app, no
# gateway). Same CPU-tuned llama.cpp build and Qwen model as before, but a
# 4K context instead of 32K -- voice prompts are well under 1K tokens, and
# the 32K was only ever for OpenClaw's ~12K-token agent prompt.
LLAMA_SERVER_BIN = JARVIS_DIR / "engines" / "bin" / "llama-server"
if not LLAMA_SERVER_BIN.exists() and shutil.which("llama-server"):
    LLAMA_SERVER_BIN = Path(shutil.which("llama-server"))  # Homebrew's llama.cpp
# Qwen3 1.7B: ~2-3x faster than the 4B on this Mac, somewhat less smart.
# The 4B (models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf) is kept -- set
# JARVIS_MODEL_FILE=<file name> in config/.env.local_model to switch back.
LLAMA_MODEL_FILE = JARVIS_DIR / "models" / "Qwen3-1.7B-Q4_K_M.gguf"
LLAMA_PORT = 8080
LLAMA_CONTEXT = 4096

# Daily briefing is prepared in the background by this process: at startup
# if today's is missing, and again at this local time.
BRIEFING_HOUR, BRIEFING_MINUTE = 7, 30

# Spoken the instant the wake word fires -- pre-rendered and cached as WAVs
# (re-rendered automatically when the voice in config/voice.json changes).
USER_NAME = skills.load_sources().get("user_name") or "there"
GREETING_CACHE_DIR = Path(__file__).resolve().parent / "cache"

# Short lines JARVIS says so a wait never feels like dead air -- pre-rendered
# in JARVIS's voice (cache/phrase-*.wav) so they play instantly, and picked
# at random so it doesn't sound like a recording. A phrase that isn't cached
# yet is skipped (never `say`: a different voice mid-conversation is jarring).
PHRASES: dict[str, list[str]] = {
    "greeting": [f"Hey {USER_NAME}, what's up?", f"Hey {USER_NAME}. What can I do for you?", "I'm here. What's up?", f"Yes, {USER_NAME}?"],
    "greeting_morning": [f"Good morning, {USER_NAME}. What's up?", f"Morning, {USER_NAME}. What can I do for you?"],
    "greeting_evening": [f"Good evening, {USER_NAME}. What's up?", f"Evening, {USER_NAME}. What do you need?"],
    "weather": ["Let me check the weather for you.", "One sec, pulling up the forecast."],
    "news": ["Let me see what's in the news.", "Give me a second to grab the headlines."],
    "search": ["Let me look that up.", "Good question. Give me a moment to check."],
    "email": ["Let me check your inbox.", "One moment, I'm looking at your emails."],
    "projects": ["Let me take a look at your projects.", "Give me a second, I'm checking your files."],
    "activity": ["Let me see what you've been up to.", "One sec, checking your Mac."],
    "briefing": ["Sure, here's your briefing.", "Of course. Here's your day."],
    "open_doc": ["On it.", "Opening it now.", "Sure, pulling it up."],
    "read_doc": ["Let me take a look.", "One sec, I'm reading it."],
    "business": ["Let me check your sites.", "One sec, pulling up the numbers."],
    "health": ["Let me check your health data.", "One sec, looking at your iPhone's numbers."],
    "web_preview": ["Pulling it up now.", "Let me bring that up."],
    "briefing_build": ["Give me a minute to put your briefing together.", "Sure. It'll take me a minute to pull everything together."],
    "think": ["Hmm, let me think about that.", "Good question, give me a second.", "Mm, one moment."],
    "still": ["Still on it, just a moment.", "Almost there, bear with me.", "Nearly done, one more second."],
}
# Waiting behaviour (see Feedback): a thinking line after THINK_AFTER s of
# silence, a "still on it" line after STILL_AFTER s more, then a soft tone
# every TONE_EVERY s so it's clear JARVIS hasn't dropped the question.
THINK_AFTER_SECONDS = 3.5
STILL_AFTER_SECONDS = 9.0
TONE_EVERY_SECONDS = 6.0
WAIT_TONE = "/System/Library/Sounds/Purr.aiff"

# Voice questions go straight to the local model with a ~100-token prompt
# plus whatever live data skills.py fetched (no agent framework).
FAST_MAX_TOKENS = 100  # short, lively replies; +60 when there's live data
REPORT_EXTRA_TOKENS = 130  # documents / web pages get a 2-3 sentence report
ACTION_SKILLS = {"open_doc", "read_doc", "web_preview", "notes", "business"}

# Stable system prompt (no time or data in it) so llama-server can reuse its
# processed prefix across turns -- the time, what's on screen and any live
# data go in a bracketed header on each user message instead.
PERSONA = (
    f"You are JARVIS, {USER_NAME}'s personal assistant, talking with them out "
    "loud. Be quick, warm and a little witty, like a sharp friend. Rules: "
    "1) Answer the question directly and specifically in one or two short "
    "sentences -- give a real answer or idea, not a question back. "
    "2) Each message starts with a line in square brackets: the time, what's "
    "on their screen, and sometimes live data. Use it silently -- never read "
    "it out or repeat it. 3) Only state facts about their day, files, emails "
    "or the news that appear in that bracketed data; otherwise say you can't "
    "see it. 4) Ask a follow-up question only sometimes, not every time. "
    "5) No markdown, lists, emoji or URLs -- you're heard, not read. "
    "Never mention being an AI."
)
# Saying "hey Jarvis" while JARVIS is talking cuts it off (score from the
# same wake model; no Whisper check -- it has to react instantly).
BARGE_IN_SCORE = 0.5
MIC_STALL_SECONDS = 5  # no audio frames this long = the stream died (sleep/wake, device change)
FAST_HISTORY_TURNS = 4  # remembered question/answer pairs
FAST_HISTORY_IDLE_RESET_SECONDS = 600  # forget the thread after 10 min quiet


def _load_llama_server_api_key() -> str:
    """The model server's API key (config/.env.local_model)."""
    for line in LOCAL_MODEL_ENV_FILE.read_text().splitlines():
        if line.startswith("LLAMA_SERVER_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"LLAMA_SERVER_API_KEY not found in {LOCAL_MODEL_ENV_FILE}")

LISTEN_CUE = "/System/Library/Sounds/Tink.aiff"

# computer/pet/main.js's wake panel (the Iron-Man-HUD-style card) -- this is
# the only channel between this process and the Electron pet app, since
# they're separate processes with nothing else in common. Each call pushes
# one pipeline stage (see panel-renderer.js's STAGE_LABEL for the full set);
# the panel itself decides how long to stay up per stage.
PANEL_STATE_URL = "http://127.0.0.1:8092/state"


def log(msg: str) -> None:
    print(f"[voice-wake] {msg}", flush=True)


def _phrase_cache_path(text: str) -> Path:
    """Cache key = phrase + current voice config, so changing the voice in PA
    (config/voice.json) re-renders every phrase in the new voice."""
    try:
        config_bytes = tts.VOICE_CONFIG_FILE.read_bytes()
    except OSError:
        config_bytes = b""
    digest = hashlib.sha1(text.encode("utf-8") + config_bytes).hexdigest()[:12]
    return GREETING_CACHE_DIR / f"phrase-{digest}.wav"


def _synthesize(text: str, timeout: float = 90) -> bytes:  # noqa: ARG001 -- kept for callers
    return tts.synthesize(text)


_phrase_lock = threading.Lock()


def warm_phrase_cache() -> None:
    """Background: render every phrase (greetings first) with Piper -- about
    0.3s each, so the whole bank takes well under a minute."""
    if not _phrase_lock.acquire(blocking=False):
        return  # another warm-up is already running
    try:
        wanted = [t for group in PHRASES.values() for t in group]
        # config/quick_replies.json fixed replies ("Good night, <name>...").
        wanted += [t for t in quick.static_phrases() if t not in wanted]
        wanted.append(quick.fill("Good morning, {name}! How did you sleep?"))
        keep = {_phrase_cache_path(t).name for t in wanted}
        for text in wanted:
            target = _phrase_cache_path(text)
            while not target.exists():
                try:
                    audio = _synthesize(text)
                except Exception as exc:  # noqa: BLE001
                    log(f"phrase render failed ({exc!r}), retrying")
                    time.sleep(15)
                    continue
                GREETING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(".tmp")
                tmp.write_bytes(audio)
                tmp.replace(target)
        # Drop renders of old voices / removed phrases.
        for old in GREETING_CACHE_DIR.glob("phrase-*.wav"):
            if old.name not in keep:
                old.unlink(missing_ok=True)
        for old in GREETING_CACHE_DIR.glob("greeting-*.wav"):
            old.unlink(missing_ok=True)
        log(f"phrase cache ready ({len(wanted)} phrases)")
    finally:
        _phrase_lock.release()


_last_phrase: dict[str, str] = {}


def say_phrase(kind: str, *more_kinds: str) -> bool:
    """Play a random cached phrase of this kind (never the same one twice in
    a row). Blocking. Returns False if none is cached yet."""
    options = [t for k in (kind, *more_kinds) for t in PHRASES.get(k, []) if _phrase_cache_path(t).exists()]
    if len(options) > 1 and _last_phrase.get(kind) in options:
        options.remove(_last_phrase[kind])
    if not options:
        return False
    text = random.choice(options)
    _last_phrase[kind] = text
    subprocess.run(["afplay", str(_phrase_cache_path(text))], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def play_greeting() -> None:
    """Blocking on purpose: the mic shouldn't start recording until JARVIS
    has finished saying hello, or it would transcribe its own voice."""
    hour = time.localtime().tm_hour
    time_kind = "greeting_morning" if 5 <= hour < 12 else "greeting_evening" if 18 <= hour < 24 else None
    if say_phrase("greeting", time_kind) if time_kind else say_phrase("greeting"):
        return
    subprocess.run(["say", PHRASES["greeting"][0]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=warm_phrase_cache, daemon=True).start()


def say_text(text: str) -> None:
    """Speak a specific line: the pre-rendered recording when there is one
    (quick replies), otherwise synthesize it now."""
    cached = _phrase_cache_path(text)
    if cached.exists():
        push_panel_state("speaking", text)
        subprocess.run(["afplay", str(cached)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        speak(text)


class Feedback:
    """Keeps a wait from being dead air. Call tick() every ~0.3s while
    something slow runs; it decides whether to say a thinking line, a
    "still on it" line, or play a soft tone, based on time since JARVIS
    last made a sound."""

    def __init__(self, spoke_filler: bool = False) -> None:
        self.last_sound = time.monotonic()
        self.spoke_filler = spoke_filler
        self.stills = 0

    def tick(self) -> None:
        quiet = time.monotonic() - self.last_sound
        if not self.spoke_filler and quiet >= THINK_AFTER_SECONDS:
            self.spoke_filler = True
            say_phrase("think")
        elif self.spoke_filler and self.stills < 1 and quiet >= STILL_AFTER_SECONDS:
            self.stills += 1
            say_phrase("still")
        elif self.spoke_filler and quiet >= TONE_EVERY_SECONDS and (self.stills >= 1 or quiet >= STILL_AFTER_SECONDS):
            subprocess.run(["afplay", "-v", "0.25", WAIT_TONE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            return
        self.last_sound = time.monotonic()


def run_with_feedback(fn, feedback: "Feedback"):  # noqa: ANN001, ANN201
    """Run fn() in a worker thread; keep the conversation alive meanwhile."""
    result: dict = {}

    def work() -> None:
        try:
            result["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(0.3)
        if worker.is_alive():
            feedback.tick()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def play_cue(path: str) -> None:
    subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify(title: str, body: str) -> None:
    script = f'display notification {json.dumps(body)} with title {json.dumps(title)}'
    subprocess.run(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def clean_for_speech(text: str) -> str:
    """Drop markdown markers and emoji so TTS doesn't read them out."""
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _speech_chunks(text: str, min_chars: int = 60) -> list[str]:
    """Split into sentence groups of at least ~min_chars, so the first chunk
    is short enough to synthesize quickly but not a choppy two-word clip."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    for sentence in sentences:
        if chunks and len(chunks[-1]) < min_chars:
            chunks[-1] += " " + sentence
        else:
            chunks.append(sentence)
    return [c for c in chunks if c.strip()]


_barged = threading.Event()
_wake_model = None  # set in main(): the wake model + its (still open) mic queue,
_audio_q = None     # reused while JARVIS talks to catch "hey Jarvis" barge-ins


class BargeInWatch:
    """While JARVIS talks, keep scoring the mic for "hey Jarvis"; if heard,
    set _barged so playback (and generation) stop."""

    def __enter__(self) -> "BargeInWatch":
        _barged.clear()
        self._stop = threading.Event()
        self._thread = None
        if _wake_model is not None and _audio_q is not None:
            try:
                while True:
                    _audio_q.get_nowait()  # only listen from now on
            except queue.Empty:
                pass
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                frame = _audio_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if _wake_model.predict(frame).get("hey_jarvis", 0.0) >= BARGE_IN_SCORE:
                log("barge-in: 'hey Jarvis' while speaking -> stop and listen")
                _barged.set()
                return

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            _wake_model.reset()


def _play(args: list[str]) -> bool:
    """Play (afplay/say) until done or barged in. False if interrupted."""
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while proc.poll() is None:
        if _barged.is_set():
            proc.kill()
            return False
        time.sleep(0.05)
    return True


def _play_wav_bytes(audio: bytes) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio)
        wav_path = tmp.name
    try:
        return _play(["afplay", wav_path])
    finally:
        os.unlink(wav_path)


_BRACKETED = re.compile(r"\[[^\]]*\]")
# Stock sign-offs the small model tacks onto almost every reply -- dropped
# so answers end when the answer does.
_FILLER_SENTENCE = re.compile(
    r"^(what would you like (to do|me to do) next|what do you think|what's your take|"
    r"(is there )?anything else (i can help (you )?with|you need)|let me know if you need anything( else)?|"
    r"how can i help( you)?( further)?|"
    r"what('?s| is) (one|the one) thing you('?d| would) like to [\w ]+|what are you (working on|up to)( today| right now)?|"
    r"(is there|do you have) (anything|something) (specific|in particular)[\w ,']*|would you like (me )?to [\w ,']*)\W*$",
    re.I,
)


def _render(sentence: str) -> tuple[str, object, str] | None:
    # Small models sometimes echo the bracketed context header -- never say it.
    sentence = _BRACKETED.sub("", sentence).strip()
    if _FILLER_SENTENCE.match(sentence):
        return None
    clean = clean_for_speech(sentence)
    if not clean:
        return None
    try:
        return ("wav", _synthesize(clean), sentence)
    except Exception as exc:  # noqa: BLE001 -- a TTS error shouldn't kill the listener
        log(f"Piper speak failed ({exc!r}), falling back to say")
        return ("say", clean, sentence)


def _play_queue(ready: "queue.Queue", feedback: "Feedback | None") -> str:
    """Play rendered sentences as they arrive, keeping the wait alive until
    the first one, updating the HUD as it goes. Returns what was spoken."""
    spoken: list[str] = []
    first = True
    with BargeInWatch():
        while True:
            try:
                item = ready.get(timeout=0.3)
            except queue.Empty:
                if first and feedback is not None:
                    feedback.tick()
                if _barged.is_set():
                    break
                continue
            if item is None or _barged.is_set():
                break
            first = False
            kind, payload, sentence = item
            if kind == "error":
                raise payload  # type: ignore[misc]
            spoken.append(sentence)
            push_panel_state("speaking", " ".join(spoken))
            ok = _play_wav_bytes(payload) if kind == "wav" else _play(["say", str(payload)])  # type: ignore[arg-type]
            if not ok:
                break
    return " ".join(spoken)


def speak(text: str, audio_path: Path | None = None, feedback: "Feedback | None" = None) -> None:
    """Say a finished text: Piper renders sentence N+1 while N plays."""
    text = clean_for_speech(text)[:2500]
    if not text:
        return
    _barged.clear()
    push_panel_state("speaking", text)
    if audio_path is not None and audio_path.exists():
        with BargeInWatch():
            _play(["afplay", str(audio_path)])
        return
    ready: "queue.Queue" = queue.Queue()

    def produce() -> None:
        for chunk in _speech_chunks(text):
            if _barged.is_set():
                break
            item = _render(chunk)
            if item:
                ready.put(item)
        ready.put(None)

    threading.Thread(target=produce, daemon=True).start()
    _play_queue(ready, feedback)


def push_panel_state(stage: str, text: str | None = None, **extra: object) -> None:
    """Best-effort: push one pipeline stage to the pet app's HUD panel (see
    computer/pet/main.js + panel-renderer.js). Silently does nothing if the
    pet isn't running -- this is a nice-to-have visual, not a dependency of
    the actual reply, which always happens regardless."""
    screen_server.talk(stage, text)
    try:
        body = json.dumps({"stage": stage, "text": text, **extra}).encode("utf-8")
        req = urllib.request.Request(
            PANEL_STATE_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.5)
    except Exception:
        pass


def hud_step(label: str, status: str, detail: str = "") -> None:
    """skills.StepFn -> one row in the HUD's live step list."""
    push_panel_state("step", label, status=status, detail=detail)
    if status != "running":
        log(f"step: {label} [{status}] {detail}")


def speech_threshold() -> float:
    return max(float(SILENCE_RMS_THRESHOLD), _noise_floor_rms * SPEECH_OVER_NOISE_FACTOR)


def record_utterance(start_timeout: float) -> np.ndarray | None:
    """Wait up to `start_timeout` seconds for speech to begin, then record
    until trailing silence or RECORD_MAX_SECONDS of speech. Returns None if
    nobody started talking in time."""
    threshold = speech_threshold()
    chunk_seconds = FRAME_SAMPLES / SAMPLE_RATE
    preroll: list[np.ndarray] = []  # keep ~0.5s before onset so the first word isn't clipped
    chunks: list[np.ndarray] = []
    silence_run = 0.0
    speech_started_at: float | None = None
    started = time.monotonic()

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES) as stream:
        while True:
            data, _ = stream.read(FRAME_SAMPLES)
            frame = data[:, 0].copy()
            now = time.monotonic()
            loud = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2))) >= threshold

            if speech_started_at is None:
                preroll = (preroll + [frame])[-6:]
                if loud:
                    speech_started_at = now
                    chunks = preroll
                elif now - started >= start_timeout:
                    return None
                continue

            chunks.append(frame)
            silence_run = 0.0 if loud else silence_run + chunk_seconds
            spoken = now - speech_started_at
            if spoken >= RECORD_MAX_SECONDS:
                break
            if spoken >= RECORD_MIN_SECONDS and silence_run >= SILENCE_TRAIL_SECONDS:
                break

    return np.concatenate(chunks)


def save_wav(audio: np.ndarray, path: Path) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(audio.tobytes())


_whisper_server: subprocess.Popen | None = None


def start_whisper_server() -> None:
    """Start the resident whisper-server as a child of this process (unless
    one is already answering on the port, e.g. left from a previous run)."""
    global _whisper_server
    server_bin = WHISPER_NATIVE_BIN / "whisper-server"
    if not server_bin.exists():
        log("native whisper-server not built; using whisper-cli per call")
        return
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{WHISPER_SERVER_PORT}/", timeout=1)
        log("whisper-server already running, reusing it")
        return
    except Exception:  # noqa: BLE001
        pass
    _whisper_server = subprocess.Popen(
        [str(server_bin), "-m", str(WHISPER_MODEL), "--host", "127.0.0.1",
         "--port", str(WHISPER_SERVER_PORT), "-t", "4"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _children.append(_whisper_server)
    log(f"whisper-server started (pid {_whisper_server.pid})")


def _env_value(key: str, default: str = "") -> str:
    for line in LOCAL_MODEL_ENV_FILE.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return default


def start_llama_server() -> None:
    """Start the local model server as a child (unless one already answers
    on the port). mlock keeps macOS from compressing the model's memory --
    that was a 20x slowdown before."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{LLAMA_PORT}/health", timeout=1)
        log("model server already running, reusing it")
        return
    except Exception:  # noqa: BLE001
        pass
    model_file = JARVIS_DIR / "models" / (_env_value("JARVIS_MODEL_FILE") or LLAMA_MODEL_FILE.name)
    if not LLAMA_SERVER_BIN.exists() or not model_file.exists():
        log(f"FATAL: missing {LLAMA_SERVER_BIN if not LLAMA_SERVER_BIN.exists() else model_file}")
        return
    proc = subprocess.Popen(
        [str(LLAMA_SERVER_BIN), "--model", str(model_file), "--alias", "qwen3-4b-instruct",
         "--host", "127.0.0.1", "--port", str(LLAMA_PORT), "--api-key", _load_llama_server_api_key(),
         "--ctx-size", str(LLAMA_CONTEXT), "--parallel", "1",
         "--threads", _env_value("JARVIS_MODEL_THREADS", "6"),
         "--load-mode", "mmap+mlock", "--flash-attn", "on",
         "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--no-webui"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _children.append(proc)
    log(f"model server started ({model_file.name}, pid {proc.pid}, {LLAMA_CONTEXT} ctx)")


_children: list[subprocess.Popen] = []


def _stop_children(*_args: object) -> None:
    for child in _children:
        if child.poll() is None:
            child.terminate()
    for child in _children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()


def _transcribe_server(wav_path: Path, prompt: str) -> str:
    boundary = f"----jarvis{random.getrandbits(64):x}"
    parts = []
    for name, value in (("response_format", "json"), ("prompt", prompt)):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode() + wav_path.read_bytes() + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        WHISPER_SERVER_URL,
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get("text", "").strip()


def transcribe(wav_path: Path, prompt: str | None = None) -> str:
    # The prompt biases Whisper toward names it would otherwise mishear
    # (unusual project names get misheard otherwise).
    prompt = prompt or skills.load_sources().get("speech_vocabulary", "JARVIS")
    try:
        return _transcribe_server(wav_path, prompt)
    except Exception as exc:  # noqa: BLE001 -- server down: fall back to the CLI
        log(f"whisper-server unavailable ({exc!r}), using whisper-cli")
    cli = WHISPER_NATIVE_BIN / "whisper-cli"
    result = subprocess.run(
        [str(cli) if cli.exists() else "whisper-cli", "-m", str(WHISPER_MODEL),
         "-f", str(wav_path), "-np", "-nt", "--prompt", prompt],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # whisper-cli prints the transcript to stdout after its log lines; the
    # actual text is whatever's left after stripping known log prefixes.
    lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith(("load_", "read_audio", "whisper_", "system_info"))
    ]
    return " ".join(lines).strip()


def strip_non_speech(text: str) -> str:
    """Whisper labels noise/silence as [BLANK_AUDIO], (wind howling),
    [inaudible], >> etc. -- remove those so noise never reaches the agent."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)|>>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if sum(c.isalpha() for c in text) >= 2 else ""


_fast_history: list[dict] = []
_fast_last_used = 0.0


def _build_messages(text: str, context: str) -> list[dict]:
    global _fast_last_used
    now = time.time()
    if now - _fast_last_used > FAST_HISTORY_IDLE_RESET_SECONDS:
        _fast_history.clear()
    _fast_last_used = now
    header = f"[{time.strftime('%A %d %B, %H:%M')}"
    ambient = activity.now_line()
    if ambient:
        header += f" | {ambient}"
    header += "]"
    if context:
        header += f"\n[Live data: {context}]"
    remembered = smarts.memory_context(text)
    if remembered:
        header += f"\n[Things they told you before: {remembered}]"
    return [{"role": "system", "content": PERSONA}, *_fast_history,
            {"role": "user", "content": f"{header}\n{text}"}]


def speak_streamed(text: str, context: str, feedback: "Feedback", extra_tokens: int | None = None) -> str:
    """Ask the model and start talking as soon as its first sentence is
    written -- the rest is generated and rendered while that plays."""
    _barged.clear()  # a previous interruption must not cancel this answer
    messages = _build_messages(text, context)
    ready: "queue.Queue" = queue.Queue()
    full: list[str] = []
    stats: dict = {}

    def produce() -> None:
        buf = ""
        try:
            extra = extra_tokens if extra_tokens is not None else (60 if context else 0)
            for piece in skills.llm_stream(messages, max_tokens=FAST_MAX_TOKENS + extra,
                                           cancel=_barged.is_set, stats=stats):
                full.append(piece)
                buf += piece
                while (m := re.search(r"[.!?](?=\s)", buf)):
                    sentence, buf = buf[: m.end()].strip(), buf[m.end():]
                    if (item := _render(sentence)):
                        ready.put(item)
            if buf.strip() and not _barged.is_set() and (item := _render(buf.strip())):
                ready.put(item)
        except Exception as exc:  # noqa: BLE001
            ready.put(("error", exc, ""))
        ready.put(None)

    threading.Thread(target=produce, daemon=True).start()
    spoken = _play_queue(ready, feedback)
    reply = _BRACKETED.sub("", "".join(full)).strip() or spoken
    if stats:
        log(f"model: prompt {stats.get('prompt_n')} tok (+{stats.get('cache_n', 0)} cached), "
            f"gen {stats.get('predicted_n')} tok @ {stats.get('predicted_per_second', 0):.1f} tok/s")
    if reply:
        _fast_history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
        del _fast_history[: max(0, len(_fast_history) - 2 * FAST_HISTORY_TURNS)]
    return reply


def _briefing_reply() -> tuple[str, Path | None]:
    """Cached morning briefing if it's fresh (instant, with pre-rendered
    audio), otherwise build one live (a minute or two, steps on the HUD)."""
    cached = skills.load_briefing(max_age_hours=12)
    if cached:
        made = time.strftime("%H:%M", time.localtime(cached["generated_at"]))
        hud_step("Daily briefing", "done", f"prepared at {made}")
        return cached["text"], None  # spoken live with Piper (fast enough)
    hud_step("Daily briefing", "running", "building now (1-2 min)")
    data = skills.build_briefing(hud_step)
    hud_step("Daily briefing", "done", "ready")
    return data["text"], None


SCREEN_OPEN_TIMEOUT = 12       # s for the screen window to load and report in
SCREEN_LOADING_TIMEOUT = 90    # s it may spend building findings before starting
SCREEN_BRIEFING_MAX = 6 * 60   # s safety cap on one briefing
SCREEN_SILENT_LIMIT = 30       # s without a report from the screen = it's gone
_last_screen_briefing = -1e9   # monotonic time of the last briefing on the screen


def brief_on_wake() -> bool:
    cfg = screen_server.load_config()
    cooldown = float(cfg.get("wake_cooldown_minutes") or 0) * 60
    return bool(cfg.get("brief_on_wake")) and time.monotonic() - _last_screen_briefing >= cooldown


def present_briefing_on_screen() -> bool:
    """Show and speak the findings on the JARVIS screen (via the pet app),
    blocking until the briefing ends so the mic doesn't record it. "hey
    Jarvis" stops it. False if the screen couldn't be shown -- the caller
    then speaks the briefing the old way."""
    screen_server.set_phase("opening")
    if not screen_server.open_screen(autostart=True):
        return False
    push_panel_state("sleep")  # the screen takes over from the HUD card
    opened = time.monotonic()
    while screen_server.state()["phase"] != "briefing":
        phase = screen_server.state()["phase"]
        waited = time.monotonic() - opened
        if (phase == "opening" and waited > SCREEN_OPEN_TIMEOUT) or waited > SCREEN_LOADING_TIMEOUT:
            log(f"screen didn't start the briefing (phase {phase!r}), speaking it instead")
            return False
        time.sleep(0.2)
    log("briefing on the JARVIS screen")
    global _last_screen_briefing
    _last_screen_briefing = time.monotonic()
    with BargeInWatch():
        ends = time.monotonic() + SCREEN_BRIEFING_MAX
        while time.monotonic() < ends:
            if _barged.is_set():
                screen_server.send("stop")
                break
            current = screen_server.state()
            if current["phase"] != "briefing" or time.time() - current["updated"] > SCREEN_SILENT_LIMIT:
                break  # finished, closed, or the screen went away (pet quit)
            time.sleep(0.2)
    return True


def _gather_context(plan: dict, text: str) -> str:
    started = time.monotonic()
    context = skills.gather(plan, text, hud_step)
    hud_step("Thinking it through", "running", f"data in {time.monotonic() - started:.1f}s")
    return context


# Conversation state for quick replies: what JARVIS said last (for
# "say that again"), whether it was a real question from the model (then a
# bare "yes"/"okay" answers it instead of triggering small talk), and an
# open offer ("Want to hear your briefing?") that a "yes" accepts.
_last_reply: str | None = None
_last_reply_was_question = False
_pending_offer: str | None = None


def _remember_reply(reply: str, from_model: bool) -> None:
    global _last_reply, _last_reply_was_question
    _last_reply = reply
    _last_reply_was_question = from_model and reply.rstrip().endswith("?")


# Voice kill switch for the JARVIS screen ("close the screen", "minimize it").
_SCREEN_CLOSE = re.compile(r"\b(close|kill|dismiss|exit|shut( it)? down|turn off|get rid of|stop)\b.*\b(screen|briefing|report|update|it|that|this|display)\b"
                           r"|^\W*(close|dismiss|kill it|close it)\W*$", re.I)
_SCREEN_MINI = re.compile(r"\b(minimi[sz]e|shrink|hide|make (it|that|the screen) small(er)?|put (it|that) (in|to) the corner)\b", re.I)
_SCREEN_FULL = re.compile(r"\b(maximi[sz]e|full ?screen|bring (it|that|the screen) back|restore|make (it|that) big(ger)?|show (it|the screen) again)\b", re.I)


def screen_command(text: str) -> str | None:
    """Close / minimize / restore the screen if it's up and asked to."""
    if not screen_server.is_open():
        return None
    if _SCREEN_MINI.search(text):
        return "Minimized." if screen_server.set_mode("mini") else None
    if _SCREEN_FULL.search(text):
        return "Back on the big screen." if screen_server.set_mode("full") else None
    if _SCREEN_CLOSE.search(text):
        screen_server.close_screen()
        return "Screen closed."
    return None


def answer_one(audio: np.ndarray) -> str:
    """Transcribe + answer one recorded utterance. Returns "noise" (nothing
    was said), "answered", or "end" (e.g. "goodnight" -- stop listening)."""
    global _pending_offer
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    try:
        save_wav(audio, wav_path)
        raw_text = transcribe(wav_path)
    finally:
        wav_path.unlink(missing_ok=True)

    text = strip_non_speech(raw_text)
    if not text:
        log(f"no speech in transcript ({raw_text!r}), still listening")
        return "noise"

    if smarts.looks_like_background(text):
        log(f"sounds like music / a video, not you -- ignored: {text[:60]!r}")
        return "noise"

    log(f"heard: {text!r}")
    push_panel_state("heard", text)

    done = screen_command(text)
    if done:
        log(f"screen: {done}")
        say_text(done)
        _remember_reply(done, from_model=False)
        return "answered"

    # Everyday Mac tasks: music, volume, directions, screenshot... (before
    # the quick replies, so "stop the music" pauses it rather than ending
    # the conversation).
    done = mac_control.handle(text)
    if done:
        log(f"mac: {done}")
        say_text(done)
        _remember_reply(done, from_model=False)
        return "answered"

    # --- instant path: everyday talk from config/quick_replies.json ---------
    offer, _pending_offer = _pending_offer, None
    forced_plan = None
    if offer and quick.is_no(text):
        say_text("No problem.")
        _remember_reply("No problem.", from_model=False)
        return "answered"
    if offer == "briefing" and quick.is_yes(text):
        forced_plan = {"skills": ["briefing"], "projects": None}
    else:
        intent = quick.match(text, after_question=_last_reply_was_question)
        if intent:
            started = time.monotonic()
            reply = quick.reply_for(intent, _last_reply)
            log(f"quick: {intent['name']} ({time.monotonic() - started:.1f}s) -> {reply!r}")
            if reply:
                say_text(reply)
            if intent.get("name") != "repeat":
                _remember_reply(reply, from_model=False)
            _pending_offer = intent.get("offer")
            return "end" if intent.get("end") else "answered"
    # --- instant path 2: memory, reminders, timers, goals (smarts.py) ------
    if not forced_plan:
        try:
            smart = smarts.handle(text)
        except Exception as exc:  # noqa: BLE001 -- never lose the question over a helper
            log(f"smarts failed: {exc!r}")
            smart = None
        if smart:
            log(f"smart: {smart!r}")
            say_text(smart)
            _remember_reply(smart, from_model=False)
            return "answered"
    notify("JARVIS heard you", text)
    push_panel_state("thinking", text)

    started = time.monotonic()
    audio_path: Path | None = None
    feedback = Feedback()
    plan = forced_plan or skills.route(text, skills.load_sources())
    log(f"plan: {plan}")
    # Say what we're about to do right away when a lookup is needed
    # ("Let me check the weather for you."); plain chat gets a thinking
    # line only if the answer takes more than a few seconds.
    if plan["skills"]:
        kind = plan["skills"][0]
        if kind == "briefing" and not skills.load_briefing(max_age_hours=12) and not findings.load():
            kind = "briefing_build"
        feedback.spoke_filler = say_phrase(kind)
        feedback.last_sound = time.monotonic()
    try:
        if plan["skills"] == ["briefing"] and present_briefing_on_screen():
            reply = "Briefing presented on the JARVIS screen."
        elif plan["skills"] == ["briefing"]:
            reply, audio_path = run_with_feedback(_briefing_reply, feedback)
            speak(reply, audio_path, feedback)
        else:
            context = run_with_feedback(lambda: _gather_context(plan, text), feedback) if plan["skills"] else ""
            report = bool(set(plan["skills"]) & ACTION_SKILLS)
            reply = speak_streamed(text, context, feedback, REPORT_EXTRA_TOKENS if report else None)
            if context:
                hud_step("Thinking it through", "done", f"{time.monotonic() - started:.0f}s")
        if not reply:
            raise RuntimeError("empty reply")
    except Exception as exc:  # noqa: BLE001
        log(f"answer failed: {exc!r}")
        push_panel_state("error", str(exc)[:160])
        # Never read raw error text aloud -- the log carries the details.
        speak("Sorry, I couldn't get an answer just now.")
        return "answered"
    log(f"reply ({time.monotonic() - started:.1f}s): {reply!r}")
    notify("JARVIS", reply[:200])
    _remember_reply(reply, from_model=True)
    return "barged" if _barged.is_set() else "answered"


_typed_lock = threading.Lock()  # one typed question at a time (the model serves one request)


def answer_text(text: str, emit, speak_it: bool = False) -> None:
    """Typed questions (the chat window, via screen_server POST /ask): the
    same brain as answer_one -- instant layers first, then route + live data
    + the model -- but streamed back as text instead of spoken, so it's
    quicker (no speech recognition, no voice rendering).
    emit({"type": "status"|"token"|"done", ...})."""
    global _pending_offer
    text = text.strip()[:1000]
    if not text:
        emit({"type": "done", "text": ""})
        return
    with _typed_lock:
        started = time.monotonic()
        log(f"typed: {text!r}")

        def finish(reply: str, source: str) -> None:
            reply = reply.strip()
            emit({"type": "done", "text": reply, "source": source, "seconds": round(time.monotonic() - started, 1)})
            log(f"typed reply ({source}, {time.monotonic() - started:.1f}s): {reply[:120]!r}")
            if speak_it and reply:
                threading.Thread(target=_say_reminder, args=(clean_for_speech(reply),), daemon=True).start()

        # Instant layers, same order as by voice.
        for label, fn in (("screen", screen_command), ("mac", mac_control.handle)):
            done = fn(text)
            if done:
                _remember_reply(done, from_model=False)
                return finish(done, label)
        offer, _pending_offer = _pending_offer, None
        forced_plan = None
        if offer == "briefing" and quick.is_yes(text):
            forced_plan = {"skills": ["briefing"], "projects": None}
        else:
            intent = quick.match(text, after_question=_last_reply_was_question)
            if intent:
                reply = quick.reply_for(intent, _last_reply) or "Done."
                if intent.get("name") != "repeat":
                    _remember_reply(reply, from_model=False)
                _pending_offer = intent.get("offer")
                return finish(reply, "quick")
            try:
                smart = smarts.handle(text)
            except Exception as exc:  # noqa: BLE001
                log(f"smarts failed: {exc!r}")
                smart = None
            if smart:
                _remember_reply(smart, from_model=False)
                return finish(smart, "smart")

        plan = forced_plan or skills.route(text, skills.load_sources())
        if plan["skills"] == ["briefing"]:
            if screen_server.open_screen(autostart=True):
                return finish("Your briefing is on the screen now.", "briefing")
            data = skills.load_briefing(max_age_hours=12)
            return finish(data["text"] if data else "Your briefing isn't ready yet -- ask me again in a minute.", "briefing")

        def step(label: str, status: str, detail: str = "") -> None:
            if status == "running":
                emit({"type": "status", "text": label})

        context = ""
        if plan["skills"]:
            emit({"type": "status", "text": "Looking that up…"})
            context = skills.gather(plan, text, step)
        emit({"type": "status", "text": "Thinking…"})
        report = bool(set(plan["skills"]) & ACTION_SKILLS)
        messages = _build_messages(text, context)
        pieces: list[str] = []
        shown = ""
        try:
            for piece in skills.llm_stream(messages, max_tokens=FAST_MAX_TOKENS + (REPORT_EXTRA_TOKENS if report else 60 if context else 40),
                                           cancel=lambda: False, stats={}):
                pieces.append(piece)
                visible = _BRACKETED.sub("", "".join(pieces))
                if visible.lstrip().startswith("["):
                    continue  # an echoed [header] that isn't closed yet -- don't show it
                visible = visible.lstrip()
                if len(visible) > len(shown):
                    emit({"type": "token", "text": visible[len(shown):]})
                    shown = visible
        except Exception as exc:  # noqa: BLE001
            log(f"typed answer failed: {exc!r}")
            return finish("Sorry, I couldn't get an answer just now -- is the model still loading?", "error")
        # Final text: drop stock sign-offs ("What would you like to do next?").
        sentences = re.split(r"(?<=[.!?])\s+", _BRACKETED.sub("", "".join(pieces)).strip())
        reply = " ".join(x for x in sentences if not _FILLER_SENTENCE.match(x.strip())).strip() or shown.strip()
        _fast_history.extend([{"role": "user", "content": text}, {"role": "assistant", "content": reply}])
        del _fast_history[: max(0, len(_fast_history) - 2 * FAST_HISTORY_TURNS)]
        _remember_reply(reply, from_model=True)
        finish(reply, "model")


def handle_wake(skip_greeting: bool = False) -> None:
    """Greet, then stay in a conversation: keep taking questions (no need to
    say "hey Jarvis" again) until CONVERSATION_IDLE_SECONDS pass with no
    speech, then go back to wake-word listening."""
    global _in_conversation
    _in_conversation = True
    smarts.duck_music()  # music down while we talk (JARVIS hears you better too)
    try:
        _conversation(skip_greeting)
    finally:
        _in_conversation = False
        smarts.restore_music()


INTRO_STAMP = JARVIS_DIR / "database" / "pet" / "intro-boot.txt"  # also written by the pet's boot intro
INTRO_SECONDS = 9.5  # the intro plays ~5 s, greets (via our /say), then fades


def _boot_time() -> str:
    """This boot's identity: kern.boottime seconds."""
    out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
    m = re.search(r"sec = (\d+)", out)
    return m.group(1) if m else ""


def intro_after_reboot() -> bool:
    """First "hey Jarvis" since the Mac started: play the boot intro on every
    display (via the pet) instead of the usual hello. False if it already
    played this boot, or the pet isn't running."""
    boot = _boot_time()
    try:
        seen = INTRO_STAMP.read_text().strip()
    except OSError:
        seen = ""
    if not boot or seen == boot:
        return False
    try:
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8092/boot", data=b"{}", method="POST"), timeout=2)
    except Exception:  # noqa: BLE001
        return False
    INTRO_STAMP.parent.mkdir(parents=True, exist_ok=True)
    INTRO_STAMP.write_text(boot)
    log("first wake since the Mac started: boot intro")
    time.sleep(INTRO_SECONDS)
    return True


_force_listen = threading.Event()  # set by POST /listen (the boot key): converse without the wake word
_mic_ready = False


def request_listen() -> bool:
    if not _mic_ready:
        return False
    _force_listen.set()
    return True


def _conversation(skip_greeting: bool = False) -> None:
    log(f"wake word detected (speech threshold {speech_threshold():.0f})")
    # The screen's briefing opens with its own greeting; the usual spoken
    # hello is the fallback when it's off or can't be shown (pet not running).
    if skip_greeting:
        # Boot key: the boot intro already greeted; let it finish, then listen.
        time.sleep(INTRO_SECONDS)
        push_panel_state("listening")
    elif intro_after_reboot():
        push_panel_state("listening")
    elif brief_on_wake() and present_briefing_on_screen():
        push_panel_state("listening")
        if _barged.is_set():
            play_cue(LISTEN_CUE)  # interrupted: "yes? go ahead"
    else:
        push_panel_state("listening")
        play_greeting()

    deadline = time.monotonic() + CONVERSATION_IDLE_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        audio = record_utterance(start_timeout=remaining)
        if audio is None:
            break
        log(f"recorded {len(audio) / SAMPLE_RATE:.1f}s")
        play_cue(LISTEN_CUE)
        outcome = answer_one(audio)
        if outcome == "end":
            # "Goodnight" / "go to sleep" / "that's all": JARVIS already said
            # goodbye, so no sleep chime.
            log("conversation ended by request")
            push_panel_state("sleep")
            return
        if outcome in ("answered", "barged"):
            if outcome == "barged":
                play_cue(LISTEN_CUE)  # cut off mid-sentence: "yes? go ahead"
            # Fresh minute after every answer.
            deadline = time.monotonic() + CONVERSATION_IDLE_SECONDS
            push_panel_state("listening")

    log("no speech for a minute, going back to sleep")
    push_panel_state("sleep")
    play_cue(SLEEP_CUE)


_in_conversation = False


FINDINGS_REFRESH_MINUTES = 30


def _say_reminder(line: str) -> None:
    """Reminders speak on their own thread (tts directly -- the conversation's
    playback/barge-in machinery belongs to the listening thread)."""
    try:
        tts.play(tts.synthesize(line))
    except Exception as exc:  # noqa: BLE001
        log(f"reminder speech failed: {exc!r}")


def _reminder_card(reminder: dict, line: str) -> None:
    """A Done / Snooze card by the pet (computer/pet/reminders.js)."""
    try:
        body = json.dumps({"id": reminder["id"], "text": reminder["text"], "kind": reminder.get("kind", "reminder")}).encode()
        req = urllib.request.Request("http://127.0.0.1:8092/reminder/card", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2)
    except Exception:  # noqa: BLE001 -- pet not running: the spoken line + notification still happen
        pass
    log(f"reminder: {line}")


def _business_card(card: dict) -> None:
    """Something happened on one of your sites: a card by the pet."""
    log(f"business: {card['title']} -- {card['text']}")
    try:
        req = urllib.request.Request("http://127.0.0.1:8092/notify/card", data=json.dumps(card).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2)
    except Exception:  # noqa: BLE001 -- pet not running: the briefing still has it
        notify(card["title"], card["text"])


def keep_awake_loop() -> None:
    """Idle sleep would pause everything, the mic included. While plugged in
    (config/voice.json "keep_awake": "plugged_in", the default), hold a
    `caffeinate -i` so the Mac doesn't idle-sleep -- the display still turns
    off as usual. "always" also on battery; "off" never. Closing the lid
    sleeps the Mac regardless."""
    proc: subprocess.Popen | None = None
    while True:
        try:
            mode = json.loads(tts.VOICE_CONFIG_FILE.read_text()).get("keep_awake", "plugged_in")
        except (OSError, ValueError):
            mode = "plugged_in"
        plugged = "AC Power" in subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
        want = mode == "always" or (mode == "plugged_in" and plugged)
        if want and (proc is None or proc.poll() is not None):
            proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            log("keeping the Mac awake for 'hey Jarvis' (display can still sleep)")
        elif not want and proc is not None and proc.poll() is None:
            proc.terminate()
            proc = None
            log("on battery: normal sleep allowed")
        time.sleep(60)


def briefing_scheduler() -> None:
    """Background: make sure today's briefing exists once it's past
    BRIEFING_HOUR:MINUTE (or at startup, if later than that). Waits while a
    conversation is running -- the model serves one request at a time."""
    time.sleep(90)  # let the model server finish loading first
    while True:
        try:
            now = time.localtime()
            today = time.strftime("%Y-%m-%d", now)
            cached = skills.load_briefing(max_age_hours=24)
            past_time = (now.tm_hour, now.tm_min) >= (BRIEFING_HOUR, BRIEFING_MINUTE)
            if past_time and not _in_conversation and (not cached or cached.get("generated_for") != today):
                log("preparing today's briefing...")
                data = skills.build_briefing()
                log(f"briefing ready ({len(data['text'])} chars)")
                screen_server.refresh_findings()
                notify("JARVIS: daily briefing ready", 'Say "hey Jarvis, give me my briefing".')
            # Keep the screen's findings current so a wake presents fresh
            # data without waiting on Mail.app (up to 45s).
            recent = findings.load(max_age_hours=FINDINGS_REFRESH_MINUTES / 60)
            if not recent and not _in_conversation and activity.idle_seconds() < 15 * 60:
                screen_server.refresh_findings()
        except Exception as exc:  # noqa: BLE001
            log(f"briefing failed: {exc!r}")
        time.sleep(60)


def warm_model() -> None:
    """Once the model server is up, process the persona once so the first
    real question only has to read its own few tokens."""
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{LLAMA_PORT}/health", timeout=2)
            break
        except Exception:  # noqa: BLE001 -- still loading
            time.sleep(3)
    try:
        list(skills.llm_stream([{"role": "system", "content": PERSONA},
                                {"role": "user", "content": "[warm-up]\nhi"}], max_tokens=1))
        log("model warmed up (persona cached)")
    except Exception as exc:  # noqa: BLE001
        log(f"model warm-up failed: {exc!r}")


def screen_locked() -> bool:
    """True while the Mac's screen is locked (macOS only lists the
    CGSSessionScreenIsLocked key in the console session while it is)."""
    try:
        out = subprocess.run(["ioreg", "-n", "Root", "-d1", "-a"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "CGSSessionScreenIsLocked" in out


def confirm_wake(frames: list[np.ndarray]) -> bool:
    """Whisper check of recent audio for an unsure detection. Hinted with
    "Hey Jarvis." -- tested: real "hey Jarvis" in 3 voices over room noise
    all came back right, while noise came back [BLANK_AUDIO] and unrelated
    speech ("Hey, pass me the glass") stayed as spoken."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    try:
        save_wav(np.concatenate(frames), wav_path)
        text = transcribe(wav_path, prompt="Wake up, Jarvis.")
    finally:
        wav_path.unlink(missing_ok=True)
    ok = bool(CONFIRM_PATTERN.search(text))
    log(f"second opinion: heard {text!r} -> {'wake' if ok else 'ignore'}")
    return ok


def main() -> None:
    if not WHISPER_MODEL.exists():
        log(f"FATAL: missing Whisper model at {WHISPER_MODEL}")
        sys.exit(1)

    # launchd stops us with SIGTERM; turn it into a normal exit so the child
    # servers (model, whisper) are shut down with us, not orphaned.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    import atexit
    atexit.register(_stop_children)
    start_llama_server()
    start_whisper_server()
    threading.Thread(target=briefing_scheduler, daemon=True).start()
    threading.Thread(target=keep_awake_loop, daemon=True).start()
    threading.Thread(target=business.poll_loop, args=(_business_card,), daemon=True).start()
    screen_server.ask_handler = answer_text  # the chat window (POST /ask)
    screen_server.listen_handler = request_listen
    screen_server.start()
    threading.Thread(target=smarts.reminder_loop, args=(_say_reminder, _reminder_card, notify), daemon=True).start()
    log("loading wake-word model...")
    model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    threading.Thread(target=warm_phrase_cache, daemon=True).start()
    log("ready, listening for 'wake up Jarvis'")
    global _mic_ready
    _mic_ready = True

    audio_q: "queue.Queue[np.ndarray]" = queue.Queue()
    global _wake_model, _audio_q
    _wake_model, _audio_q = model, audio_q
    activity.start()
    threading.Thread(target=warm_model, daemon=True).start()

    def callback(indata, frames, time_info, status):  # noqa: ANN001
        if status:
            log(f"stream status: {status}")
        audio_q.put(indata[:, 0].copy())

    global _noise_floor_rms
    recent_rms: list[float] = []  # ~10s of frame levels, for the noise floor
    peak_score = 0.0
    last_heartbeat = time.monotonic()
    last_near_miss = 0.0
    last_trigger = 0.0
    recent_audio: list[np.ndarray] = []  # last CONFIRM_WINDOW_SECONDS of raw frames
    window_frames = int(CONFIRM_WINDOW_SECONDS * SAMPLE_RATE / FRAME_SAMPLES)
    confirm_at: float | None = None
    unsure_peak = 0.0
    lock_state = {"at": 0.0, "locked": False}

    def now_locked(now: float) -> bool:
        """Not listening while the screen is locked (checked every 2s)."""
        if now - lock_state["at"] >= 2.0:
            lock_state["at"] = now
            locked = screen_locked()
            if locked != lock_state["locked"]:
                log("screen locked: not listening" if locked else "screen unlocked: listening again")
                model.reset()
            lock_state["locked"] = locked
        return lock_state["locked"]

    while True:  # the mic stream is reopened if it goes silent (Mac woke from sleep, mic changed)
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=FRAME_SAMPLES,
                callback=callback,
            ):
                while True:
                    frame = audio_q.get(timeout=MIC_STALL_SECONDS)
                    if now_locked(time.monotonic()):
                        confirm_at, unsure_peak = None, 0.0
                        continue
                    scores = model.predict(frame)
                    score = scores.get("hey_jarvis", 0.0)
                    now = time.monotonic()
                    recent_audio = (recent_audio + [frame])[-window_frames:]

                    recent_rms = (recent_rms + [float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))])[-125:]
                    if len(recent_rms) >= 25:
                        _noise_floor_rms = float(np.percentile(recent_rms, 30))
                    peak_score = max(peak_score, score)
                    if NEAR_MISS_LOG_SCORE <= score < DETECTION_THRESHOLD and now - last_near_miss >= 1.0:
                        last_near_miss = now
                        log(f"near miss: wake-word score {score:.2f} (needs {CONFIRM_MIN_SCORE}+)")
                    if now - last_heartbeat >= HEARTBEAT_SECONDS:
                        log(f"alive: mic noise floor {_noise_floor_rms:.0f}, peak wake score {peak_score:.2f} in last {HEARTBEAT_SECONDS // 60} min")
                        peak_score = 0.0
                        last_heartbeat = now
                    wake = False
                    forced = _force_listen.is_set()
                    if forced:
                        _force_listen.clear()
                        wake = True
                    if not forced and (now - last_trigger) >= COOLDOWN_SECONDS:
                        if score >= CONFIRM_MIN_SCORE and confirm_at is None:
                            confirm_at = now + CONFIRM_TAIL_SECONDS
                        unsure_peak = max(unsure_peak, score) if confirm_at else 0.0
                        if not wake and confirm_at is not None and now >= confirm_at:
                            log(f"unsure detection (score {unsure_peak:.2f}), checking with Whisper")
                            wake = confirm_wake(recent_audio)
                            confirm_at, unsure_peak = None, 0.0
                            if not wake:
                                last_trigger = now - COOLDOWN_SECONDS + 1.0  # brief pause before re-checking
                    if wake:
                        confirm_at, unsure_peak = None, 0.0
                        last_trigger = now
                        try:
                            handle_wake(skip_greeting=forced)
                        except Exception as exc:  # noqa: BLE001
                            log(f"error handling wake: {exc!r}")
                        # Drop audio queued while handling (JARVIS's own speech, a
                        # multi-minute wait for the reply) so the backlog isn't
                        # scored as fresh wake-word input.
                        try:
                            while True:
                                audio_q.get_nowait()
                        except queue.Empty:
                            pass
                        model.reset()
                        recent_audio = []

        except queue.Empty:
            log(f"mic silent for {MIC_STALL_SECONDS}s (Mac slept or the mic changed) -- reopening it")
        except sd.PortAudioError as exc:
            log(f"mic error ({exc}) -- reopening it")
            time.sleep(2)
        # Re-scan audio devices so a reconnected / default-changed mic is used.
        try:
            sd._terminate()
            sd._initialize()
        except Exception:  # noqa: BLE001
            pass
        model.reset()


if __name__ == "__main__":
    main()
