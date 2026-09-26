# Your websites -> JARVIS

JARVIS can keep an eye on sites you run -- sign-ups, orders, revenue,
approvals waiting, form submissions, deals -- and tell you:

- **Briefing**: a BUSINESS card ("3 things waiting on you", trends vs. last week)
- **Voice**: "how's <site> doing?", "any new orders?", "how are my sites
  performing?", "what does my pipeline look like?"
- **Pet cards**: a new order 🛒, new sign-ups 👋, approvals waiting ⏳, a new
  project request 📬, a deal won 🏆, a site not responding ⚠️ -- with an
  "Open admin" button. Checked every 30 minutes.

## 1. Add a stats endpoint to each site

A small file that answers `GET` with JSON **counts only** (never names or
emails), protected by its own read-only key sent as the `X-Jarvis-Key` header:

```php
<?php
require_once __DIR__ . '/../config.php';           // your site's DB connection
header('Content-Type: application/json');
if (!hash_equals(JARVIS_STATS_KEY, $_SERVER['HTTP_X_JARVIS_KEY'] ?? '')) { http_response_code(401); exit; }
$db = db();
$q = [
  'users'         => 'SELECT COUNT(*) FROM users',
  'new_users_7d'  => 'SELECT COUNT(*) FROM users WHERE created_at >= NOW() - INTERVAL 7 DAY',
  'orders_24h'    => 'SELECT COUNT(*) FROM orders WHERE created_at >= NOW() - INTERVAL 1 DAY',
  'revenue_7d'    => "SELECT COALESCE(SUM(total),0) FROM orders WHERE created_at >= NOW() - INTERVAL 7 DAY",
  'pending_shops' => "SELECT COUNT(*) FROM shops WHERE approved = 0",
];
$out = [];
foreach ($q as $k => $sql) { try { $out[$k] = 0 + $db->query($sql)->fetchColumn(); } catch (Throwable $e) { $out[$k] = null; } }
echo json_encode(['success' => true, 'data' => $out]);
```

Name your numbers with the keys JARVIS knows (see `LABELS` in
`computer/voice/business.py`: `new_users_7d`, `orders_7d`, `revenue_7d`,
`visitors_24h`, `pending_*`, `unread_submissions`, `open_deals`,
`pipeline_value`, `overdue_tasks`...). Unknown keys are kept in the history.
Keep the key **out of git** on the site (a secrets file your repo ignores).

## 2. Tell JARVIS about the site

Copy `config/business.example.json` to `config/business.json` (git-ignored)
and add each site: `name`, `group` (e.g. the company), `url` (the stats
endpoint), `admin_url` (opened from the pet's card) and `key_location` (a
note to yourself: where the key goes on the server).

## 3. Generate the keys

```bash
python3 computer/business/setup-keys.py
```

It creates a random key per site, saves it in `config/business.json` and
prints where to paste each one. Test a site any time with
`computer/voice/venv/bin/python computer/voice/business.py`.
