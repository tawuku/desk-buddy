"""
Business dashboards for JARVIS: your own websites' admin numbers -- sign-ups,
orders, revenue, approvals waiting, form submissions, deals -- read from a
small read-only stats endpoint on each site (see computer/business/README.md).

- config/business.json (personal, git-ignored; template:
  config/business.example.json): each site's name, group, stats URL, key and
  admin URL. Keys are read-only and live only on this Mac.
- Every 30 minutes (JARVIS's background loop) each site is fetched; results
  go to database/business/history.jsonl, so trends compare against a week ago.
- Briefing: a BUSINESS card (findings.py). Voice: "how's <site> doing?",
  "business update". Pet cards when something happens: a new order, new
  sign-ups, approvals waiting, a new project request, a deal won, a site down.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

JARVIS_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = JARVIS_DIR / "config" / "business.json"
HISTORY_FILE = JARVIS_DIR / "database" / "business" / "history.jsonl"
STATE_FILE = JARVIS_DIR / "database" / "business" / "state.json"
POLL_SECONDS = 30 * 60
CURRENCY = "€"

# How each known number is said / shown. Unknown keys are still stored.
LABELS = {
    "users": "users", "new_users_24h": "new sign-ups today", "new_users_7d": "new sign-ups this week",
    "active_users_7d": "active users this week", "visitors_24h": "visitors today", "page_views_24h": "page views today",
    "orders": "orders", "orders_24h": "orders today", "orders_7d": "orders this week",
    "revenue": "total revenue", "revenue_24h": "revenue today", "revenue_7d": "revenue this week",
    "shops": "shops", "products": "products", "jobs": "jobs", "freelancers": "freelancers", "newsletter": "newsletter subscribers",
    "pending_shops": "shops awaiting approval", "pending_jobs": "jobs awaiting approval",
    "pending_freelancers": "freelancers awaiting approval", "pending_role_requests": "role requests waiting",
    "upcoming_events": "upcoming events", "events_next_7d": "events this week", "pending_events": "events to review",
    "pending_submissions": "submissions to review", "new_events_7d": "new events this week",
    "open_deals": "open deals", "pipeline_value": "pipeline value", "new_leads_7d": "new leads this week",
    "deals_won_30d": "deals won this month", "won_value_30d": "won this month", "deals_closing_7d": "deals closing this week",
    "open_tasks": "open tasks", "overdue_tasks": "overdue tasks", "tasks_due_7d": "tasks due this week",
    "bookings_upcoming_7d": "bookings this week", "open_bug_reports": "open bug reports",
    "submissions_24h": "form submissions today", "submissions_7d": "form submissions this week",
    "unread_submissions": "unread submissions", "project_requests_7d": "project requests this week",
    "chat_sessions_7d": "website chats this week", "predictions_24h": "predictions today",
    "online_now": "online right now", "pending_reviews": "reviews to moderate", "conversations_7d": "agent conversations this week",
}
MONEY = {"revenue", "revenue_24h", "revenue_7d", "pipeline_value", "won_value_30d"}
# Anything waiting on you -- these drive the "needs attention" line and cards.
ATTENTION = ["pending_shops", "pending_jobs", "pending_freelancers", "pending_role_requests", "pending_events",
             "pending_submissions", "unread_submissions", "overdue_tasks", "open_bug_reports", "pending_reviews"]
# Per-site headline order for the briefing / voice summary.
HEADLINE = ["new_users_7d", "orders_7d", "revenue_7d", "visitors_24h", "new_leads_7d", "won_value_30d", "pipeline_value",
            "submissions_7d", "project_requests_7d", "events_next_7d", "upcoming_events", "active_users_7d",
            "predictions_24h", "conversations_7d", "open_tasks"]


def fmt(key: str, value) -> str:
    if value is None:
        return "?"
    if key in MONEY:
        return f"{CURRENCY}{value:,.0f}".replace(",", ".")
    return f"{int(value):,}".replace(",", ".") if float(value).is_integer() else f"{value:.1f}"


def sites() -> list[dict]:
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return []
    return [s for s in cfg.get("sites", []) if s.get("url") and s.get("key") and s.get("enabled", True)]


def names() -> set[str]:
    """Every configured site and group name (keys or not) -- for voice routing."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return set()
    return {w for s in cfg.get("sites", []) for w in (s.get("name"), s.get("group")) if w}


