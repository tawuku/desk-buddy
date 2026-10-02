"""
Live data for JARVIS's voice replies: weather, news, web search, Mail.app,
and local project folders. Stdlib only (runs in computer/voice/venv).

Why routing is keyword-based rather than LLM tool-calling: the local model
reads ~22 tokens/s on this Mac, so every token of prompt costs time. Picking
sources in Python is instant, and each fetcher returns a *compact* text
block (a few hundred chars) that's handed to the model to phrase the answer.

Sources are configured in config/jarvis_sources.json.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import remote

JARVIS_DIR = Path(__file__).resolve().parent.parent.parent
SOURCES_FILE = JARVIS_DIR / "config" / "jarvis_sources.json"
LOCAL_MODEL_ENV_FILE = JARVIS_DIR / "config" / ".env.local_model"
LLAMA_CHAT_URL = "http://127.0.0.1:8080/v1/chat/completions"  # remote brain: see remote.py
CACHE_DIR = Path(__file__).resolve().parent / "cache"
BRIEFING_FILE = CACHE_DIR / "briefing.json"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) JARVIS/1.0"

# step(label, status, detail) -- status is "running" | "done" | "failed".
# Drives the HUD panel's progress list; a no-op when nothing is listening.
StepFn = Callable[[str, str, str], None]


def _noop_step(_label: str, _status: str, _detail: str = "") -> None:
    pass


def load_sources() -> dict:
    """config/jarvis_sources.json (personal, git-ignored); the shipped
    example until the installer / you create it."""
    path = SOURCES_FILE if SOURCES_FILE.exists() else SOURCES_FILE.with_name("jarvis_sources.example.json")
    return json.loads(path.read_text())


def _get(url: str, timeout: float = 8, form: dict | None = None) -> bytes:
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


# --- LLM -----------------------------------------------------------------

def _chat_url() -> str:
    return remote.url("/llm/v1/chat/completions") if remote.enabled() else LLAMA_CHAT_URL


def _auth_headers() -> dict:
    if remote.enabled():
        return remote.headers()
    return {"Authorization": f"Bearer {_api_key()}"}


def _api_key() -> str:
    for line in LOCAL_MODEL_ENV_FILE.read_text().splitlines():
        if line.startswith("LLAMA_SERVER_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"LLAMA_SERVER_API_KEY not found in {LOCAL_MODEL_ENV_FILE}")


def llm_chat(messages: list[dict], max_tokens: int = 120, timeout: float = 300) -> tuple[str, dict]:
    """Returns (reply text, llama.cpp timings dict)."""
    body = json.dumps({
        "model": "qwen3-4b-instruct",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.6,
        # Qwen3 small models "think out loud" first unless told not to;
        # harmless for models without a thinking mode.
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode("utf-8")
    req = urllib.request.Request(
        _chat_url(),
        data=body,
        headers={"Content-Type": "application/json", **_auth_headers()},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read())
    reply = (payload["choices"][0]["message"].get("content") or "").strip()
    return reply, payload.get("timings") or {}


def llm_stream(messages: list[dict], max_tokens: int = 120, timeout: float = 300,
               cancel: "Callable[[], bool] | None" = None, stats: dict | None = None):
    """Yield the reply as it's generated (text pieces), so JARVIS can start
    speaking after the first sentence instead of waiting for the whole answer.
    cache_prompt: llama-server reuses the already-processed prefix (system
    prompt + earlier turns), so each turn only reads the new message.
    cancel() -> True stops generation (barge-in). stats gets llama timings."""
    body = json.dumps({
        "model": "qwen3-4b-instruct",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": True,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode("utf-8")
    req = urllib.request.Request(
        _chat_url(),
        data=body,
        headers={"Content-Type": "application/json", **_auth_headers()},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            if cancel and cancel():
                return
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if stats is not None and chunk.get("timings"):
                stats.update(chunk["timings"])
            for choice in chunk.get("choices", []):
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    yield piece


# --- weather (Open-Meteo, no key) -------------------------------------------

_WMO = [
    ((0,), "clear sky"), ((1,), "mainly clear"), ((2,), "partly cloudy"), ((3,), "overcast"),
    ((45, 48), "fog"), ((51, 53, 55, 56, 57), "drizzle"), ((61, 63, 65, 66, 67), "rain"),
    ((71, 73, 75, 77), "snow"), ((80, 81, 82), "rain showers"), ((85, 86), "snow showers"),
    ((95, 96, 99), "thunderstorms"),
]


def _wmo_text(code: int) -> str:
    for codes, text in _WMO:
        if code in codes:
            return text
    return "mixed conditions"


def weather_data(src: dict) -> dict | None:
    """Raw current + today numbers (used by quick replies' template)."""
    city = src["city"]
    qs = urllib.parse.urlencode({
        "latitude": city["lat"], "longitude": city["lon"], "timezone": city.get("timezone", "auto"),
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": 1,
    })
    try:
        d = json.loads(_get(f"https://api.open-meteo.com/v1/forecast?{qs}"))
    except Exception:  # noqa: BLE001
        return None
    return {
        "now_temp": d["current"]["temperature_2m"],
        "now_text": _wmo_text(d["current"]["weather_code"]),
        "min": d["daily"]["temperature_2m_min"][0],
        "max": d["daily"]["temperature_2m_max"][0],
        "rain": d["daily"]["precipitation_probability_max"][0],
    }


def weather(src: dict, step: StepFn = _noop_step) -> str:
    city = src["city"]
    step(f"Weather · {city['name']}", "running", "")
    qs = urllib.parse.urlencode({
        "latitude": city["lat"], "longitude": city["lon"], "timezone": city.get("timezone", "auto"),
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "forecast_days": 2,
    })
    try:
        d = json.loads(_get(f"https://api.open-meteo.com/v1/forecast?{qs}"))
    except Exception as exc:  # noqa: BLE001
        step(f"Weather · {city['name']}", "failed", str(exc)[:60])
        return f"Weather in {city['name']}: unavailable right now."
    cur, day = d["current"], d["daily"]
    now_txt = f"{cur['temperature_2m']:.0f}°C, {_wmo_text(cur['weather_code'])}"
    step(f"Weather · {city['name']}", "done", now_txt)
    return (
        f"Weather in {city['name']} now: {now_txt}, feels like {cur['apparent_temperature']:.0f}°C, "
        f"wind {cur['wind_speed_10m']:.0f} km/h. Today: {day['temperature_2m_min'][0]:.0f} to "
        f"{day['temperature_2m_max'][0]:.0f}°C, {_wmo_text(day['weather_code'][0])}, "
        f"{day['precipitation_probability_max'][0]}% chance of rain. Tomorrow: "
        f"{day['temperature_2m_min'][1]:.0f} to {day['temperature_2m_max'][1]:.0f}°C, "
        f"{_wmo_text(day['weather_code'][1])}."
    )


# --- news (RSS) ------------------------------------------------------------

def news_data(src: dict, step: StepFn = _noop_step, per_feed: int = 3) -> list[dict]:
    """[{"name", "lang", "titles": [...]}] for each feed that answered."""
    out = []
    for feed in src["news_feeds"]:
        label = f"News · {feed['name']}"
        step(label, "running", "")
        try:
            xml = _get(feed["url"]).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            step(label, "failed", str(exc)[:60])
            continue
        titles = []
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
            m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.S)
            if m:
                t = _strip_tags(m.group(1))
                if t and not re.search(r"tagesschau \d|^Liveblog", t, re.I):
                    titles.append(t)
            if len(titles) >= per_feed:
                break
        step(label, "done", f"{len(titles)} headlines")
        if titles:
            out.append({"name": feed["name"], "lang": feed.get("lang", "en"), "titles": titles})
    return out


