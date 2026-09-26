"""
The "smarter JARVIS" layer -- everything instant, no model involved:

- Background media: while Spotify / Music play, a conversation turns them
  down (and back up after), and lyric-like rambling isn't answered.
- Memory: "remember that Anna loves sunflowers", "forget that...", "what do
  you know about me?" -- facts in database/memory.json; the relevant ones
  go into every question's context so answers use them.
- Reminders and timers: "remind me to call Mom at 6", "remind me in 20
  minutes to stretch", "set a timer for 10 minutes", "what reminders do I
  have?", "cancel the reminder". database/reminders.json; a background loop
  says them out loud and shows a card by the pet.
- Goals by voice: "I worked out today", "I called Mom", "add a goal to read
  3 times a week" -- the same database/goals.json the pet's Goals window uses.

handle(text) returns a reply for anything it recognises, else None.
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import activity

JARVIS_DIR = Path(__file__).resolve().parent.parent.parent
DB = JARVIS_DIR / "database"
MEMORY_FILE = DB / "memory.json"
REMINDERS_FILE = DB / "reminders.json"
GOALS_FILE = DB / "goals.json"
_lock = threading.Lock()

STOP = set("""a an the and or but so to of in on at for with about from by is are was were be been am i me my mine you your
it its this that these those do does did have has had can could would should will just really very please hey jarvis okay ok
what whats who when where why how there here some any all no not dont""".split())


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(path)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zäöüß0-9']+", text.lower().replace("’", "'"))


def _stem(w: str) -> str:
    w = w.replace("'", "")
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _keys(text: str) -> set[str]:
    return {_stem(w) for w in _words(text) if w not in STOP and len(w) > 1}


def _overlap(a: set[str], b: set[str]) -> int:
    """Shared keywords; longer words also match inside each other
    (flower ~ sunflower, birthday ~ birthdays)."""
    return sum(1 for x in a if any(x == y or (min(len(x), len(y)) >= 4 and (x in y or y in x)) for y in b))


def _lower_first(text: str) -> str:
    """'Your passport...' -> 'your passport...', but keep names ('Anna ...')."""
    return text[0].lower() + text[1:] if re.match(r"^(Your|You|You're|You've)\b", text) else text


# --- background media ------------------------------------------------------------

_MEDIA_APPS = ("Spotify", "Music")
_ducked: dict[str, int] = {}


def _osa(script: str, timeout: float = 3) -> str:
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _playing_volume(app: str) -> int | None:
    # "is running" first, so asking never launches the app.
    out = _osa(f'if application "{app}" is running then\n tell application "{app}"\n'
               f'  if player state is playing then return sound volume as text\n end tell\nend if\nreturn ""')
    return int(out) if out.isdigit() else None


def media_playing() -> bool:
    if _ducked:
        return True
    if any(_playing_volume(a) is not None for a in _MEDIA_APPS):
        return True
    title = activity.now_line().lower()
    return bool(re.search(r"youtube|netflix|twitch|prime video|disney\+|spotify", title))


def duck_music() -> None:
    """Turn playing music down for the conversation (restored by restore_music)."""
    for app in _MEDIA_APPS:
        vol = _playing_volume(app)
        if vol is not None and app not in _ducked:
            _ducked[app] = vol
            _osa(f'tell application "{app}" to set sound volume to {max(8, vol // 4)}')


def restore_music() -> None:
    for app, vol in list(_ducked.items()):
        _osa(f'if application "{app}" is running then tell application "{app}" to set sound volume to {vol}')
    _ducked.clear()


def looks_like_background(text: str) -> bool:
    """Lyrics / a video talking, not the user: long, no question, not addressed
    to JARVIS -- only judged that strictly while media is playing."""
    words = _words(text)
    if len(words) < 16 or "?" in text or "jarvis" in words:
        return False
    return media_playing()


# --- memory ------------------------------------------------------------------------

_REMEMBER = re.compile(r"^(?:hey,? )?(?:jarvis,? )?(?:please |can you |could you )?(?:remember|note|keep in mind|don't forget|dont forget|make a note)"
                       r"(?: that| this)?[:,]?\s+(.{3,})$", re.I)
_FORGET = re.compile(r"^(?:hey,? )?(?:jarvis,? )?(?:please )?forget (?:that |about |the fact that )?(.{3,})$", re.I)
_RECALL = re.compile(r"\b(what do you (know|remember) about me|what have i told you|what do you remember)\b", re.I)


def _memories() -> list[dict]:
    return _read(MEMORY_FILE, [])


def _you(text: str) -> str:
    """Store facts in the second person, the way JARVIS will say them back."""
    swaps = {r"\bi am\b": "you are", r"\bi'm\b": "you're", r"\bim\b": "you're", r"\bmy\b": "your", r"\bme\b": "you",
             r"\bi\b": "you", r"\bmine\b": "yours", r"\bi've\b": "you've", r"\bi'll\b": "you'll"}
    out = text.strip().rstrip(".!")
    for pat, rep in swaps.items():
        out = re.sub(pat, rep, out, flags=re.I)
    return out[0].upper() + out[1:] if out else out


def remember(fact: str) -> str:
    fact = _you(fact)
    with _lock:
        mem = _memories()
        keys = _keys(fact)
        # Replace a near-duplicate ("Anna's birthday is May 3" -> "... May 4").
        mem = [m for m in mem if len(keys & _keys(m["text"])) < max(2, len(keys) * 0.7)]
        mem.append({"id": secrets.token_hex(4), "text": fact, "at": time.time()})
        _write(MEMORY_FILE, mem[-300:])
    return fact


def forget(what: str) -> str | None:
    keys = _keys(what)
    with _lock:
        mem = _memories()
        scored = sorted(((len(keys & _keys(m["text"])), m) for m in mem), key=lambda x: -x[0])
        if not scored or scored[0][0] == 0:
            return None
        gone = scored[0][1]
        _write(MEMORY_FILE, [m for m in mem if m["id"] != gone["id"]])
    return gone["text"]


def memory_context(question: str, limit: int = 4) -> str:
    """Facts relevant to the question (plus who's who from the Goals window)."""
    keys = _keys(question)
    goals = _read(GOALS_FILE, {})
    partner = goals.get("settings", {}).get("partner", {}).get("name")
    # "my girlfriend" means her -- look up facts stored under her name too.
    if partner and re.search(r"\b(girlfriend|partner|gf|wife)\b", question, re.I):
        keys |= _keys(partner)
    for rel, words in _RELATIONS.items():
        if keys & words:
            keys |= words
    facts = []
    mem = _memories()
    scored = sorted(((_overlap(keys, _keys(m["text"])), m["at"], m["text"]) for m in mem), reverse=True)
    facts += [t for s, _a, t in scored if s > 0][:limit]
    people = [f"{p['name']} ({p.get('relation', 'friend')})" for p in goals.get("people", [])]
    if partner:
        people.insert(0, f"{partner} (girlfriend)")
    if people and (keys & {_stem(w) for p in people for w in _words(p)} or re.search(r"\b(girlfriend|mom|mum|dad|friend|family|call)\b", question, re.I)):
        facts.append("People in their life: " + ", ".join(dict.fromkeys(people)))
    return " ".join(f"{f}." if not f.endswith(".") else f for f in facts)


# --- reminders & timers ---------------------------------------------------------------

_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45,
        "forty-five": 45, "sixty": 60, "ninety": 90}