def fetch(site: dict) -> dict:
    """{'ok': bool, 'status': http code / 'down', 'data': {...}}"""
    req = urllib.request.Request(site["url"], headers={"X-Jarvis-Key": site["key"], "User-Agent": "JARVIS/1.0 (owner's assistant)",
                                                       "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read())
        return {"ok": bool(body.get("success")), "status": 200, "data": body.get("data") or {}}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "data": {}}
    except Exception:  # noqa: BLE001 -- timeout / DNS / TLS: treat as down
        return {"ok": False, "status": "down", "data": {}}


def _history() -> list[dict]:
    try:
        return [json.loads(l) for l in HISTORY_FILE.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def latest() -> dict[str, dict]:
    """site name -> newest record."""
    out: dict[str, dict] = {}
    for rec in _history():
        out[rec["site"]] = rec
    return out


def week_ago(site: str, key: str) -> float | None:
    """The value of `key` ~7 days ago (closest record 6.5-8 days old)."""
    now = time.time()
    best = None
    for rec in _history():
        if rec["site"] == site and rec.get("ok") and 6.5 * 86400 <= now - rec["t"] <= 8 * 86400:
            v = rec["data"].get(key)
            if v is not None:
                best = v
    return best


def poll_once(alert=None) -> dict[str, dict]:
    """Fetch every site, append to the history, fire alerts. Returns results."""
    prev = latest()
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    results = {}
    for s in sites():
        res = fetch(s)
        rec = {"t": time.time(), "site": s["name"], "group": s.get("group", s["name"]), **res}
        with HISTORY_FILE.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        results[s["name"]] = rec
        if alert:
            for card in _alerts(s, prev.get(s["name"]), rec):
                alert(card)
    _trim_history()
    return results


def _trim_history(days: int = 60) -> None:
    rows = _history()
    cutoff = time.time() - days * 86400
    if rows and rows[0]["t"] < cutoff:
        HISTORY_FILE.write_text("".join(json.dumps(r) + "\n" for r in rows if r["t"] >= cutoff))


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _alerts(site: dict, before: dict | None, now: dict) -> list[dict]:
    """Cards for what changed since the last check."""
    name, admin = site["name"], site.get("admin_url", "")
    st = _state()
    cards = []

    def card(key: str, icon: str, title: str, text: str, every_h: float = 3) -> None:
        stamp = f"{name}:{key}"
        if time.time() - st.get(stamp, 0) >= every_h * 3600:
            st[stamp] = time.time()
            cards.append({"icon": icon, "title": title, "text": text, "url": admin})

    if not now["ok"]:
        # Down twice in a row (not a key/config problem) -> say so, once every 6 h.
        if now["status"] in ("down", 500, 502, 503, 504) and before and not before.get("ok") and before.get("status") == now["status"]:
            card("down", "⚠️", f"{name} isn't responding", f"{name} didn't answer twice in a row ({now['status']}). Worth a look.", 6)
    elif before and before.get("ok"):
        d, b = now["data"], before["data"]

        def up(key: str) -> int:
            if d.get(key) is None or b.get(key) is None:
                return 0
            return int(d[key] - b[key])

        if up("orders") > 0:
            card("orders", "🛒", f"New order on {name}!", f"{up('orders')} new order{'s' if up('orders') > 1 else ''} -- "
                 f"{fmt('revenue_24h', d.get('revenue_24h') or 0)} revenue today.", 0)
        if up("users") >= 1:
            card("signups", "👋", f"New sign-ups on {name}", f"{up('users')} new since the last check -- "
                 f"{fmt('new_users_7d', d.get('new_users_7d'))} this week.", 3)
        for key in ("pending_shops", "pending_jobs", "pending_freelancers", "pending_role_requests", "pending_events",
                    "pending_submissions", "pending_reviews"):
            if up(key) > 0:
                card(key, "⏳", f"Waiting for you on {name}", f"{fmt(key, d[key])} {LABELS[key]}.", 6)
        if up("unread_submissions") > 0 or up("project_requests_7d") > 0:
            what = "project request" if up("project_requests_7d") > 0 else "form submission"
            card("submission", "📬", f"New {what} on {name}", f"You have {fmt('unread_submissions', d.get('unread_submissions'))} unread.", 0)
        if up("deals_won_30d") > 0:
            card("won", "🏆", "Deal won!", f"{name}: {fmt('won_value_30d', d.get('won_value_30d'))} won this month.", 0)
        if up("new_leads_7d") > 0:
            card("lead", "🎯", f"New lead in {name}", f"{fmt('open_deals', d.get('open_deals'))} open deals, "
                 f"{fmt('pipeline_value', d.get('pipeline_value'))} in the pipeline.", 3)
        if up("open_bug_reports") > 0:
            card("bug", "🐞", f"New bug report in {name}", f"{fmt('open_bug_reports', d['open_bug_reports'])} open.", 3)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st))
    return cards


