"""
Remote brain: run JARVIS's heavy parts (the language model, Whisper speech
recognition and the Piper voice) on another computer -- see computer/server/.

config/remote.json (git-ignored; written by computer/server/connect.sh):
  {"host": "192.168.1.50", "port": 8090, "token": "..."}

With the file present, this Mac only records the mic, plays audio and shows
the pet/screen; with it absent (or "enabled": false) everything runs locally
exactly as before.
"""
from __future__ import annotations

import json
from pathlib import Path

REMOTE_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "remote.json"


def _cfg() -> dict:
    try:
        cfg = json.loads(REMOTE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return cfg if cfg.get("host") and cfg.get("enabled", True) else {}


def enabled() -> bool:
    return bool(_cfg())


def url(path: str) -> str:
    cfg = _cfg()
    return f"http://{cfg['host']}:{cfg.get('port', 8090)}{path}"


def headers() -> dict:
    """Auth header for the remote server ({} when running locally)."""
    token = _cfg().get("token")
    return {"Authorization": f"Bearer {token}"} if token else {}
