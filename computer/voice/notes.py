"""
Apple Notes for JARVIS: "read my latest note", "what did I write about the
pitch?", "any new notes?" -- and new or edited notes in the briefing.

Read-only, through AppleScript (macOS asks once for permission to control
Notes). Everything stays on this Mac; note text only goes to the local model.
"""
from __future__ import annotations

import re
import subprocess
import time
from datetime import datetime

SEP_REC = "␞"   # record separator
SEP_FIELD = "␟"  # field separator
EXCERPT = 1200

# Newest first: title, modified (ISO), folder, first EXCERPT chars of the text.
_SCRIPT = """
tell application "Notes"
  set cutoff to (current date) - (%DAYS% * days)
  set found to (notes whose modification date > cutoff)
  set out to ""
  set k to count of found
  if k > %LIMIT% then set k to %LIMIT%
  repeat with i from 1 to k
    set n to item i of found
    set txt to plaintext of n
    if (length of txt) > %CHARS% then set txt to text 1 thru %CHARS% of txt
    set fld to ""
    try
      set fld to name of container of n
    end try
    set out to out & (name of n) & "%F%" & ((modification date of n) as «class isot» as string) & "%F%" & fld & "%F%" & txt & "%R%"
  end repeat
  return out
end tell
"""

_cache: dict = {}


def fetch(days: int = 30, limit: int = 40, chars: int = EXCERPT) -> list[dict]:
    """Notes changed in the last `days`, newest first (cached 2 minutes)."""
    key = (days, limit, chars)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    script = (_SCRIPT.replace("%DAYS%", str(days)).replace("%LIMIT%", str(limit)).replace("%CHARS%", str(chars))
              .replace("%F%", SEP_FIELD).replace("%R%", SEP_REC))
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return []
    if proc.returncode != 0:
        return []
    notes = []
    for rec in proc.stdout.split(SEP_REC):
        parts = rec.strip("\n").split(SEP_FIELD)
        if len(parts) < 4 or not parts[0].strip():
            continue
        try:
            modified = datetime.fromisoformat(parts[1].strip())
        except ValueError:
            modified = None
        notes.append({"title": parts[0].strip(), "modified": modified, "folder": parts[2].strip(), "text": parts[3].strip()})
    notes.sort(key=lambda n: n["modified"] or datetime.min, reverse=True)
    _cache[key] = (time.time(), notes)
    return notes


def _ago(dt: datetime | None) -> str:
    if not dt:
        return "some time ago"
    secs = (datetime.now() - dt).total_seconds()
    if secs < 3600:
        return f"{max(1, int(secs // 60))} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    return f"{int(secs // 86400)} days ago"


def _clean(text: str, n: int) -> str:
    text = re.sub(r"\s*\n\s*", " / ", text.strip())
    return text[:n] + ("…" if len(text) > n else "")


_STOP = set("a an the my me i about on in of to for what did write wrote notes note any new is was do you can read tell show".split())


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß0-9]+", text.lower()) if w not in _STOP and len(w) > 2}


def search(query: str, days: int = 365) -> list[dict]:
    terms = _terms(query)
    if not terms:
        return []
    notes = fetch(days=days, limit=150, chars=2000)
    scored = []
    for n in notes:
        hay = f"{n['title']} {n['title']} {n['text']}".lower()
        score = sum(hay.count(t) for t in terms)
        if score:
            scored.append((score, n))
    return [n for _s, n in sorted(scored, key=lambda x: -x[0])]


def context_for(question: str, step=lambda *a: None) -> str:
    """Context block for a voice question about Notes."""
    step("Apple Notes", "running", "")
    q = question.lower()
    about = re.search(r"\b(?:about|on|regarding|for|called|named|titled)\s+(.+?)[?.!]*$", q)
    if about and not re.search(r"\b(latest|last|newest|recent|new)\b", about.group(1)):
        hits = search(about.group(1))
        step("Apple Notes", "done", f"{len(hits)} matching")
        if not hits:
            return f"Apple Notes: no note mentions '{about.group(1)}'. Task: say so briefly."
        top = hits[:2]
        blocks = [f"Note '{n['title']}' ({_ago(n['modified'])}): {_clean(n['text'], 900)}" for n in top]
        return ("Apple Notes -- " + " || ".join(blocks) +
                "\nTask: answer the question from these notes in two or three spoken sentences.")
    recent = fetch(days=30, limit=10)
    step("Apple Notes", "done", f"{len(recent)} recent")
    if not recent:
        return "Apple Notes: no notes changed in the last 30 days (or JARVIS isn't allowed to read Notes yet). Task: say so."
    if re.search(r"\b(new|any|recent|changed|added|edited)\b", q) and not re.search(r"\b(read|latest|last)\b", q):
        day = [n for n in recent if n["modified"] and (datetime.now() - n["modified"]).total_seconds() < 86400 * 2]
        listing = "; ".join(f"'{n['title']}' ({_ago(n['modified'])})" for n in (day or recent)[:5])
        return (f"Apple Notes: {len(day)} changed in the last 2 days. Recent: {listing}."
                "\nTask: list the note titles naturally in one or two sentences.")
    n = recent[0]
    return (f"Their latest note is '{n['title']}' (edited {_ago(n['modified'])}, folder {n['folder'] or 'Notes'}): "
            f"{_clean(n['text'], 1100)}\nTask: tell them what the note says in two or three spoken sentences.")


def category() -> dict | None:
    """Briefing card: notes added or edited since yesterday (else left out)."""
    recent = fetch(days=2, limit=15, chars=200)
    fresh = [n for n in recent if n["modified"] and (datetime.now() - n["modified"]).total_seconds() < 86400 * 1.5]
    if not fresh:
        return None
    rows = [{"l": _clean(n["title"], 32), "r": _ago(n["modified"]).upper(), "tone": "info"} for n in fresh[:3]]
    titles = [n["title"] for n in fresh[:3]]
    named = titles[0] if len(titles) == 1 else ", ".join(titles[:-1]) + " and " + titles[-1]
    return {"name": "NOTES", "count": f"{len(fresh)} NEW", "headline": _clean(f"New in Notes: {named}.", 90),
            "items": rows, "say": f"In your notes, you added or changed {len(fresh)} since yesterday: {named}."}


if __name__ == "__main__":
    for n in fetch(days=7, limit=5, chars=80):
        print(n["modified"], "|", n["folder"], "|", n["title"][:50], "|", _clean(n["text"], 60))
