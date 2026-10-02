"""
Everyday Mac tasks by voice -- instant, no model:

  Music     "play some music", "play Burna Boy", "pause the music", "next song",
            "previous song", "what's playing?", "shuffle on"
  Volume    "set the volume to 40", "volume 70 percent", "max volume"
  Directions "how do I get to Frankfurt?", "directions to the airport by
            bike", "how long to drive to Mannheim?" -- says the time and
            distance and opens the route in Apple Maps
  Mac       "take a screenshot", "lock my screen", "how's my battery?",
            "turn on dark mode", "quit Spotify"

handle(text) returns what JARVIS should say, or None if it isn't one of these.
Music uses Spotify when it's installed (Music otherwise). Directions look up
the place on OpenStreetMap (Nominatim) and the route on OSRM -- the only
network calls here, made only when you ask for directions.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import skills

USER_AGENT = "desk-buddy-jarvis/1.0 (local voice assistant)"


def _osa(script: str, timeout: float = 5) -> str:
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        return ""


# --- music -----------------------------------------------------------------------

def _player() -> str:
    """The app to control: whichever is playing, else Spotify if installed."""
    for app in ("Spotify", "Music"):
        state = _osa(f'if application "{app}" is running then tell application "{app}" to return player state as text')
        if state == "playing":
            return app
    return "Spotify" if Path("/Applications/Spotify.app").exists() else "Music"


_PLAY = re.compile(r"^(?:(?:can you |could you |please |jarvis,? )*)(?:play|put on|start playing)\s+(.+?)(?:\s+on\s+(spotify|apple music|music))?[.!?]*$", re.I)
_GENERIC = re.compile(r"^(?:some |the |my )?(?:music|songs?|tunes|something|a song|it|playlist)(?: again)?$|^(?:some )?music please$", re.I)
_PAUSE = re.compile(r"\b(pause|stop|mute)\b.*\b(music|song|spotify|playback|track)\b|^\s*pause( it| please)?[.!]*$", re.I)
_RESUME = re.compile(r"\b(resume|continue|unpause)\b.*\b(music|song|playing|playback)?\b|^\s*(resume|unpause)[.!]*$", re.I)
_NEXT = re.compile(r"\b(?:next|skip(?: to the next)?) (?:the |this )?(?:song|track|one)\b|^\W*(?:(?:please|jarvis),? )?(?:next|skip)(?: it| this| please)?\W*$", re.I)
_PREV = re.compile(r"\b(previous|last|go back)\b.*\b(song|track)\b", re.I)
_WHAT = re.compile(r"\b(what('?s| is) (this )?(song|playing|track)|what song is (this|playing)|who (sings|is singing) (this|that))\b", re.I)
_SHUFFLE = re.compile(r"\bshuffle\b(?:.*\b(on|off)\b)?", re.I)


def _now_playing(app: str) -> str | None:
    info = _osa(f'if application "{app}" is running then tell application "{app}"\n'
                f' if player state is playing then return (name of current track) & " -- " & (artist of current track)\n'
                f'end tell')
    return info or None


def _play_query(query: str, app: str) -> str:
    """Play something named. Music: first match in your library. Spotify:
    AppleScript can't search, so the results open in Spotify."""
    q = query.strip(" .!?")
    if app == "Music":
        esc = q.replace('"', '\\"')
        hit = _osa(f'tell application "Music"\n set found to (every track of library playlist 1 whose name contains "{esc}" or artist contains "{esc}" or album contains "{esc}")\n'
                   f' if (count of found) > 0 then\n  play item 1 of found\n  return (name of item 1 of found) & " -- " & (artist of item 1 of found)\n end if\nend tell\nreturn ""', timeout=10)
        if hit:
            return f"Playing {hit.replace(' -- ', ' by ')}."
        return f"I couldn't find {q} in your Music library."
    subprocess.Popen(["open", f"spotify:search:{urllib.parse.quote(q)}"])
    return f"I've opened Spotify with results for {q} -- tap play on the one you want."


