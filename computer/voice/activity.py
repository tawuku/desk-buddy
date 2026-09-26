"""
What's going on on this Mac -- so JARVIS can talk about your day.

A background thread samples every SAMPLE_SECONDS: the frontmost app (via
`lsappinfo`, no permission needed), its window title (System Events --
needs Accessibility; silently skipped if not granted), and the active tab
title when a browser is in front (needs one-time Automation permission per
browser; skipped if refused). Idle time (no keyboard/mouse for IDLE_SECONDS)
isn't counted. Changes are appended to cache/activity-YYYY-MM-DD.jsonl --
local only, never sent anywhere; files older than KEEP_DAYS are deleted.

Recently changed files come from Spotlight (`mdfind`), on demand.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent / "cache"
SAMPLE_SECONDS = 15
IDLE_SECONDS = 300
KEEP_DAYS = 7
HOME = Path.home()

_BROWSERS = {
    "Safari": 'tell application "Safari" to get name of current tab of front window',
    "Google Chrome": 'tell application "Google Chrome" to get title of active tab of front window',
    "Arc": 'tell application "Arc" to get title of active tab of front window',
    "Microsoft Edge": 'tell application "Microsoft Edge" to get title of active tab of front window',
    "Brave Browser": 'tell application "Brave Browser" to get title of active tab of front window',
}
_disabled: set[str] = set()  # probes that failed for lack of permission
_current: dict = {}
_lock = threading.Lock()


def _run(args: list[str], timeout: float = 3) -> str | None:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _osascript(key: str, script: str) -> str | None:
    if key in _disabled:
        return None
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        # -1719/-25211: no Accessibility; -1743: Automation refused. Stop
        # asking instead of retrying (and re-prompting) every 15 seconds.
        if re.search(r"-1719|-25211|-1743|not allowed", out.stderr):
            _disabled.add(key)
        return None
    return out.stdout.strip() or None


def idle_seconds() -> float:
    out = _run(["ioreg", "-c", "IOHIDSystem", "-d", "4"]) or ""
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(m.group(1)) / 1e9 if m else 0.0


def frontmost() -> dict:
    app = None
    asn = _run(["lsappinfo", "front"])
    if asn:
        info = _run(["lsappinfo", "info", "-only", "name", asn]) or ""
        m = re.search(r'"LSDisplayName"="([^"]*)"', info)
        app = m.group(1) if m else None
    title = None
    if app:
        title = _osascript(
            "window_title",
            'tell application "System Events" to tell (first application process whose frontmost is true) '
            'to get name of front window',
        )
        if app in _BROWSERS:
            tab = _osascript(f"browser:{app}", _BROWSERS[app])
            title = tab or title
    return {"app": app, "title": (title or "")[:140]}


def _log_path(day: date) -> Path:
    return CACHE_DIR / f"activity-{day.isoformat()}.jsonl"


def _prune() -> None:
    cutoff = date.today() - timedelta(days=KEEP_DAYS)
    for f in CACHE_DIR.glob("activity-*.jsonl"):
        try:
            if date.fromisoformat(f.stem.split("-", 1)[1]) < cutoff:
                f.unlink()
        except ValueError:
            continue


def monitor_loop() -> None:
    """Background thread: sample, append on change (and a heartbeat every
    few minutes so durations are measurable)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _prune()
    last_key, last_write = None, 0.0
    while True:
        try:
            idle = idle_seconds() >= IDLE_SECONDS
            now = frontmost() if not idle else {"app": "(away)", "title": ""}
            with _lock:
                _current.update(now, t=time.time())
            key = (now["app"], now["title"])
            if key != last_key or time.time() - last_write > 300:
                with _log_path(date.today()).open("a") as f:
                    f.write(json.dumps({"t": round(time.time()), **now}) + "\n")
                last_key, last_write = key, time.time()
        except Exception:  # noqa: BLE001 -- awareness is best-effort
            pass
        time.sleep(SAMPLE_SECONDS)


def start() -> None:
    threading.Thread(target=monitor_loop, daemon=True).start()


def _entries(day: date) -> list[dict]:
    try:
        return [json.loads(l) for l in _log_path(day).read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def now_line() -> str:
    """One short line for every prompt: what the user is looking at right now."""
    with _lock:
        cur = dict(_current)
    if not cur.get("app") or cur["app"] == "(away)":
        return ""
    return f"On screen: {cur['app']}" + (f" -- {cur['title']}" if cur.get("title") else "")


def today_summary(max_apps: int = 6, max_titles: int = 6) -> str:
    """Time per app today plus the most-seen window titles."""
    entries = _entries(date.today())
    if not entries:
        return "No activity recorded today yet."
    per_app: dict[str, float] = defaultdict(float)
    per_title: dict[str, float] = defaultdict(float)
    for cur, nxt in zip(entries, entries[1:] + [{"t": time.time()}]):
        span = min(nxt["t"] - cur["t"], 600)  # cap gaps (sleep, crashes)
        if cur.get("app") and cur["app"] != "(away)":
            per_app[cur["app"]] += span
            if cur.get("title"):
                per_title[f"{cur['app']}: {cur['title']}"] += span
    apps = sorted(per_app.items(), key=lambda kv: -kv[1])[:max_apps]
    titles = sorted(per_title.items(), key=lambda kv: -kv[1])[:max_titles]
    first = datetime.fromtimestamp(entries[0]["t"]).strftime("%H:%M")
    parts = [f"Active on the Mac since {first}. Time per app: " +
             ", ".join(f"{a} {m / 60:.0f} min" for a, m in apps) + "."]
    if titles:
        parts.append("Main windows: " + "; ".join(f"{t} ({m / 60:.0f} min)" for t, m in titles) + ".")
    return " ".join(parts)


_SKIP_PATH = re.compile(r"/(\.[^/]+|node_modules|venv|__pycache__|cache|DerivedData|build|_to_delete|_preview)(/|$)")
DEFAULT_FOLDERS = ["~/Documents", "~/Desktop", "~/Downloads"]


def recent_files(hours: float = 24, limit: int = 10, folders: list[str] | None = None) -> str:
    """Files you changed recently (Spotlight), newest first. Queries only the
    folders where work lives -- a whole-home query took ~17s (cloud storage,
    Library), these take well under a second each, run in parallel."""
    from concurrent.futures import ThreadPoolExecutor

    roots = [Path(f).expanduser() for f in (folders or DEFAULT_FOLDERS)]
    query = f"kMDItemFSContentChangeDate >= $time.now(-{int(hours * 3600)}) && kMDItemContentTypeTree != public.folder"

    def search(root: Path) -> list[str]:
        if not root.exists():
            return []
        return [l for l in (_run(["mdfind", "-onlyin", str(root), query], timeout=8) or "").splitlines()
                if not _SKIP_PATH.search(l[len(str(root)):])]

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = {l for chunk in pool.map(search, roots) for l in chunk}
    files = []
    for line in paths:
        if line.endswith((".DS_Store", ".log", ".jsonl", ".wav", ".tmp")):
            continue
        p = Path(line)
        try:
            files.append((p.stat().st_mtime, p))
        except OSError:
            continue
    files.sort(reverse=True)
    if not files:
        return f"No files changed in the last {hours:.0f} hours."
    shown = [f"{p.name} (in {p.parent.name}, {datetime.fromtimestamp(t).strftime('%H:%M')})" for t, p in files[:limit]]
    return f"Files changed in the last {hours:.0f}h ({len(files)} total), newest first: " + "; ".join(shown) + "."
