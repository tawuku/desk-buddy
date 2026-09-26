"""
Loopback HTTP server for the JARVIS screen (computer/screen/index.html),
started by wake_listener.py on 127.0.0.1:8094:

  GET  /                 the screen itself
  GET  /findings         today's findings report (findings.py), JSON
  POST /tts   {text}     Piper WAV for one line -- the screen plays it itself,
                         so the orb can pulse with the real audio level
  POST /screen/status    the lead screen reports its state {phase, step,
                         revealed, caption, speaking, report}
  GET  /screen/poll      {"seq", "command", "state"}: commands for the lead
                         screen (e.g. "stop" when "hey Jarvis" interrupts),
                         and the lead's state for the mirror screens
  POST /screen/command   a button pressed on a mirror screen, for the lead
  POST /say   {text}     speak a line out loud (the boot intro's greeting)

The desktop pet (computer/pet/main.js) shows the screen full-screen on every
display -- the main display's window leads (speaks, runs the briefing), the
others mirror it. open_screen() asks the pet to via its own port, 8092.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import findings
import smarts
import tts

PORT = 8094
SCREEN_DIR = Path(__file__).resolve().parent.parent / "screen"
PET_SCREEN_URL = "http://127.0.0.1:8092/screen"
PET_SCREEN_MODE_URL = "http://127.0.0.1:8092/screen/mode"
PET_SCREEN_CLOSE_URL = "http://127.0.0.1:8092/screen/close"
REFRESH_AFTER_SECONDS = 20 * 60
SCREEN_COMMANDS = {"start", "stop", "dismiss", "voice"}  # plus "replay:<n>"
SCREEN_CONFIG_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "screen.json"
DEFAULT_SCREEN_CONFIG = {"brief_on_wake": True, "wake_cooldown_minutes": 0}  # serve the cache, rebuild in the background past this age
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

_state = {"phase": "closed", "speaking": False, "updated": 0.0}
_state_lock = threading.Lock()
_command = {"seq": 0, "command": None}
_talk = {"seq": 0, "stage": None, "text": None}  # the live conversation, for the caption
_tts_lock = threading.Lock()
_build_lock = threading.Lock()


def log(msg: str) -> None:
    print(f"[screen] {msg}", flush=True)


# --- API for wake_listener.py --------------------------------------------------

def state() -> dict:
    with _state_lock:
        return dict(_state)


def load_config() -> dict:
    try:
        return {**DEFAULT_SCREEN_CONFIG, **json.loads(SCREEN_CONFIG_FILE.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_SCREEN_CONFIG)


def set_phase(phase: str) -> None:
    with _state_lock:
        _state.update(phase=phase, speaking=False, updated=time.time())


def talk(stage: str, text: str | None) -> None:
    """Mirror a HUD stage (listening/heard/thinking/speaking/...) onto the
    screen's caption -- after the briefing the conversation continues there."""
    if stage == "step":
        return
    with _state_lock:
        _talk.update(seq=_talk["seq"] + 1, stage=stage, text=text)


def send(command: str) -> None:
    with _state_lock:
        _command["seq"] += 1
        _command["command"] = command