def music(text: str) -> str | None:
    t = text.strip()
    if _WHAT.search(t):
        for app in ("Spotify", "Music"):
            np = _now_playing(app)
            if np:
                return f"This is {np.replace(' -- ', ' by ')}."
        return "Nothing is playing right now."
    if _PAUSE.search(t):
        app = _player()
        _osa(f'if application "{app}" is running then tell application "{app}" to pause')
        return "Paused."
    if _NEXT.search(t):
        app = _player()
        _osa(f'tell application "{app}" to next track')
        time.sleep(0.6)
        np = _now_playing(app)
        return f"Next up: {np.replace(' -- ', ' by ')}." if np else "Skipped."
    if _PREV.search(t):
        app = _player()
        _osa(f'tell application "{app}" to previous track')
        return "Going back."
    if _SHUFFLE.search(t) and re.search(r"\b(music|shuffle on|shuffle off|turn|set)\b", t, re.I):
        app = _player()
        on = (_SHUFFLE.search(t).group(1) or "on").lower() == "on"
        prop = "shuffling" if app == "Spotify" else "shuffle enabled"
        _osa(f'tell application "{app}" to set {prop} to {"true" if on else "false"}')
        return f"Shuffle {'on' if on else 'off'}."
    if _RESUME.search(t) and not _PLAY.match(t):
        app = _player()
        _osa(f'tell application "{app}" to play')
        return "Resuming."
    m = _PLAY.match(t)
    if m:
        what, where = m.group(1), (m.group(2) or "").lower()
        app = "Music" if where in ("apple music", "music") else "Spotify" if where == "spotify" else _player()
        if _GENERIC.match(what.strip(" .!?")):
            _osa(f'tell application "{app}" to play')
            time.sleep(0.8)
            np = _now_playing(app)
            return f"Playing {np.replace(' -- ', ' by ')}." if np else f"Starting {app}."
        return _play_query(what, app)
    return None


# --- volume ----------------------------------------------------------------------

_VOL_SET = re.compile(r"\b(?:set |turn |put )?(?:the )?volume (?:to |at |on )?(\d{1,3})\s*(?:%|percent)?\b|\b(\d{1,3})\s*(?:%|percent) volume\b", re.I)
_VOL_MAX = re.compile(r"\b(max(imum)?|full) volume\b|\bvolume (all the way )?up (to )?(max|full)\b", re.I)


def volume(text: str) -> str | None:
    if _VOL_MAX.search(text):
        _osa("set volume output volume 100")
        return "Volume at maximum."
    m = _VOL_SET.search(text)
    if m:
        level = max(0, min(100, int(m.group(1) or m.group(2))))
        _osa(f"set volume output volume {level}\nset volume without output muted")
        return f"Volume set to {level} percent."
    return None


# --- directions --------------------------------------------------------------------

_DIRECTIONS = re.compile(
    r"\b(?:directions|route|navigate|navigation|how (?:do|can) i (?:get|go)|how (?:long|far)(?: does it take| is it)?(?: to (?:get|drive|walk|cycle|bike))?|"
    r"take me|way)\s+(?:me\s+)?(?:to|from here to)\s+(.+?)"
    r"(?:\s+by\s+(car|bike|bicycle|foot|walking|train|transit|public transport|bus))?(?:\s+from\s+(.+?))?[?.!]*$", re.I)
_HOW_FAR = re.compile(r"\bhow (?:long|far) (?:is it )?(?:to (?:drive|walk|cycle|bike) )?(?:to|until)\s+(.+?)[?.!]*$", re.I)