def news(src: dict, step: StepFn = _noop_step, per_feed: int = 3) -> str:
    lines = [f"{f['name']}: " + " | ".join(f["titles"]) for f in news_data(src, step, per_feed)]
    return "Top headlines -- " + " || ".join(lines) if lines else "News: feeds unreachable right now."


# --- web search (DuckDuckGo HTML endpoint, no key) ---------------------------

_FILLER = re.compile(
    r"^(hey |ok |okay )?(jarvis[, ]*)?(can you |could you |please )*"
    r"(search( the web)?( for)?|look up|google|find out|tell me)\s*",
    re.I,
)


def web_search(query: str, step: StepFn = _noop_step, n: int = 3) -> str:
    q = _FILLER.sub("", query).strip(" ?.!") or query
    label = f"Web search · {q[:40]}"
    step(label, "running", "")
    results: list[str] = []
    try:
        # POST, like DuckDuckGo's own HTML search form -- a GET from a script
        # gets served the homepage instead of results.
        page = _get("https://html.duckduckgo.com/html/", timeout=10, form={"q": q}).decode("utf-8", "replace")
        titles = [_strip_tags(t) for t in re.findall(r'class="result__a"[^>]*>(.*?)</a>', page, re.S)]
        snippets = [_strip_tags(s) for s in re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)]
        results = [f"{t}: {s}" for t, s in zip(titles, snippets) if s][:n]
    except Exception:  # noqa: BLE001 -- fall through to Wikipedia
        pass
    if not results:
        # Fallback: Wikipedia's public search API (no key; less current).
        try:
            d = json.loads(_get("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
                {"action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": n})))
            results = [f"{r['title']} (Wikipedia): {_strip_tags(r['snippet'])}" for r in d["query"]["search"]]
        except Exception as exc:  # noqa: BLE001
            step(label, "failed", str(exc)[:60])
            return "Web search: unavailable right now."
    step(label, "done", f"{len(results)} results")
    if not results:
        return f"Web search for '{q}': no results."
    return f"Web results for '{q}' -- " + " || ".join(r[:260] for r in results)


