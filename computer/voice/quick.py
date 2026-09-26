"""
Instant replies for everyday talk ("goodnight", "go to sleep", "thanks",
"what time is it", "louder", ...) -- no language model, so they answer in
about a second instead of 10+. The catalog lives in config/quick_replies.json
(editable); this module matches what was said against it and runs the few
built-in live handlers (time, date, weather, volume, repeat).

Matching is deliberately strict: the *whole* utterance has to be one of the
catalog phrases (after normalizing and trimming filler words), so "what time
should I leave for the airport" never gets hijacked by the "time" intent.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import skills

CATALOG_FILE = skills.JARVIS_DIR / "config" / "quick_replies.json"

# Words trimmed from either end of an utterance before matching
# ("hey jarvis, goodnight!" -> "goodnight"; "thanks buddy" -> "thanks").
_EDGE_FILLER = {
    "hey", "hi", "jarvis", "please", "ok", "okay", "so", "just", "um", "uh", "well",
    "buddy", "mate", "man", "bro", "dude", "now", "then", "oh", "and", "alright", "yo",
    "today", "right",
}
# Words that mean "yes" / "no" in reply to an offer ("Want your briefing?").
_YES = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please", "go ahead", "go on",
        "do it", "yes please", "sure thing", "of course", "absolutely", "lets hear it",
        "lets go", "why not", "definitely", "alright", "sounds good"}
_NO = {"no", "nope", "nah", "not now", "later", "no thanks", "no thank you", "maybe later", "not yet", "skip it"}

_catalog_cache: tuple[float, list[dict]] | None = None


def _catalog() -> list[dict]:
    """Re-read the JSON whenever it changes on disk, so edits apply live."""
    global _catalog_cache
    mtime = CATALOG_FILE.stat().st_mtime
    if _catalog_cache is None or _catalog_cache[0] != mtime:
        _catalog_cache = (mtime, json.loads(CATALOG_FILE.read_text())["intents"])
    return _catalog_cache[1]


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'")
    t = t.replace("'", "")  # what's -> whats, i'm -> im
    t = re.sub(r"[^a-z0-9äöüß ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _candidates(text: str) -> list[str]:
    """The normalized utterance, then progressively filler-trimmed versions."""
    words = normalize(text).split()
    out = [" ".join(words)]
    while len(words) > 1 and (words[0] in _EDGE_FILLER or words[-1] in _EDGE_FILLER):
        if words[0] in _EDGE_FILLER:
            words = words[1:]
        elif words[-1] in _EDGE_FILLER:
            words = words[:-1]
        out.append(" ".join(words))
    return out


def match(text: str, after_question: bool = False) -> dict | None:
    """Return the matching intent, or None to let the model handle it.
    after_question: JARVIS's last line was a real (model) question -- then a
    bare "yes"/"good"/"okay" is an answer to it, not small talk."""
    cands = _candidates(text)
    for intent in _catalog():
        if after_question and intent.get("defer_after_question"):
            continue
        phrases = {normalize(p) for p in intent.get("match", [])}
        if any(c in phrases for c in cands):
            return intent
    return _closer(text) or _all_closers(text)


# A short utterance that *contains* a conversation ender ("Jarvis, you can
# go to sleep for now", "okay stop", "alright that's all, thanks") ends the
# conversation too -- unless it also asks for something.
_REQUEST_WORDS = {"what", "whats", "how", "why", "when", "where", "who", "which", "can", "could", "would",
                  "will", "open", "tell", "show", "give", "play", "search", "find", "check", "remind", "set", "send", "call"}
_CLOSER_MAX_WORDS = 8
_CLOSER_FILLER = {"you", "can", "for", "now", "it", "that", "thats", "for now", "a", "lot", "much", "very", "all", "go", "to"}


def _closer(text: str) -> dict | None:
    words = normalize(text).split()
    if not words or len(words) > _CLOSER_MAX_WORDS or "?" in text:
        return None
    padded = f" {' '.join(words)} "
    best = None
    for intent in _catalog():
        if not intent.get("end"):
            continue
        for phrase in intent.get("match", []):
            ph = normalize(phrase)
            if not ph or f" {ph} " not in padded:
                continue
            # Asking for something besides the closer ("thanks, what's the
            # weather", "stop the timer and tell me the news") isn't a
            # goodbye: at most two other non-filler words, none a request.
            rest = [w for w in padded.replace(f" {ph} ", " ").split() if w not in _EDGE_FILLER and w not in _CLOSER_FILLER]
            if len(rest) > 2 or any(w in _REQUEST_WORDS for w in rest):
                continue
            if best is None or len(ph) > len(best[1]):
                best = (intent, ph)
    return best[0] if best else None


def _all_closers(text: str) -> dict | None:
    """Longer goodbyes made only of closers and filler, clause by clause:
    "Okay, thank you, go to sleep. You can go to sleep now, Jarvis." (A
    clause may carry one unknown word -- usually a misheard "Jarvis".)"""
    clauses = [c for c in re.split(r"[.!?,;]+", text) if normalize(c)]
    if len(clauses) < 2:
        return None
    found = None
    for clause in clauses:
        words = [w for w in normalize(clause).split() if w not in _EDGE_FILLER and w not in _CLOSER_FILLER]
        intent = _closer(clause)
        if intent:
            found = found or intent
        elif len(words) > 1 or any(w in _REQUEST_WORDS for w in words):
            return None
    # Prefer "sleep"/"stop" over "thanks" when both were said.
    return found


def _starts_with(text: str, phrases: set[str]) -> bool:
    """'Sure, go ahead' / 'yeah let's hear it' -> the first 1-2 words decide."""
    words = normalize(text).split()
    return bool(words) and (words[0] in phrases or " ".join(words[:2]) in phrases)