_NUM_RE = r"(\d+(?:[.,]\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty[- ]five|forty|sixty|ninety)"
_UNIT = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400}

_REMIND = re.compile(r"\bremind me\b|\bset (?:a |an )?reminder\b", re.I)
_TIMER = re.compile(rf"\b(?:set (?:a |an )?)?timer (?:for )?{_NUM_RE}\s*(second|sec|minute|min|hour|hr)s?\b|\b{_NUM_RE}[- ](second|minute|hour)s? timer\b", re.I)
_LIST = re.compile(r"\b(what|which|any) (reminders|timers)\b|\bmy (reminders|timers)\b|\bdo i have any reminders\b", re.I)
_CANCEL = re.compile(r"\b(cancel|delete|remove|stop|clear)\b.*\b(reminder|reminders|timer|timers)\b", re.I)


def _num(tok: str) -> float:
    tok = tok.lower().replace(",", ".")
    return _NUM.get(tok, None) or float(tok)


def parse_when(text: str, now: datetime | None = None) -> tuple[datetime | None, str]:
    """Find a time in text. Returns (when, text with the time phrase removed)."""
    now = now or datetime.now()
    t = text
    m = re.search(r"\bin half an hour\b", t, re.I)
    if m:
        return now + timedelta(minutes=30), (t[: m.start()] + t[m.end():])
    m = re.search(rf"\bin {_NUM_RE}\s*(second|sec|minute|min|hour|hr|day)s?(?: and (?:a )?half)?\b", t, re.I)
    if m:
        secs = _num(m.group(1)) * _UNIT[m.group(2).lower()]
        if "half" in m.group(0).lower():
            secs += _UNIT[m.group(2).lower()] / 2
        return now + timedelta(seconds=secs), (t[: m.start()] + t[m.end():])
    day = now.date()
    day_phrase = re.search(r"\b(tomorrow|tonight|this evening|this afternoon|in the morning|in the evening)\b", t, re.I)
    hint = day_phrase.group(1).lower() if day_phrase else ""
    if hint == "tomorrow":
        day = day + timedelta(days=1)
    m = re.search(r"\bat (noon|midnight|(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?|o'?clock)?)\b", t, re.I)
    if not m:
        if hint == "tomorrow":  # "tomorrow" alone -> 9 am
            when = datetime.combine(day, datetime.min.time()).replace(hour=9)
            return when, (t[: day_phrase.start()] + t[day_phrase.end():])
        return None, t
    if m.group(1).lower() == "noon":
        hour, minute = 12, 0
    elif m.group(1).lower() == "midnight":
        hour, minute = 0, 0
    else:
        hour, minute = int(m.group(2)), int(m.group(3) or 0)
        ampm = (m.group(4) or "").lower().replace(".", "")
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        elif not ampm.startswith(("am", "pm")) and hour <= 12:
            # "at 6": evening words -> pm; small hours are usually pm too.
            if hint in ("tonight", "this evening", "in the evening", "this afternoon") or (hour < 8 and hint != "in the morning"):
                hour = hour % 12 + 12
    if hour > 23 or minute > 59:
        return None, t
    when = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    if when <= now:
        if hour < 12 and when + timedelta(hours=12) > now and not m.group(4):
            when += timedelta(hours=12)
        else:
            when += timedelta(days=1)
    spans = sorted([m.span()] + ([day_phrase.span()] if day_phrase else []), reverse=True)
    for a, b in spans:
        t = t[:a] + t[b:]
    return when, t


