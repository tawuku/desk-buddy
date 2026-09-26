"""
Apple Health, via an iPhone Shortcut: the Shortcut (see computer/health/README.md)
saves a small text file a few times a day to iCloud Drive -> Shortcuts -> JARVIS:

    date: 2026-09-26
    steps: 8123
    active_kcal: 451
    exercise_min: 32
    sleep: 7 hr 5 min
    resting_hr: 58

This module reads those files (no network, nothing leaves the Mac) for the
briefing (findings.py HEALTH category) and voice answers ("how did I sleep?").
The pet reads the same files for its walk / sleep nudges (computer/pet/health.js).

Parsing is lenient on purpose: Shortcuts formats numbers by region ("8.123",
"7,5"), may add units ("8123 count", "451 kcal"), and reports durations in
seconds, minutes or "7 hr 5 min" depending on the action used.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path.home()
HEALTH_FOLDERS = [
    HOME / "Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/JARVIS",  # iCloud Drive > Shortcuts > JARVIS
    HOME / "Library/Mobile Documents/com~apple~CloudDocs/JARVIS",                        # iCloud Drive > JARVIS
    HOME / "Library/Mobile Documents/com~apple~CloudDocs/Shortcuts/JARVIS",
]
GOALS_FILE = Path(__file__).resolve().parent.parent.parent / "database" / "goals.json"
DEFAULTS = {"on": True, "stepGoal": 8000, "sleepGoalH": 7.5}
FIELDS = ("steps", "active_kcal", "exercise_min", "sleep_min", "resting_hr", "stand_h")
_ALIASES = {"steps": "steps", "step": "steps", "active_kcal": "active_kcal", "active energy": "active_kcal", "kcal": "active_kcal",
            "exercise_min": "exercise_min", "exercise": "exercise_min", "sleep": "sleep_min", "sleep_min": "sleep_min",
            "sleep_h": "sleep_min", "resting_hr": "resting_hr", "resting heart rate": "resting_hr", "stand_h": "stand_h", "stand": "stand_h"}


def settings() -> dict:
    try:
        return {**DEFAULTS, **json.loads(GOALS_FILE.read_text()).get("settings", {}).get("health", {})}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def parse_number(text: str) -> float | None:
    """'8.123' / '8,123' (grouping) -> 8123; '7,5' / '7.5' -> 7.5; units ignored."""
    m = re.search(r"\d[\d.,   ]*", text)
    if not m:
        return None
    tok = re.sub(r"[   ]", "", m.group(0)).rstrip(".,")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", tok):
        return float(re.sub(r"[.,]", "", tok))
    if re.search(r"[.,]\d{1,2}$", tok):
        whole, frac = re.split(r"[.,](?=\d{1,2}$)", tok)
        return float(re.sub(r"[.,]", "", whole) + "." + frac)
    return float(re.sub(r"[.,]", "", tok))


def parse_minutes(text: str, key: str) -> float | None:
    """Durations: '7 hr 5 min', '7:05', '425' (minutes), '25500' (seconds), '7.1' (hours)."""
    t = text.lower()
    h = re.search(r"(\d+(?:[.,]\d+)?)\s*(h|hr|hrs|hour|hours|std)\b", t)
    mnt = re.search(r"(\d+)\s*(m|min|mins|minutes)\b", t)
    if h or mnt:
        return (parse_number(h.group(1)) * 60 if h else 0) + (int(mnt.group(1)) if mnt else 0)
    hm = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*", t)
    if hm:
        return int(hm.group(1)) * 60 + int(hm.group(2))
    n = parse_number(t)
    if n is None:
        return None
    if key == "sleep_h" or n <= 16:
        return n * 60      # hours
    if n > 1440:
        return n / 60      # seconds
    return n               # minutes


def parse_file(text: str) -> dict:
    out: dict = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        raw_key, value = line.split(":", 1)
        key = raw_key.strip().lower()
        if key == "date":
            m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
            if m:
                out["date"] = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            continue
        field = _ALIASES.get(key)
        if not field or not value.strip():
            continue
        n = parse_minutes(value, key) if field == "sleep_min" else parse_number(value)
        if n is not None:
            out[field] = round(n, 1)
    return out


def _files() -> list[Path]:
    found = []
    for folder in HEALTH_FOLDERS:
        if not folder.exists():
            continue
        for p in folder.iterdir():
            name = p.name
            if name.startswith(".") and name.endswith(".icloud"):
                # Not downloaded yet (iCloud "optimize storage") -- ask for it.
                subprocess.Popen(["brctl", "download", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                continue
            if name.lower().startswith("health") and p.suffix.lower() in (".txt", ".json", ".md", ""):
                found.append(p)
    return found


def load_days(days: int = 14) -> dict[str, dict]:
    """date -> metrics (plus 'synced' mtime), newest file per date wins."""
    cutoff = date.today() - timedelta(days=days)
    out: dict[str, dict] = {}
    for p in _files():
        try:
            text = p.read_text(errors="replace")
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if p.suffix.lower() == ".json":
            try:
                text = "\n".join(f"{k}: {v}" for k, v in json.loads(text).items())
            except ValueError:
                pass
        d = parse_file(text)
        day = d.get("date") or (re.search(r"\d{4}-\d{2}-\d{2}", p.name) or [None])[0] or date.fromtimestamp(mtime).isoformat()
        if date.fromisoformat(day) < cutoff:
            continue
        if day not in out or mtime > out[day]["synced"]:
            out[day] = {**d, "date": day, "synced": mtime}
    return out


def _fmt_sleep(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}h {m:02d}m"


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def snapshot() -> dict | None:
    """Latest day, yesterday, 7-day averages -- or None without any data."""
    days = load_days(14)
    if not days:
        return None
    ordered = sorted(days)
    latest = days[ordered[-1]]
    today = date.today()
    week = [days[d] for d in ordered if date.fromisoformat(d) > today - timedelta(days=7)]
    prev = [days[d] for d in ordered if today - timedelta(days=14) < date.fromisoformat(d) <= today - timedelta(days=7)]
    avg = {f: _avg([d[f] for d in week if f in d]) for f in FIELDS}
    prev_avg = {f: _avg([d[f] for d in prev if f in d]) for f in FIELDS}
    return {"latest": latest, "is_today": latest["date"] == today.isoformat(), "week": avg, "prev_week": prev_avg,
            "days": len(week), "synced_ago_h": (time.time() - latest["synced"]) / 3600, "settings": settings()}


def summary() -> str:
    """Compact text for voice answers."""
    s = snapshot()
    if not s:
        return "Health: no data from the iPhone yet (the JARVIS Health Shortcut hasn't synced)."
    L, W, cfg = s["latest"], s["week"], s["settings"]
    when = "today" if s["is_today"] else f"on {L['date']}"
    parts = []
    if "steps" in L:
        parts.append(f"{int(L['steps'])} steps {when} (goal {cfg['stepGoal']})")
    if "sleep_min" in L:
        parts.append(f"slept {_fmt_sleep(L['sleep_min'])} last night (goal {cfg['sleepGoalH']}h)")
    if "exercise_min" in L:
        parts.append(f"{int(L['exercise_min'])} exercise minutes")
    if "active_kcal" in L:
        parts.append(f"{int(L['active_kcal'])} active kcal")
    if "resting_hr" in L:
        parts.append(f"resting heart rate {int(L['resting_hr'])}")
    week = []
    if W.get("steps"):
        week.append(f"{int(W['steps'])} steps/day")
    if W.get("sleep_min"):
        week.append(f"{_fmt_sleep(W['sleep_min'])} sleep/night")
    trend = ""
    if W.get("steps") and s["prev_week"].get("steps"):
        change = (W["steps"] - s["prev_week"]["steps"]) / s["prev_week"]["steps"] * 100
        trend = f" Steps are {'up' if change >= 0 else 'down'} {abs(change):.0f}% on the week before."
    return (f"Health (from iPhone, synced {s['synced_ago_h']:.0f}h ago): " + "; ".join(parts) + "."
            + (f" 7-day average: {', '.join(week)}." if week else "") + trend)


def category() -> dict | None:
    """The briefing's HEALTH card (findings.py), or None without data."""
    s = snapshot()
    if not s:
        return None
    L, W, cfg = s["latest"], s["week"], s["settings"]
    rows, say = [], ["Health."]
    good, warn, info = "good", "warn", "info"
    if "sleep_min" in L:
        hours = L["sleep_min"] / 60
        tone = good if hours >= cfg["sleepGoalH"] else warn if hours >= cfg["sleepGoalH"] - 1 else "bad"
        rows.append({"l": "Sleep last night", "r": _fmt_sleep(L["sleep_min"]).upper(), "tone": tone})
        say.append(f"You slept {int(hours)} hours {int(round((hours % 1) * 60))} minutes"
                   + (", nicely rested." if tone == good else ", a bit short." if tone == warn else ". Go easy today."))
    if "steps" in L:
        pct = L["steps"] / max(1, cfg["stepGoal"])
        rows.append({"l": "Steps " + ("today" if s["is_today"] else L["date"][5:]), "r": f"{int(L['steps']):,}".replace(",", "."),
                     "tone": good if pct >= 1 else info if pct >= 0.5 else warn})
        say.append(f"{int(L['steps'])} steps {'so far today' if s['is_today'] else 'yesterday'}"
                   + (", goal smashed." if pct >= 1 else f", {int(pct * 100)} percent of your goal."))
    if W.get("steps") and len(rows) < 3:
        rows.append({"l": "7-day average", "r": f"{int(W['steps']):,} STEPS".replace(",", "."), "tone": info})
    if "exercise_min" in L and len(rows) < 3:
        rows.append({"l": "Exercise", "r": f"{int(L['exercise_min'])} MIN", "tone": good if L["exercise_min"] >= 30 else info})
    if "resting_hr" in L and len(rows) < 3:
        rows.append({"l": "Resting heart rate", "r": f"{int(L['resting_hr'])} BPM", "tone": info})
    if W.get("steps") and s["prev_week"].get("steps"):
        change = (W["steps"] - s["prev_week"]["steps"]) / s["prev_week"]["steps"] * 100
        if abs(change) >= 10:
            say.append(f"Your steps are {'up' if change > 0 else 'down'} {abs(change):.0f} percent on last week.")
    if s["synced_ago_h"] > 36:
        say.append("Your iPhone hasn't synced in a while, by the way.")
    headline_bits = []
    if "sleep_min" in L:
        headline_bits.append(f"Slept {_fmt_sleep(L['sleep_min'])}")
    if "steps" in L:
        headline_bits.append(f"{int(L['steps']):,} steps".replace(",", "."))
    return {"name": "HEALTH", "count": "IPHONE" if s["synced_ago_h"] < 36 else "STALE",
            "headline": " · ".join(headline_bits) + "." if headline_bits else "Health data synced.",
            "items": rows[:3], "say": " ".join(say)}


if __name__ == "__main__":
    print(summary())
    print(json.dumps(category(), indent=1))
