#!/usr/bin/env python3
"""
JARVIS brain server: run this on the always-on / stronger computer (the
Windows PC). It owns the heavy parts and exposes them on ONE port, behind a
token, to the Mac (which only records, plays audio and draws the pet):

  POST /llm/...   -> llama-server   127.0.0.1:8080  (OpenAI-style chat, streamed)
  POST /stt       -> whisper-server 127.0.0.1:8093  (speech -> text)
  POST /tts       -> Piper, in this process         ({"text","voice","speed"} -> WAV)
  GET  /health    -> {"llm": bool, "stt": bool, "tts": bool}

llama-server and whisper-server stay on the PC's loopback; only this port is
reachable from the network, and every request needs the token in
config/server.json ({"token": "...", "port": 8090}).
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "computer" / "voice"))
import remote  # noqa: E402
remote.REMOTE_FILE = ROOT / "config" / ".no-remote"  # the server IS the remote: always synthesize locally
import tts  # noqa: E402  (Piper; runs here, in the server)

CONFIG = json.loads((ROOT / "config" / "server.json").read_text())
TOKEN = CONFIG["token"]
PORT = int(CONFIG.get("port", 8090))
LLM_KEY = CONFIG.get("llm_api_key", "")
LLM = "http://127.0.0.1:8080"
# Hosted model instead of the local llama-server: set JARVIS_LLM_API_KEY in the
# environment (never in a file). JARVIS_LLM_URL / JARVIS_LLM_MODEL override the defaults.
HOSTED_KEY = os.environ.get("JARVIS_LLM_API_KEY", "")
HOSTED_URL = os.environ.get("JARVIS_LLM_URL", "https://api.anthropic.com/v1/chat/completions")
HOSTED_MODEL = os.environ.get("JARVIS_LLM_MODEL", "claude-haiku-4-5-20251001")
LLAMA_ONLY_KEYS = ("cache_prompt", "chat_template_kwargs")
STT = "http://127.0.0.1:8093"


def _up(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=1.5)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:  # noqa: BLE001
        return False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # noqa: ANN002
        pass

    def _authed(self) -> bool:
        got = self.headers.get("Authorization", "")
        if hmac.compare_digest(got, f"Bearer {TOKEN}"):
            return True
        self._send(401, b"unauthorized", "text/plain")
        return False

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, target: str, hosted: bool = False) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length)
        headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
        if hosted:
            body = json.loads(data or b"{}")
            body["model"] = HOSTED_MODEL
            for key in LLAMA_ONLY_KEYS:
                body.pop(key, None)
            data = json.dumps(body).encode()
            headers["Authorization"] = f"Bearer {HOSTED_KEY}"
        elif LLM_KEY and target.startswith(LLM):
            headers["Authorization"] = f"Bearer {LLM_KEY}"  # only if llama-server was started with --api-key
        req = urllib.request.Request(target, data=data, method="POST", headers=headers)
        try:
            upstream = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read(), "text/plain")
            return
        except Exception as exc:  # noqa: BLE001
            self._send(502, f"upstream down: {exc}".encode(), "text/plain")
            return
        # Relay as chunked so streamed (SSE) replies reach the Mac token by token.
        self.send_response(upstream.status)
        self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/octet-stream"))
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                chunk = upstream.read1(8192) if hasattr(upstream, "read1") else upstream.read(8192)
                if not chunk:
                    break
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True  # the Mac barged in / cancelled
        finally:
            upstream.close()

    def do_GET(self) -> None:  # noqa: N802
        if not self._authed():
            return
        if self.path == "/health":
            self._send(200, json.dumps({"llm": bool(HOSTED_KEY) or _up(f"{LLM}/health"), "stt": _up(f"{STT}/"),
                                        "tts": bool(tts.available_voices())}).encode(), "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if not self._authed():
            return
        if HOSTED_KEY and self.path == "/llm/v1/chat/completions":
            self._proxy(HOSTED_URL, hosted=True)
        elif self.path.startswith("/llm/"):
            self._proxy(LLM + self.path[4:])
        elif self.path == "/stt":
            self._proxy(f"{STT}/inference")
        elif self.path == "/tts":
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            text = str(body.get("text", "")).strip()[:1200]
            if not text:
                self._send(400, b"no text", "text/plain")
                return
            self._send(200, tts.synthesize(text, body.get("voice"), body.get("speed")), "audio/wav")
        else:
            self._send(404, b"not found", "text/plain")


if __name__ == "__main__":
    print(f"[jarvis-server] listening on 0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