def is_yes(text: str) -> bool:
    return _starts_with(text, _YES) and not _starts_with(text, _NO)


def is_no(text: str) -> bool:
    return _starts_with(text, _NO)


def fill(template: str) -> str:
    return template.replace("{name}", skills.load_sources().get("user_name") or "there")


def static_phrases() -> list[str]:
    """Every fixed reply, for pre-rendering in JARVIS's voice."""
    return [fill(t) for intent in _catalog() for t in intent.get("say", [])]


# --- built-in live handlers ------------------------------------------------

def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _osascript(script: str) -> None:
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)


def run_handler(name: str, last_reply: str | None) -> str | None:
    """Live text for handler intents (None = nothing extra to say)."""
    now = datetime.now()
    if name == "time":
        return f"It's {now.strftime('%-I:%M %p')}."
    if name == "date":
        return f"Today is {now.strftime('%A')}, {now.strftime('%B')} {_ordinal(now.day)}."
    if name == "day":
        return f"It's {now.strftime('%A')}."
    if name == "weather":
        src = skills.load_sources()
        w = skills.weather_data(src)
        if not w:
            return "I can't reach the weather service right now."
        return (
            f"Right now it's {w['now_temp']:.0f} degrees and {w['now_text']} in {src['city']['name']}. "
            f"Today's between {w['min']:.0f} and {w['max']:.0f}, with a {w['rain']} percent chance of rain."
        )
    if name == "repeat":
        return last_reply or "I haven't said anything yet."
    if name == "volume_up":
        _osascript("set volume output volume ((output volume of (get volume settings)) + 15)")
        return None
    if name == "volume_down":
        _osascript("set volume output volume ((output volume of (get volume settings)) - 15)")
        return None
    if name == "mute":
        _osascript("set volume output muted true")
        return None
    if name == "unmute":
        _osascript("set volume output muted false")
        return None
    if name == "briefing_ready":
        return None  # the 'say' line already offers it; see wake_listener
    return None


def reply_for(intent: dict, last_reply: str | None) -> str:
    """The line to speak for a matched intent (may be empty, e.g. mute)."""
    handler = intent.get("handler")
    live = run_handler(handler, last_reply) if handler else None
    if handler == "briefing_ready" and not skills.load_briefing(max_age_hours=12):
        return fill(f"Good morning, {{name}}! How did you sleep?")
    if live and not intent.get("say"):
        return live
    says = intent.get("say") or []
    text = fill(random.choice(says)) if says else ""
    return f"{text} {live}".strip() if live else text