def _get_json(url: str, timeout: float = 10):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _geocode(place: str, near: dict | None) -> tuple[float, float, str] | None:
    params = {"q": place, "format": "json", "limit": 1}
    if near:  # prefer results around home
        lat, lon = near["lat"], near["lon"]
        params.update(viewbox=f"{lon - 3},{lat + 2},{lon + 3},{lat - 2}", bounded=0)
    try:
        res = _get_json("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params))
    except Exception:  # noqa: BLE001
        return None
    if not res:
        return None
    r = res[0]
    return float(r["lat"]), float(r["lon"]), r.get("display_name", place).split(",")[0]


def _duration(seconds: float) -> str:
    mins = int(round(seconds / 60))
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    h, m = divmod(mins, 60)
    return f"{h} hour{'s' if h != 1 else ''}" + (f" {m} minute{'s' if m != 1 else ''}" if m else "")


def directions(text: str) -> str | None:
    m = _DIRECTIONS.search(text)
    dest, mode, origin_name = (m.group(1), m.group(2), m.group(3)) if m else (None, None, None)
    if not dest:
        m2 = _HOW_FAR.search(text)
        if not m2:
            return None
        dest = m2.group(1)
        mode = "foot" if re.search(r"\bwalk", text, re.I) else "bike" if re.search(r"\b(cycle|bike)", text, re.I) else None
    dest = re.sub(r"^(the nearest|the|a)\s+", lambda x: "" if x.group(1).lower() in ("the", "a") else x.group(0), dest.strip(" ,.?!"), flags=re.I)
    mode = (mode or "car").lower()
    profile, flag, label = {"car": ("driving", "d", "by car"), "bike": ("cycling", "w", "by bike"), "bicycle": ("cycling", "w", "by bike"),
                            "foot": ("walking", "w", "on foot"), "walking": ("walking", "w", "on foot"),
                            "train": (None, "r", "by public transport"), "transit": (None, "r", "by public transport"),
                            "public transport": (None, "r", "by public transport"), "bus": (None, "r", "by public transport")}[mode]
    # Maps plans from your real current location (empty saddr).
    subprocess.Popen(["open", "maps://?" + urllib.parse.urlencode({"daddr": dest, "dirflg": flag})])
    home = skills.load_sources().get("city") or {}
    origin = _geocode(origin_name, home) if origin_name else ((home["lat"], home["lon"], home["name"]) if home.get("lat") else None)
    target = _geocode(dest, home)
    if not origin or not target or not profile:
        return f"I've opened directions to {dest} in Maps."
    servers = {"driving": "https://router.project-osrm.org/route/v1/driving/",
               "cycling": "https://routing.openstreetmap.de/routed-bike/route/v1/driving/",
               "walking": "https://routing.openstreetmap.de/routed-foot/route/v1/driving/"}
    try:
        route = _get_json(f"{servers[profile]}{origin[1]},{origin[0]};{target[1]},{target[0]}?overview=false")["routes"][0]
    except Exception:  # noqa: BLE001
        return f"I've opened directions to {dest} in Maps."
    km = route["distance"] / 1000
    dist = f"{km:.0f} kilometres" if km >= 10 else f"{km:.1f} kilometres"
    arrive = datetime.fromtimestamp(time.time() + route["duration"]).strftime("%-I:%M %p")
    return (f"{target[2]} is about {_duration(route['duration'])} {label} from {origin[2]}, {dist} -- "
            f"you'd get there around {arrive} if you left now. The route is open in Maps.")


# --- the Mac itself ------------------------------------------------------------------

_SCREENSHOT = re.compile(r"\b(take|grab|make) (a |me a )?screenshot\b|\bscreenshot (this|the screen|my screen)\b", re.I)
_LOCK = re.compile(r"\block (my |the )?(screen|computer|mac|pc)\b", re.I)
_BATTERY = re.compile(r"\b(battery|charge level|how much charge|am i charging)\b", re.I)
_DARK = re.compile(r"\b(dark|light) mode\b", re.I)
_QUIT = re.compile(r"^(?:please |jarvis,? )?(?:quit|close) (?:the )?(?:app )?([\w .-]+?)(?: app)?[.!]*$", re.I)


def mac(text: str) -> str | None:
    if _SCREENSHOT.search(text):
        path = Path.home() / "Desktop" / f"Screenshot {datetime.now():%Y-%m-%d at %H.%M.%S}.png"
        subprocess.run(["screencapture", "-x", str(path)], timeout=10)
        return "Screenshot saved to your Desktop." if path.exists() else "I couldn't take the screenshot -- macOS may need Screen Recording permission for JARVIS."
    if _LOCK.search(text):
        subprocess.Popen(["pmset", "displaysleepnow"])
        return "Locking your screen."
    if _BATTERY.search(text):
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
        m = re.search(r"(\d+)%;\s*([\w ]+?);\s*(\d+:\d+|\(no estimate\))?", out)
        if not m:
            return "This Mac doesn't have a battery -- it's on mains power."
        pct, state, left = m.group(1), m.group(2).strip(), m.group(3)
        extra = ""
        if left and ":" in left and left != "0:00":
            h, mi = left.split(":")
            extra = f", about {int(h)} hours {int(mi)} minutes {'left' if 'discharging' in state else 'until full'}"
        status = ("charging" if state in ("charging", "finishing charge") else "on battery" if "discharging" in state
                  else "fully charged" if state == "charged" else "plugged in but not charging")
        return f"Battery at {pct} percent, {status}{extra}."
    m = _DARK.search(text)
    if m and re.search(r"\b(turn|switch|enable|disable|set|on|off|use)\b", text, re.I):
        dark = m.group(1).lower() == "dark"
        if re.search(r"\b(off|disable)\b", text, re.I):
            dark = not dark
        _osa(f'tell application "System Events" to tell appearance preferences to set dark mode to {"true" if dark else "false"}')
        return f"{'Dark' if dark else 'Light'} mode on."
    m = _QUIT.match(text.strip())
    if m and not re.search(r"\b(screen|briefing|report|it|that|this|conversation)\b", m.group(1), re.I):
        import actions
        app = actions.find_app(m.group(1))
        if app:
            name = Path(app).stem
            _osa(f'if application "{name}" is running then tell application "{name}" to quit')
            return f"Closed {name}."
    return None


def handle(text: str) -> str | None:
    for fn in (volume, music, directions, mac):
        try:
            reply = fn(text)
        except Exception as exc:  # noqa: BLE001 -- a helper failing mustn't eat the question
            print(f"[mac_control] {fn.__name__} failed: {exc!r}", flush=True)
            reply = None
        if reply:
            return reply
    return None