# --- email (Mail.app via AppleScript) ----------------------------------------

_MAIL_SCRIPT = """
tell application "Mail"
  set unreadTotal to unread count of inbox
  set out to (unreadTotal as text) & linefeed
  set msgs to (messages of inbox whose read status is false)
  set n to count of msgs
  if n > {limit} then set n to {limit}
  repeat with i from 1 to n
    set m to item i of msgs
    set out to out & (sender of m) & tab & (subject of m) & tab & ((date received of m) as «class isot» as string) & linefeed
  end repeat
  return out
end tell
"""


def email_data(src: dict, step: StepFn = _noop_step) -> dict:
    """{"error": None | "timeout" | "permission" | "failed", "total": int,
    "messages": [(iso_date, sender, subject), ...] newest first}."""
    limit = int(src.get("email", {}).get("max_messages", 8))
    step("Mail · unread inbox", "running", "")
    try:
        proc = subprocess.run(
            ["osascript", "-e", _MAIL_SCRIPT.replace("{limit}", str(limit))],
            capture_output=True, text=True, timeout=45,
        )
    except subprocess.TimeoutExpired:
        step("Mail · unread inbox", "failed", "Mail.app timed out")
        return {"error": "timeout", "total": 0, "messages": []}
    if proc.returncode != 0:
        err = proc.stderr.strip()
        step("Mail · unread inbox", "failed", "no permission" if "-1743" in err else err[:60])
        return {"error": "permission" if "-1743" in err else "failed", "total": 0, "messages": []}
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    total = int(lines[0]) if lines and lines[0].strip().isdigit() else 0
    msgs = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) >= 3:
            sender = re.sub(r"\s*<[^>]+>", "", parts[0]).strip('" ')
            msgs.append((parts[2], sender, parts[1]))
    msgs.sort(reverse=True)  # ISO dates sort chronologically; newest first
    step("Mail · unread inbox", "done", f"{total} unread")
    return {"error": None, "total": total, "messages": msgs}


def email(src: dict, step: StepFn = _noop_step) -> str:
    d = email_data(src, step)
    if d["error"] == "timeout":
        return "Email: Mail.app didn't respond in time."
    if d["error"] == "permission":
        return "Email: JARVIS isn't allowed to control Mail yet (macOS Automation permission needed)."
    if d["error"]:
        return "Email: couldn't read Mail.app right now."
    if not d["messages"]:
        return f"Email: {d['total']} unread in the inbox."
    shown = "; ".join(f"from {s}: \"{subj[:70]}\"" for _d, s, subj in d["messages"])
    return f"Email: {d['total']} unread. Newest: {shown}."


# --- projects (local folders) ------------------------------------------------

_SKIP_NAMES = {".DS_Store", "Icon\r", ".localized"}
_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist", ".cache"}


def _ago(ts: float) -> str:
    delta = time.time() - ts
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    return f"{int(delta // 86400)} days ago"