# --- reporting ---------------------------------------------------------------------

def _trend(site: str, key: str, value) -> str:
    before = week_ago(site, key)
    if value is None or not before:
        return ""
    change = (value - before) / before * 100
    return f" ({'+' if change >= 0 else ''}{change:.0f}% vs last week)" if abs(change) >= 5 else ""


def _site_lines(name: str, rec: dict, n: int = 3) -> tuple[list[str], list[str]]:
    """(headline facts, things waiting on you) for one site."""
    d = rec.get("data", {})
    facts = [f"{fmt(k, d[k])} {LABELS.get(k, k)}{_trend(name, k, d[k])}" for k in HEADLINE if d.get(k)][:n]
    waiting = [f"{fmt(k, d[k])} {LABELS.get(k, k)}" for k in ATTENTION if d.get(k)]
    return facts, waiting


def summary(only: str | None = None) -> str:
    """Compact text for voice answers ("how's <site> doing?")."""
    recs = latest()
    if not recs:
        return "Business: no sites connected yet (config/business.json)." if not sites() else "Business: no data yet -- first check is running."
    parts = []
    for name, rec in recs.items():
        if only and only.lower() not in f"{name} {rec.get('group', '')}".lower():
            continue
        if not rec.get("ok"):
            parts.append(f"{name}: not reachable ({rec.get('status')})")
            continue
        facts, waiting = _site_lines(name, rec, 4)
        parts.append(f"{name}: " + ", ".join(facts or ["no activity"]) + (f"; waiting on you: {', '.join(waiting)}" if waiting else ""))
    age = (time.time() - max(r["t"] for r in recs.values())) / 60
    return f"Business (checked {age:.0f} min ago) -- " + " || ".join(parts) + "."


def category() -> dict | None:
    """The briefing's BUSINESS card (findings.py), or None without sites."""
    recs = latest()
    if not recs:
        return None
    rows, says, waiting_all, down = [], [], [], []
    for name, rec in recs.items():
        if not rec.get("ok"):
            down.append(name)
            rows.append({"l": name, "r": "OFFLINE" if rec.get("status") == "down" else f"HTTP {rec.get('status')}", "tone": "bad"})
            continue
        facts, waiting = _site_lines(name, rec, 2)
        waiting_all += [f"{w} on {name}" for w in waiting]
        top = next((k for k in HEADLINE if rec["data"].get(k)), None)
        rows.append({"l": name, "r": f"{fmt(top, rec['data'][top])} {LABELS.get(top, top).upper()}" if top else "QUIET",
                     "tone": "warn" if waiting else "good"})
        if facts:
            says.append(f"{name}: {', '.join(facts)}")
    headline = (f"{len(waiting_all)} thing{'s' if len(waiting_all) != 1 else ''} waiting on you." if waiting_all
                else "All quiet -- nothing waiting on you.")
    if down:
        headline = f"{', '.join(down)} not responding. " + headline
    say = "Business. " + ". ".join(says[:4]) + "."
    if waiting_all:
        say += " Waiting on you: " + ", ".join(waiting_all[:3]) + "."
    if down:
        say += f" And {', '.join(down)} didn't respond."
    rows.sort(key=lambda r: {"bad": 0, "warn": 1}.get(r["tone"], 2))
    return {"name": "BUSINESS", "count": f"{len(recs)} SITES", "headline": headline[:90], "items": rows[:3], "say": say}


def poll_loop(alert) -> None:
    """Background (wake_listener): check every POLL_SECONDS."""
    time.sleep(60)
    while True:
        try:
            if sites():
                poll_once(alert)
        except Exception as exc:  # noqa: BLE001
            print(f"[business] poll failed: {exc!r}", flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":  # python3 business.py -> check every site now
    for name, rec in poll_once().items():
        print(name, "->", "ok" if rec["ok"] else rec["status"], json.dumps(rec["data"])[:300])
    print(summary())
