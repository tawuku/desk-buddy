#!/usr/bin/env python3
"""
Build today's daily briefing by hand. Normally not needed: the JARVIS app
(wake_listener.py's briefing_scheduler) prepares it every morning at 07:30,
or at startup if that time has passed. The text is cached in
computer/voice/cache/briefing.json and spoken live with Piper when asked
for (fast enough that no audio is pre-rendered anymore).

  python3 briefing.py           # build if today's is missing
  python3 briefing.py --force   # rebuild now

Needs JARVIS running (it uses the app's local model server).
"""
from __future__ import annotations

import sys
import urllib.request
from datetime import datetime

import skills


def log(msg: str) -> None:
    print(f"[briefing {datetime.now():%H:%M:%S}] {msg}", flush=True)


def main() -> int:
    force = "--force" in sys.argv
    today = datetime.now().strftime("%Y-%m-%d")
    cached = skills.load_briefing(max_age_hours=24)
    if cached and cached.get("generated_for") == today and not force:
        log("today's briefing already prepared, nothing to do")
        return 0
    try:
        urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3)
    except Exception:  # noqa: BLE001
        log("model server isn't running -- turn JARVIS on in PA first")
        return 1
    data = skills.build_briefing(lambda label, status, detail="": status != "running" and log(f"{label} [{status}] {detail}"))
    log(f"briefing ({len(data['text'])} chars): {data['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