def open_screen(autostart: bool = True) -> bool:
    """Ask the pet app to show the screen. False if the pet isn't running."""
    try:
        body = json.dumps({"autostart": autostart}).encode()
        req = urllib.request.Request(PET_SCREEN_URL, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def _pet_post(url: str, payload: dict) -> bool:
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


def is_open() -> bool:
    return state()["phase"] not in ("closed", "opening")


def close_screen() -> bool:
    """Kill switch: the pet destroys every screen window."""
    ok = _pet_post(PET_SCREEN_CLOSE_URL, {})
    set_phase("closed")
    return ok


def set_mode(mode: str) -> bool:
    """'mini' = corner mini-player (keeps talking), 'full' = back to full screen."""
    return _pet_post(PET_SCREEN_MODE_URL, {"mode": mode})


def refresh_findings() -> dict | None:
    """Rebuild the findings (one build at a time)."""
    if not _build_lock.acquire(blocking=False):
        return None
    try:
        started = time.monotonic()
        data = findings.build()
        log(f"findings rebuilt in {time.monotonic() - started:.0f}s")
        return data
    except Exception as exc:  # noqa: BLE001
        log(f"findings build failed: {exc!r}")
        return None
    finally:
        _build_lock.release()


def current_findings() -> dict | None:
    cached = findings.load(max_age_hours=24)
    if cached is None:
        refresh_findings()
        with _build_lock:  # another thread may have been building; wait for it
            pass
        return findings.load(max_age_hours=24)
    if time.time() - cached["generated_at"] > REFRESH_AFTER_SECONDS:
        threading.Thread(target=refresh_findings, daemon=True).start()
    return cached


# --- HTTP ----------------------------------------------------------------------

def _say(text: str) -> None:
    try:
        with _tts_lock:
            audio = tts.synthesize(text)
        tts.play(audio)
    except Exception as exc:  # noqa: BLE001
        log(f"say failed: {exc!r}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass  # quiet; errors are logged explicitly

    def _allowed(self) -> bool:
        # Blocks DNS-rebinding pages from reaching this loopback server.
        if self.headers.get("Host") in ALLOWED_HOSTS:
            return True
        self._send(403, b"forbidden", "text/plain")
        return False

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: object, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def _body(self) -> dict:
        n = min(int(self.headers.get("Content-Length") or 0), 64_000)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        if not self._allowed():
            return
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, (SCREEN_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/findings":
            data = current_findings()
            self._json(data if data else {"error": "no findings yet"}, 200 if data else 503)
        elif path == "/screen/poll":
            with _state_lock:
                self._json({**_command, "state": dict(_state), "talk": dict(_talk)})
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if not self._allowed():
            return
        if self.path == "/tts":
            text = str(self._body().get("text", "")).strip()[:1200]
            if not text:
                self._send(400, b"no text", "text/plain")
                return
            try:
                with _tts_lock:
                    audio = tts.synthesize(text)
            except Exception as exc:  # noqa: BLE001
                log(f"tts failed: {exc!r}")
                self._send(500, b"tts failed", "text/plain")
                return
            self._send(200, audio, "audio/wav")
        elif self.path in ("/reminders/snooze", "/reminders/done"):
            body = self._body()
            rid = str(body.get("id", ""))
            if self.path.endswith("snooze"):
                smarts.snooze(rid, float(body.get("minutes", 10)))
            else:
                smarts.mark_done(rid)
            self._json({"ok": True})
        elif self.path == "/say":
            text = str(self._body().get("text", "")).strip()[:400]
            if text:
                threading.Thread(target=_say, args=(text,), daemon=True).start()
            self._json({"ok": bool(text)})
        elif self.path == "/screen/status":
            body = self._body()
            with _state_lock:
                _state.update({k: body[k] for k in ("step", "revealed", "caption", "label", "voice", "report") if k in body})
                _state.update(phase=str(body.get("phase", "idle")), speaking=bool(body.get("speaking")),
                              updated=time.time())
            self._json({"ok": True})
        elif self.path == "/screen/command":
            command = str(self._body().get("command", ""))
            if command in SCREEN_COMMANDS or (command.startswith("replay:") and command[7:].isdigit()):
                send(command)
                self._json({"ok": True})
            else:
                self._send(400, b"unknown command", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        import sys
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return  # a status check that hung up early -- nothing to report
        super().handle_error(request, client_address)


def start() -> None:
    try:
        server = Server(("127.0.0.1", PORT), Handler)
    except OSError as exc:
        log(f"couldn't listen on {PORT}: {exc}")
        return
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"serving the JARVIS screen on http://127.0.0.1:{PORT}/")
