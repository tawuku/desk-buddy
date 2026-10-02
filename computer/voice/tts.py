#!/usr/bin/env python3
"""
JARVIS's voice: Piper, a small English neural TTS that runs in-process.

Replaces the XTTS-v2 server (computer/tts/, ~2.3GB RAM, ~3x *slower* than
real time on this Intel Mac). Piper renders ~5x *faster* than real time from
a ~60MB voice model, so there's no server to keep warm.

Voices are .onnx files in models/piper/ (with their .onnx.json configs);
the selected voice and speed live in config/voice.json (PA's Voice card
edits it; changes apply on the next sentence, no restart).

CLI (used by PA's Preview button):
  python3 tts.py --list                          # JSON: voices + current config
  python3 tts.py [--voice NAME] [--speed X] "text"   # speak it now
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import threading
import urllib.request
import wave
from pathlib import Path

import remote

JARVIS_DIR = Path(__file__).resolve().parent.parent.parent
PIPER_DIR = JARVIS_DIR / "models" / "piper"
VOICE_CONFIG_FILE = JARVIS_DIR / "config" / "voice.json"
DEFAULT_CONFIG = {"voice": "en_GB-alan-medium", "speed": 1.0}

_voices: dict[str, object] = {}
_lock = threading.Lock()


def load_config() -> dict:
    try:
        return {**DEFAULT_CONFIG, **json.loads(VOICE_CONFIG_FILE.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)


def save_config(voice: str | None = None, speed: float | None = None) -> dict:
    cfg = load_config()
    if voice:
        cfg["voice"] = voice
    if speed:
        cfg["speed"] = round(float(speed), 2)
    VOICE_CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")
    return cfg


def available_voices() -> list[str]:
    if remote.enabled() and not PIPER_DIR.exists():
        return [load_config()["voice"]]
    return sorted(p.stem for p in PIPER_DIR.glob("*.onnx") if p.with_suffix(".onnx.json").exists())


def _voice(name: str):  # noqa: ANN202
    from piper import PiperVoice  # deferred: ~1s import, only when speaking

    with _lock:
        if name not in _voices:
            _voices[name] = PiperVoice.load(str(PIPER_DIR / f"{name}.onnx"))
        return _voices[name]


def synthesize(text: str, voice: str | None = None, speed: float | None = None) -> bytes:
    """WAV bytes for text. speed > 1 talks faster (Piper's length_scale is
    the inverse: duration multiplier)."""
    cfg = load_config()
    if remote.enabled():  # the heavy lifting happens on the remote brain
        req = urllib.request.Request(
            remote.url("/tts"), method="POST",
            data=json.dumps({"text": text, "voice": voice, "speed": speed or cfg.get("speed")}).encode(),
            headers={"Content-Type": "application/json", **remote.headers()})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    from piper.config import SynthesisConfig

    name = voice or cfg["voice"]
    if name not in available_voices():
        name = DEFAULT_CONFIG["voice"]
    rate = float(speed or cfg.get("speed") or 1.0)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        _voice(name).synthesize_wav(text, wav, syn_config=SynthesisConfig(length_scale=1.0 / max(0.5, min(rate, 2.0))))
    return buf.getvalue()


def play(audio: bytes) -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio)
        path = tmp.name
    try:
        subprocess.run(["afplay", path])
    finally:
        Path(path).unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if "--list" in argv:
        print(json.dumps({"voices": available_voices(), "config": load_config()}))
        return 0
    voice = speed = None
    words = []
    it = iter(argv)
    for arg in it:
        if arg == "--voice":
            voice = next(it, None)
        elif arg == "--speed":
            speed = float(next(it, "1.0"))
        else:
            words.append(arg)
    text = " ".join(words).strip() or "Hey there, what's up?"
    play(synthesize(text, voice, speed))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
