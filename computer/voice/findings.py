#!/usr/bin/env python3
"""
The daily findings report shown on the JARVIS screen (computer/screen/):
six categories, each with a headline, three detail rows and one spoken line.

Built straight from the live data in skills.py -- no model involved, so it
takes seconds (Mail.app is the slow part, up to 45s) and can't invent
anything. Cached in cache/findings.json; screen_server.py serves it and the
screen speaks each category's "say" line with Piper.

  python3 findings.py        # build now and print it
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import activity
import business as business_data
import health as health_data
import notes as notes_data
import skills

FINDINGS_FILE = skills.CACHE_DIR / "findings.json"
GOALS_FILE = skills.JARVIS_DIR / "database" / "goals.json"  # written by the pet's Goals window

# Row tones -> the screen maps these to the design's colours.
GOOD, WARN, BAD, INFO, MUTED = "good", "warn", "bad", "info", "muted"


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _cat(name: str, count: str, headline: str, items: list[dict], say: str) -> dict:
    return {"name": name, "count": count, "headline": headline, "items": items[:3], "say": say}


def _row(left: str, right: str, tone: str = INFO) -> dict:
    return {"l": left, "r": right, "tone": tone}


def _plural(n: int, word: str) -> str:
    if n == 1:
        return f"{n} {word}"
    if word[-1:].lower() == "y":
        return f"{n} {word[:-1]}{'IES' if word.isupper() else 'ies'}"
    return f"{n} {word}{'S' if word.isupper() else 's'}"


# --- categories ----------------------------------------------------------------

def priority(src: dict) -> dict:
    tasks = skills._open_tasks(str(skills.JARVIS_DIR / "TASKS.md"))
    for proj in src["projects"]:
        if proj.get("tasks_file"):
            tasks += [t for t in skills._open_tasks(proj["tasks_file"]) if t not in tasks]
    if not tasks:
        return _cat("PRIORITY", "CLEAR", "No open tasks. The runway is clear.",
                    [_row("TASKS.md", "0 OPEN", GOOD)],
                    "Priority first. Nothing is waiting on your task list.")
    rows = [_row(_short(t, 40), "OPEN", WARN if i == 0 else INFO) for i, t in enumerate(tasks)]
    more = f", plus {_plural(len(tasks) - 1, 'other')}" if len(tasks) > 1 else ""
    return _cat("PRIORITY", _plural(len(tasks), "TASK"), _short(f"Oldest open task: {tasks[0]}.", 90), rows,
                f"Priority first. Your oldest open task is {_short(tasks[0], 90)}{more}.")


# --- goals (mirrors computer/pet/store.js's progress()) ----------------------------

def _load_goals() -> dict:
    try:
        return json.loads(GOALS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _period_start(cadence: str, now: datetime) -> datetime:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if cadence == "weekly":
        return day - timedelta(days=day.weekday())
    if cadence == "monthly":
        return day.replace(day=1)
    return day


def _period_len(cadence: str, now: datetime) -> int:
    if cadence == "daily":
        return 1
    if cadence == "weekly":
        return 7
    nxt = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (nxt - timedelta(days=1)).day


def goal_status(data: dict | None = None) -> list[dict]:
    """Each active goal with this period's progress."""
    data = data if data is not None else _load_goals()
    now = datetime.now()
    out = []
    for g in data.get("goals", []):
        if g.get("archived"):
            continue
        start = _period_start(g.get("cadence", "daily"), now)
        done = sum(1 for l in data.get("log", []) if l.get("goal") == g["id"] and l.get("at", 0) / 1000 >= start.timestamp())
        target = max(1, int(g.get("target") or 1))
        days_in = (now - start).days + 1
        length = _period_len(g.get("cadence", "daily"), now)
        expected = target if g.get("cadence") == "daily" else (target * days_in) // length
        out.append({"title": g["title"], "cadence": g.get("cadence", "daily"), "done": done, "target": target,
                    "met": done >= target, "behind": done < expected, "days_left": length - days_in})
    return out