def _say_time(when: datetime) -> str:
    now = datetime.now()
    clock = when.strftime("%-I:%M %p").replace(":00 ", " ")
    if when.date() == now.date():
        mins = round((when - now).total_seconds() / 60)
        return f"in {mins} minute{'s' if mins != 1 else ''}" if mins < 60 else f"at {clock}"
    if when.date() == (now + timedelta(days=1)).date():
        return f"tomorrow at {clock}"
    return when.strftime("on %A at ") + clock


def _reminders() -> list[dict]:
    return _read(REMINDERS_FILE, [])


def add_reminder(text: str, when: datetime, kind: str = "reminder") -> dict:
    r = {"id": secrets.token_hex(4), "text": text, "due": when.timestamp(), "kind": kind, "created": time.time(), "done": False}
    with _lock:
        rs = [x for x in _reminders() if not x.get("done") or time.time() - x["due"] < 86400]
        rs.append(r)
        _write(REMINDERS_FILE, rs)
    return r


def snooze(rid: str, minutes: float = 10) -> bool:
    with _lock:
        rs = _reminders()
        for r in rs:
            if r["id"] == rid:
                r.update(due=time.time() + minutes * 60, done=False, fired=False)
                _write(REMINDERS_FILE, rs)
                return True
    return False


def mark_done(rid: str) -> None:
    with _lock:
        rs = _reminders()
        for r in rs:
            if r["id"] == rid:
                r["done"] = True
        _write(REMINDERS_FILE, rs)


