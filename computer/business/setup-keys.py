#!/usr/bin/env python3
"""
Generate the read-only JARVIS stats key for every site in config/business.json
that doesn't have one yet, save it there, and print where to paste each key on
the site's server. Run it yourself -- the keys only ever appear in your
Terminal and in config/business.json (git-ignored).

    python3 computer/business/setup-keys.py            # new keys for empty ones
    python3 computer/business/setup-keys.py --rotate   # replace every key
"""
import json
import secrets
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "business.json"
if not CONFIG.exists():
    sys.exit(f"No {CONFIG} yet -- copy config/business.example.json to it and fill in your sites first.")
cfg = json.loads(CONFIG.read_text())
rotate = "--rotate" in sys.argv
changed = []
for site in cfg.get("sites", []):
    if rotate or not site.get("key"):
        site["key"] = secrets.token_hex(32)
        changed.append(site)
CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
CONFIG.chmod(0o600)
if not changed:
    print("Every site already has a key (use --rotate to replace them).")
for site in changed:
    print(f"\n== {site['name']}\n   Paste on the server -> {site.get('key_location', 'the site config')}\n   Key: {site['key']}\n   Test: curl -s -H 'X-Jarvis-Key: <key>' {site['url']}")
if changed:
    print("\nThen: JARVIS picks them up within 30 minutes -- or run: computer/voice/venv/bin/python computer/voice/business.py")