def people_due(data: dict | None = None) -> list[tuple[str, int | None]]:
    data = data if data is not None else _load_goals()
    due = []
    for p in data.get("people", []):
        last = p.get("lastContact")
        days = int((time.time() - last / 1000) // 86400) if last else None
        if days is None or days >= int(p.get("everyDays") or 7):
            due.append((p["name"], days))
    return due


def goals_summary() -> str:
    """Compact text for voice answers ("how am I doing on my goals?")."""
    status = goal_status()
    if not status:
        return "Goals: none set yet (they're added in the pet's Goals & reminders window)."
    word = {"daily": "today", "weekly": "this week", "monthly": "this month"}
    parts = [f"{g['title']}: {g['done']} of {g['target']} {word[g['cadence']]}"
             + (" (done)" if g["met"] else " (behind)" if g["behind"] else "") for g in status]
    due = people_due()
    extra = (" Due a call: " + ", ".join(f"{n} ({d} days)" if d is not None else n for n, d in due) + ".") if due else ""
    return "Goals -- " + "; ".join(parts) + "." + extra


def goals(_src: dict) -> dict | None:
    status = goal_status()
    due = people_due()
    if not status and not due:
        return None  # nothing logged -- the category is left out
    word = {"daily": "TODAY", "weekly": "THIS WEEK", "monthly": "THIS MONTH"}
    rows = [_row(_short(g["title"], 30), f"{g['done']}/{g['target']} {word[g['cadence']]}",
                 GOOD if g["met"] else WARN if g["behind"] else INFO) for g in status]
    rows += [_row(f"Call {name}", f"{days} DAYS" if days is not None else "DUE", WARN) for name, days in due]
    met = [g for g in status if g["met"]]
    behind = [g for g in status if g["behind"] and not g["met"]]
    if behind:
        g = behind[0]
        headline = f"{g['title']}: {g['done']} of {g['target']} {word[g['cadence']].lower()}."
    elif status:
        headline = f"{len(met)} of {len(status)} goals done for the period. On track."
    else:
        headline = f"Time to call {due[0][0]}."
    say = "Goals. "
    if status:
        say += f"{len(met)} of {len(status)} are done for their period"
        if behind:
            g = behind[0]
            say += f"; {g['title']} needs attention, {g['done']} of {g['target']} {word[g['cadence']].lower()}"
        say += "."
    if due:
        name, days = due[0]
        say += f" And it's been {days} days since you talked to {name}." if days is not None else f" Maybe give {name} a call."
    return _cat("GOALS", f"{len(met)}/{len(status)} ON TRACK" if status else "CHECK-INS", _short(headline, 90), rows, say)


def health(_src: dict) -> dict | None:
    """Apple Health via the iPhone Shortcut (health.py); left out without data."""
    return health_data.category()


def business(_src: dict) -> dict | None:
    """Your sites' admin numbers (business.py); left out without sites."""
    return business_data.category()


def notes(_src: dict) -> dict | None:
    """Apple Notes added / edited since yesterday; left out when none."""
    return notes_data.category()


def weather(src: dict) -> dict:
    city = src["city"]["name"]
    w = skills.weather_data(src)
    if not w:
        return _cat("WEATHER", "OFFLINE", "Weather is unavailable right now.",
                    [_row(city, "NO DATA", MUTED)], "The weather service didn't answer, so no forecast this time.")
    rain = int(w["rain"] or 0)
    rain_tone = BAD if rain >= 70 else WARN if rain >= 40 else GOOD
    return _cat(
        "WEATHER", f"{w['now_temp']:.0f}°C",
        f"{w['now_temp']:.0f}° and {w['now_text']} in {city}.",
        [_row("Right now", f"{w['now_temp']:.0f}° · {w['now_text'].upper()}"),
         _row("Today", f"{w['min']:.0f}° – {w['max']:.0f}°"),
         _row("Chance of rain", f"{rain}%", rain_tone)],
        f"Weather. It's {w['now_temp']:.0f} degrees and {w['now_text']} in {city}, "
        f"{w['min']:.0f} to {w['max']:.0f} today, with a {rain} percent chance of rain.",
    )


def inbox(src: dict) -> dict:
    d = skills.email_data(src)
    if d["error"]:
        why = {"timeout": "Mail.app didn't respond in time.",
               "permission": "JARVIS needs permission to read Mail."}.get(d["error"], "Couldn't read Mail.app.")
        return _cat("INBOX", "UNAVAILABLE", why, [_row("Mail.app", d["error"].upper(), WARN)],
                    f"Inbox. {why}")
    total, msgs = d["total"], d["messages"]
    if not msgs:
        return _cat("INBOX", f"{total} UNREAD", "Inbox zero. Nothing unread.",
                    [_row("Unread", "0", GOOD)], "Inbox. Nothing unread. Inbox zero.")
    rows = []
    for iso, sender, subject in msgs:
        try:
            when = datetime.fromisoformat(iso)
            stamp = when.strftime("%H:%M") if when.date() == datetime.now().date() else when.strftime("%d %b").upper()
        except ValueError:
            stamp = ""
        rows.append(_row(_short(f"{sender} — {subject}", 42), stamp, INFO))
    _iso, sender, subject = msgs[0]
    return _cat("INBOX", f"{total} UNREAD", _short(f"{_plural(total, 'unread message')}; newest from {sender}.", 90), rows,
                f"Inbox. {_plural(total, 'unread message')}. The newest is from {sender}, about {_short(subject, 80)}.")


def projects(src: dict) -> dict:
    scanned = []
    for proj in src["projects"]:
        s = skills._scan_project(proj)
        scanned.append((len(s["day"]), len(s["week"]), s["total"], proj["name"]))
    scanned.sort(reverse=True)
    rows = []
    for day, week, total, name in scanned:
        if day:
            rows.append(_row(name, f"{day} TODAY", GOOD))
        elif week:
            rows.append(_row(name, f"{week} THIS WEEK", INFO))
        else:
            rows.append(_row(name, "QUIET" if total else "NO FILES", MUTED))
    today = [n for d, _w, _t, n in scanned if d]
    week = [n for d, w, _t, n in scanned if w and not d]
    quiet = [n for d, w, _t, n in scanned if not d and not w]
    if today:
        headline = f"{today[0]} moved most today: {_plural(scanned[0][0], 'file')} changed."
    elif week:
        headline = f"Nothing touched today; {week[0]} was busiest this week."
    else:
        headline = "All projects quiet this week."

    def names(xs: list[str]) -> str:
        return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]

    parts = []
    if today:
        parts.append(f"{names(today[:3])} moved today")
    if week:
        parts.append(f"{names(week[:2])} changed earlier this week")
    if quiet and len(quiet) <= 2:
        parts.append(f"{names(quiet)} {'has' if len(quiet) == 1 else 'have'} been quiet")
    elif quiet:
        parts.append(f"{len(quiet)} others have been quiet")
    return _cat("PROJECTS", f"{len(scanned)} TRACKED", _short(headline, 90), rows,
                "Projects. " + "; ".join(parts) + ".")