def _reminder_command(text: str) -> str | None:
    if _LIST.search(text) and not _REMIND.search(text):
        upcoming = sorted((r for r in _reminders() if not r.get("done")), key=lambda r: r["due"])
        if not upcoming:
            return "You have no reminders coming up."
        items = [f"{r['text']} {_say_time(datetime.fromtimestamp(r['due']))}" for r in upcoming[:3]]
        more = f", and {len(upcoming) - 3} more" if len(upcoming) > 3 else ""
        return f"You have {len(upcoming)}: " + "; ".join(items) + more + "."
    if _CANCEL.search(text) and not _REMIND.search(text):
        upcoming = [r for r in _reminders() if not r.get("done")]
        if not upcoming:
            return "There's nothing to cancel."
        keys = _keys(re.sub(r"\b(cancel|delete|remove|stop|clear|reminder|reminders|timer|timers|the|my)\b", " ", text, flags=re.I))
        if re.search(r"\ball\b", text, re.I):
            for r in upcoming:
                mark_done(r["id"])
            return f"Done, cleared all {len(upcoming)}."
        best = max(upcoming, key=lambda r: (len(keys & _keys(r["text"])), r["created"]))
        mark_done(best["id"])
        return f"Cancelled: {best['text']}."
    m = _TIMER.search(text)
    if m:
        amount = _num(m.group(1) or m.group(3))
        unit = {"sec": "second", "min": "minute", "hr": "hour"}.get((m.group(2) or m.group(4)).lower(), (m.group(2) or m.group(4)).lower())
        n = int(amount) if float(amount).is_integer() else amount
        add_reminder(f"{n}-{unit} timer", datetime.now() + timedelta(seconds=amount * _UNIT[unit]), kind="timer")
        return f"Timer set for {n} {unit}{'s' if amount != 1 else ''}."
    if not _REMIND.search(text):
        return None
    when, rest = parse_when(text)
    task = re.sub(r".*?\b(?:remind me|set (?:a |an )?reminder)\b", "", rest, count=1, flags=re.I)
    task = re.sub(r"^\s*(?:to|about|that|of|for)\b", "", task.strip(" ,.?!"), flags=re.I).strip(" ,.?!")
    task = re.sub(r"\s+(to|about)\s*$", "", task).strip()
    if not when:
        return "Sure -- when should I remind you? Say something like: remind me at six to call Mom."
    if not task:
        task = "the thing you asked me about"
    task = _you(task)
    add_reminder(task[0].lower() + task[1:], when)
    return f"Okay, I'll remind you to {task[0].lower() + task[1:]} {_say_time(when)}."


def reminder_loop(say, card, notify) -> None:
    """Background: fire due reminders -- a spoken line, a card by the pet, a
    notification. say(text) blocks until spoken."""
    while True:
        time.sleep(3)
        try:
            due = [r for r in _reminders() if not r.get("done") and not r.get("fired") and r["due"] <= time.time()]
            for r in due:
                with _lock:
                    rs = _reminders()
                    for x in rs:
                        if x["id"] == r["id"]:
                            x["fired"] = True
                            x["done"] = r["kind"] == "timer"
                    _write(REMINDERS_FILE, rs)
                user = _read(JARVIS_DIR / "config" / "jarvis_sources.json", {}).get("user_name", "")
                line = (f"{user}, your {r['text']} is done." if r["kind"] == "timer"
                        else f"{user}, a reminder: {r['text']}.").strip(", ")
                card(r, line)
                notify("JARVIS reminder", r["text"])
                say(line)
        except Exception as exc:  # noqa: BLE001
            print(f"[smarts] reminder loop: {exc!r}", flush=True)


# --- goals by voice --------------------------------------------------------------------

_DID = re.compile(r"^(?:hey,? )?(?:jarvis,? )?(?:so |okay,? |ok,? |guess what,? )?(?:i(?:'ve| have)? (?:just |finally |already )?(?:did|done|finished|completed|went|had|got|worked|ran|read|studied|practiced|meditated|applied|wrote|cooked|walked|trained|made)"
                  r"|mark|log|check off|tick off|done with)\b(.*)$", re.I)
_CALLED = re.compile(r"\bi(?:'ve| have)? (?:just )?(?:called|phoned|rang|spoke to|spoke with|talked to|talked with|texted|messaged|facetimed|visited|saw)\s+(?:my |with )?(.+?)(?:\s+(?:today|yesterday|earlier|just now|now))?[.!]*$", re.I)
_ADD_GOAL = re.compile(r"\b(?:add|set|create|new)(?: a| another)? (?:new )?goal(?: to| of| for|:)?\s+(.+)$|\bmy (?:new )?goal is to\s+(.+)$", re.I)
_RELATIONS = {"mom": {"mom", "mum", "mother", "mama", "mommy"}, "dad": {"dad", "father", "papa", "daddy"}}


def _goals_data() -> dict:
    return _read(GOALS_FILE, {"version": 1, "goals": [], "log": [], "skips": [], "people": [], "moods": [], "settings": {}, "character": "human"})