def _scan_project(proj: dict, max_files: int = 5000) -> dict:
    name_filter = (proj.get("name_filter") or "").lower()
    exclude = _SKIP_DIRS | set(proj.get("exclude_dirs", []))
    files: list[tuple[float, str]] = []
    for raw in proj["paths"]:
        root = Path(os.path.expanduser(raw))
        if not root.exists():
            continue
        # A name-filtered project (files in a shared folder like Downloads)
        # only looks two levels deep -- that folder isn't the project itself.
        max_depth = 2 if name_filter else 12
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).parts) - base_depth
            dirnames[:] = [d for d in dirnames if d not in exclude and not d.startswith(".") and depth < max_depth]
            for fn in filenames:
                if fn in _SKIP_NAMES or fn.startswith("."):
                    continue
                if name_filter and name_filter not in fn.lower() and name_filter not in dirpath.lower():
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    files.append((os.path.getmtime(full), os.path.relpath(full, root)))
                except OSError:
                    continue
                if len(files) >= max_files:
                    break
    files.sort(reverse=True)
    now = time.time()
    return {
        "total": len(files),
        "day": [f for f in files if now - f[0] < 86400],
        "week": [f for f in files if now - f[0] < 7 * 86400],
        "latest": files[:3],
    }


def _open_tasks(path: str) -> list[str]:
    try:
        text = Path(os.path.expanduser(path)).read_text()
    except OSError:
        return []
    tasks = [m.group(1).strip() for m in re.finditer(r"^-\s*\[ \]\s*(.+)$", text, re.M)]
    return [t for t in tasks if not t.startswith("(add your first task")]


def project_summary(proj: dict, with_about: bool = False) -> str:
    s = _scan_project(proj)
    about = f" ({proj['about']})" if with_about and proj.get("about") else ""
    if s["total"] == 0:
        return f"{proj['name']}{about}: no files found."
    parts = [f"{proj['name']}{about}: {s['total']} files"]
    if s["day"]:
        names = ", ".join(Path(p).name for _t, p in s["day"][:3])
        parts.append(f"{len(s['day'])} changed in the last 24h ({names})")
    elif s["week"]:
        names = ", ".join(Path(p).name for _t, p in s["week"][:3])
        parts.append(f"nothing today; {len(s['week'])} changed this week ({names})")
    else:
        t, p = s["latest"][0]
        parts.append(f"no changes this week; last edit {Path(p).name} {_ago(t)}")
    if proj.get("tasks_file"):
        tasks = _open_tasks(proj["tasks_file"])
        parts.append(f"{len(tasks)} open tasks" + (f" (oldest: {tasks[0][:60]})" if tasks else ""))
    return "; ".join(parts) + "."


def projects(src: dict, only: list[str] | None = None, step: StepFn = _noop_step) -> str:
    out = []
    for proj in src["projects"]:
        if only and proj["name"] not in only:
            continue
        step(f"Project · {proj['name']}", "running", "")
        # Background notes only when asking about specific projects -- the
        # all-projects overview stays short (prompt tokens cost ~45ms each).
        summary = project_summary(proj, with_about=bool(only))
        detail = summary.split("files; ", 1)[1].split(";")[0].strip() if "files; " in summary else "scanned"
        step(f"Project · {proj['name']}", "done", detail[:48])
        out.append(summary)
    return "Projects -- " + " ".join(out)


# --- routing -----------------------------------------------------------------