def world(src: dict) -> dict:
    feeds = skills.news_data(src, per_feed=3)
    if not feeds:
        return _cat("WORLD", "OFFLINE", "News feeds are unreachable right now.",
                    [_row("Feeds", "NO DATA", MUTED)], "In the world: the news feeds didn't answer this time.")
    stories = [(f["name"], f["lang"], t) for f in feeds for t in f["titles"]]
    # Lead with English stories -- Piper reads English, and the headline is spoken.
    stories.sort(key=lambda s: s[1] != "en")
    rows = [_row(_short(t, 44), name.upper(), INFO if lang == "en" else MUTED) for name, lang, t in stories[1:]]
    spoken = [t for _n, lang, t in stories if lang == "en"][:2]
    other = sum(1 for _n, lang, _t in stories if lang != "en")
    if spoken:
        say = "In the world. " + ". ".join(s.rstrip(".") for s in spoken) + "."
    else:
        say = "In the world."
    if other:
        say += f" Plus {_plural(other, 'story')} in German on screen."
    return _cat("WORLD", _plural(len(stories), "STORY"),
                _short(stories[0][2], 90), rows, say)


def _port_up(port: int, path: str = "/") -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1.5)
        return True
    except urllib.error.HTTPError:
        return True  # answering, just not happy with the bare path
    except Exception:  # noqa: BLE001
        return False