def _progress_line(data: dict, goal: dict) -> str:
    import findings  # progress maths shared with the briefing
    st = next((g for g in findings.goal_status(data) if g["title"] == goal["title"]), None)
    if not st:
        return ""
    word = {"daily": "today", "weekly": "this week", "monthly": "this month"}[st["cadence"]]
    if st["met"]:
        return f"That's {goal['title']} done {word}" + (f" -- {st['done']} of {st['target']}." if st["target"] > 1 else ".")
    return f"{goal['title']}: {st['done']} of {st['target']} {word}."


def _goal_command(text: str) -> str | None:
    m = _ADD_GOAL.search(text)
    if m:
        body = (m.group(1) or m.group(2)).strip(" .!")
        cadence, target = "daily", 1
        cm = re.search(rf"\b{_NUM_RE} times? (?:a|per|each) (week|month|day)\b", body, re.I)
        if cm:
            target, cadence = int(_num(cm.group(1))), {"week": "weekly", "month": "monthly", "day": "daily"}[cm.group(2).lower()]
            body = body[: cm.start()] + body[cm.end():]
        elif re.search(r"\b(every|each|once a|per) week\b|\bweekly\b", body, re.I):
            cadence = "weekly"
        elif re.search(r"\b(every|each|once a|per) month\b|\bmonthly\b", body, re.I):
            cadence = "monthly"
        body = re.sub(r"\b(every|each|once a|per) (day|week|month)\b|\b(daily|weekly|monthly)\b", "", body, flags=re.I)
        title = re.sub(r"\s+", " ", body).strip(" ,.")
        if not title:
            return None
        title = title[0].upper() + title[1:]
        with _lock:
            data = _goals_data()
            data.setdefault("goals", []).append({"id": secrets.token_hex(5), "created": int(time.time() * 1000), "title": title,
                                                 "cadence": cadence, "target": target, "why": "", "remind": True, "remindAt": None})
            _write(GOALS_FILE, data)
        freq = {"daily": "every day", "weekly": "a week", "monthly": "a month"}[cadence]
        times = f"{target} times " if target > 1 else ("once " if cadence != "daily" else "")
        return f"New goal: {title}, {times}{freq}. I'll keep you on track."
    m = _CALLED.search(text)
    if m:
        who = _keys(m.group(1))
        with _lock:
            data = _goals_data()
            for p in data.get("people", []):
                names = _keys(p["name"])
                for rel, words in _RELATIONS.items():
                    if names & {_stem(w) for w in words}:
                        names |= {_stem(w) for w in words}
                if who & names:
                    p["lastContact"] = int(time.time() * 1000)
                    _write(GOALS_FILE, data)
                    return f"Nice, I've noted you talked to {p['name']}. That will have made their day."
    m = _DID.search(text)
    if m:
        said = _keys(text)
        data = _goals_data()
        goals = [g for g in data.get("goals", []) if not g.get("archived")]
        scored = sorted(((len(said & _keys(g["title"])), g) for g in goals), key=lambda x: -x[0])
        if scored and scored[0][0] > 0:
            goal = scored[0][1]
            with _lock:
                data = _goals_data()
                data.setdefault("log", []).append({"goal": goal["id"], "at": int(time.time() * 1000)})
                _write(GOALS_FILE, data)
            return f"Logged! {_progress_line(data, goal)}".strip()
    return None


# --- entry point -------------------------------------------------------------------------

def handle(text: str) -> str | None:
    """A reply for memory / reminder / goal commands, else None."""
    t = text.strip()
    if _RECALL.search(t):
        mem = _memories()
        if not mem:
            return "Nothing yet. Tell me things like: remember that my girlfriend loves sunflowers."
        latest = [m["text"] for m in sorted(mem, key=lambda m: -m["at"])[:4]]
        return f"I know {len(mem)} thing{'s' if len(mem) != 1 else ''} you told me. Most recent: " + "; ".join(latest) + "."
    m = _REMEMBER.match(t)
    if m and not re.match(r"^\s*do you\b", t, re.I):
        fact = remember(m.group(1))
        return f"Got it, I'll remember: {_lower_first(fact)}."
    m = _FORGET.match(t)
    if m:
        gone = forget(m.group(1))
        return f"Forgotten: {_lower_first(gone)}." if gone else "I don't have anything like that stored."
    for fn in (_reminder_command, _goal_command):
        reply = fn(t)
        if reply:
            return reply
    return None