_KW = {
    "weather": r"\b(weather|temperature|rain(ing)?|forecast|wetter|umbrella|sunny|snow(ing)?|cold outside|hot outside|degrees)\b",
    "news": r"\b(news|headlines?|nachrichten|happening in the world|world events)\b",
    "email": r"\b(e-?mails?|mails?|inbox|unread)\b",
    # "Updates" alone means the briefing; "updates on <project>" is a project question.
    "briefing": r"\b(brief(ing)?|brie?(v|ph)ing|breifing|brief me|daily update|morning update|catch me up|what did i miss|update on everything|"
                r"my day|status report|what'?s new|update me|(any|my|the|daily|latest|today'?s|an|some) updates?(?!\s+(on|about|for|from|to|of)\b))\b",
    "projects": r"\b(projects?|work(ing)? on|progress|updates?)\b",
    "search": r"\b(search|look up|google|who (is|was|won)|latest|current(ly)?|price of|score|release date|when (is|was|does)|how much (is|does))\b",
    # Your own sites' admin numbers (business.py) -- "how's <site> doing?"
    # (Your own site names are added from config/business.json in route().)
    "business": r"\b(how('?s| is| are) (the (site|shop|platform|business)|my (sites|websites|business|platforms|shop))( \w+)? (doing|performing|going)|"
                r"business (update|stats|numbers)|how('?s| is) business|sign ?ups?|signups|registrations|revenue|sales|pipeline|"
                r"(new |any )?(orders|leads|deals)|visitors|traffic|admin (portal|dashboard)|approvals?|site stats)\b",
    "notes": r"\b(my notes?|apple notes|notes app|latest note|last note|newest note|new notes?|recent notes?|in my notes|"
             r"what did i (write|note|jot)|note (about|on|called)|notes (about|on))\b",
    "health": r"\b(how (did|have) i (sleep|slept)|my sleep|sleep (last night|score)|how many steps|my steps|steps (today|so far)|"
              r"how active|my activity|resting heart|heart rate|calories (burned|today)|exercise minutes|my health)\b",
    "goals": r"\b(my goals?|goal progress|how am i doing on|am i on track|my habits?|who should i call|who (do|should) i call)\b",
    "activity": r"\b(what (am|was|have|did) i (been )?(doing|working on|up to|do|work on|change)|"
                r"(my|the) (computer|mac|screen|laptop)|on (my|the) (computer|mac|screen)|"
                r"recent(ly)? (files|changed|edited|worked)|(which|what) files|my day so far|"
                r"how long (have|did) i|what('s| is) open|what app)\b",
}


# On-the-spot tasks (actions.py) -- checked first; each runs alone.
_ACTIONS = [
    ("read_doc", r"\b(what('?s| is) in (it|that)|(summari[sz]e|read( me)?|explain|go through|tell me (more )?about) (it|that|this)$|"
                 r"what('?s| is) (in|on) (this|the|my) (document|file|pdf|page|tab)|"
                 r"(summari[sz]e|read|report on|explain|go through) (this|the|my|the current|the open|what'?s on (my|the)) "
                 r"(document|file|pdf|page|tab|screen)|this (document|file|pdf) (about|say))\b"),
    # "go to" / "visit" / "check out" only with a site word or an address --
    # "go to sleep" must never open the browser.
    ("web_preview", r"\bpreview\b|"
                    r"\b(open|pull up|show me|look at|check out|visit|go to|browse)\b.*\b(website|web ?site|site|web ?page|online|on the (web|internet))\b|"
                    r"\b(open|pull up|show me|look at|check out|visit|go to|browse)\s+(the\s+)?[\w-]+\.(com|de|org|net|io|ai|co|app|dev)\b"),
    ("open_doc", r"^\W*(hey,? jarvis\W*)?((can|could) you |please |jarvis,? )*(open|pull up|bring up|launch)\b"),
]


def _site_named(t: str) -> str | None:
    """A site (or group) from config/business.json mentioned in t -- also as
    speech recognition tends to hear it ("my shop" for "MyShop")."""
    try:
        import business
        names = business.names()
    except Exception:  # noqa: BLE001
        return None
    for n in sorted(names, key=len, reverse=True):
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", n).lower()          # "MyShop" -> "my shop"
        variants = {n.lower(), spaced, spaced.replace(" ", "")}
        if any(re.search(rf"\b{re.escape(v)}\b", t) for v in variants if v):
            return n
    return None


def route(text: str, src: dict) -> dict:
    """Decide which sources a question needs. Returns {"skills": [...],
    "projects": [names] or None}."""
    t = text.lower()
    site = _site_named(t)
    for name, pat in _ACTIONS:
        if re.search(pat, t):
            return {"skills": [name], "projects": None}
    skills = [k for k, pat in _KW.items() if re.search(pat, t)]
    skills = list(dict.fromkeys(skills))
    named = [
        p["name"] for p in src["projects"]
        if any(re.search(rf"\b{re.escape(n.lower())}\b", t) for n in [p["name"], *p.get("aliases", [])])
    ]
    if named and "projects" not in skills:
        skills.append("projects")
    if "briefing" in skills:
        return {"skills": ["briefing"], "projects": None}
    if "notes" in skills:
        return {"skills": ["notes"], "projects": None}
    if "business" in skills or (site and re.search(r"\b(doing|performing|going|stats|numbers)\b", t)):
        return {"skills": ["business"], "projects": None}
    # "What was I working on?" is about the Mac, not a project status
    # report -- unless a project is named.
    if "activity" in skills and not named:
        skills = [s for s in skills if s not in ("projects", "search")]
    # "news about X" / "latest on X" is a search, not the headline feed.
    if "news" in skills and re.search(r"\b(news|latest) (about|on|from)\b", t):
        skills = list(dict.fromkeys([s for s in skills if s != "news"] + ["search"]))
    # A named project or an explicit "project" question beats generic words
    # like "latest"/"updates" that also match search.
    if "projects" in skills and "search" in skills and not re.search(r"\b(search|look up|google)\b", t):
        skills.remove("search")
    return {"skills": skills, "projects": named or None}