def systems(_src: dict) -> dict:
    model = _port_up(8080, "/health")
    speech = _port_up(8093)
    rows = [_row("Local model", "ONLINE" if model else "OFFLINE", GOOD if model else BAD),
            _row("Speech recognition", "ONLINE" if speech else "OFFLINE", GOOD if speech else BAD)]
    summary = activity.today_summary(max_apps=1, max_titles=0)
    top = ""
    if summary.startswith("Active on the Mac since"):
        since = summary.split("since ", 1)[1][:5]
        top = summary.split("Time per app: ", 1)[1].rstrip(".") if "Time per app: " in summary else ""
        rows.append(_row(f"On the Mac since {since}", _short(top, 22).upper() if top else "—", INFO))
    issues = [n for n, ok in (("the local model", model), ("speech recognition", speech)) if not ok]
    if issues:
        return _cat("SYSTEMS", _plural(len(issues), "ISSUE"), f"{' and '.join(issues).capitalize()} offline.",
                    rows, f"Finally, systems. {' and '.join(issues).capitalize()} {'is' if len(issues) == 1 else 'are'} offline.")
    say = "Finally, systems. Everything is running locally."
    if top:
        say += f" Most of your time today went to {top.rsplit(' ', 2)[0]}."
    return _cat("SYSTEMS", "ALL NOMINAL", "All JARVIS systems running locally.", rows, say)


# --- report ----------------------------------------------------------------------

def _greeting(name: str) -> str:
    hour = datetime.now().hour
    part = "morning" if 5 <= hour < 12 else "afternoon" if hour < 18 else "evening"
    return f"Good {part}, {name}."


def build(step: skills.StepFn = skills._noop_step) -> dict:
    src = skills.load_sources()
    cats = []
    for fn in (priority, business, goals, health, notes, weather, inbox, projects, world, systems):
        label = f"Findings · {fn.__name__}"
        step(label, "running", "")
        try:
            cat = fn(src)
            if cat:
                cats.append(cat)
            step(label, "done", "")
        except Exception as exc:  # noqa: BLE001 -- one broken source shouldn't sink the report
            step(label, "failed", str(exc)[:60])
            cats.append(_cat(fn.__name__.upper(), "ERROR", "Couldn't gather this one.",
                             [_row("Error", _short(str(exc), 24).upper(), BAD)], ""))
    for i, c in enumerate(cats, 1):
        c["code"] = f"{i:02d}"
    name = src.get("user_name", "sir")
    data = {
        "generated_at": time.time(),
        "intro": f"{_greeting(name)} I've compiled today's findings across {len(cats)} categories.",
        "outro": "That's everything. Anything you'd like me to look into?",
        "categories": cats,
    }
    skills.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FINDINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(FINDINGS_FILE)
    return data


def load(max_age_hours: float = 24) -> dict | None:
    try:
        data = json.loads(FINDINGS_FILE.read_text())
    except (OSError, ValueError):
        return None
    if time.time() - data.get("generated_at", 0) > max_age_hours * 3600:
        return None
    return data


if __name__ == "__main__":
    started = time.monotonic()
    print(json.dumps(build(), indent=1, ensure_ascii=False))
    print(f"[findings] built in {time.monotonic() - started:.1f}s")