def gather(plan: dict, text: str, step: StepFn = _noop_step) -> str:
    src = load_sources()
    blocks = []
    for skill in plan["skills"]:
        if skill == "weather":
            blocks.append(weather(src, step))
        elif skill == "news":
            blocks.append(news(src, step))
        elif skill == "email":
            blocks.append(email(src, step))
        elif skill == "projects":
            blocks.append(projects(src, plan.get("projects"), step))
        elif skill == "search":
            blocks.append(web_search(text, step))
        elif skill == "business":
            import business
            step("Your sites", "running", "")
            blocks.append(business.summary(_site_named(text.lower())))
            step("Your sites", "done", "")
        elif skill == "notes":
            import notes
            blocks.append(notes.context_for(text, step))
        elif skill == "health":
            import health
            step("Apple Health", "running", "")
            blocks.append(health.summary())
            step("Apple Health", "done", "")
        elif skill == "goals":
            import findings
            step("Your goals", "running", "")
            blocks.append(findings.goals_summary())
            step("Your goals", "done", "")
        elif skill in ("open_doc", "read_doc", "web_preview"):
            import actions
            fn = {"open_doc": actions.open_document, "read_doc": actions.front_document,
                  "web_preview": actions.web_preview}[skill]
            blocks.append(fn(step) if skill == "read_doc" else fn(text, step))
        elif skill == "activity":
            import activity
            step("Your Mac today", "running", "")
            blocks.append(activity.today_summary())
            step("Your Mac today", "done", "")
            step("Recently changed files", "running", "")
            files = activity.recent_files(24, 10, src.get("recent_files_folders"))
            step("Recently changed files", "done", files.split(" (", 1)[0][:40] if files else "")
            blocks.append(files)
    return "\n".join(blocks)


# --- daily briefing ------------------------------------------------------------

def load_briefing(max_age_hours: float = 12) -> dict | None:
    try:
        data = json.loads(BRIEFING_FILE.read_text())
    except (OSError, ValueError):
        return None
    if time.time() - data.get("generated_at", 0) > max_age_hours * 3600:
        return None
    return data


def build_briefing(step: StepFn = _noop_step) -> dict:
    """Gather everything, have the model write a ~1 minute spoken briefing,
    and cache it. Takes a minute or two on this Mac -- run ahead of time by
    briefing.py (com.jarvis.briefing, every morning + at login)."""
    src = load_sources()
    context = "\n".join([
        weather(src, step),
        email(src, step),
        projects(src, None, step),
        news(src, step, per_feed=2),
    ])
    step("Writing briefing", "running", "")
    now = datetime.now()
    system = (
        f"You are JARVIS, {src['user_name']}'s personal assistant. It is "
        f"{now.strftime('%A, %B %d, %H:%M')}. Write a spoken daily briefing of "
        "about 120-170 words from ONLY the data below: a one-line greeting, the "
        "weather, the email highlights (who wrote, what about), one sentence per "
        "project on what moved or what's waiting, then the two most important "
        "headlines. Plain spoken English, no lists, no markdown, no emoji. Never "
        "invent details that aren't in the data."
    )
    started = time.monotonic()
    text, timings = llm_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": context}],
        max_tokens=280,
        timeout=900,
    )
    step("Writing briefing", "done", f"{time.monotonic() - started:.0f}s")
    data = {
        "generated_at": time.time(),
        "generated_for": now.strftime("%Y-%m-%d"),
        "text": text,
        "context": context,
        "timings": timings,
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = BRIEFING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(BRIEFING_FILE)
    return data
