// Apple Health numbers from the iPhone Shortcut -- the same little text files
// computer/voice/health.py reads (iCloud Drive -> Shortcuts -> JARVIS,
// "health-YYYY-MM-DD.txt"). Used for the pet's walk / sleep nudges and the
// Goals window's sync status. Lenient parsing: see health.py.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const MOBILE = path.join(os.homedir(), 'Library', 'Mobile Documents');
const FOLDERS = [
  path.join(MOBILE, 'iCloud~is~workflow~my~workflows', 'Documents', 'JARVIS'),
  path.join(MOBILE, 'com~apple~CloudDocs', 'JARVIS'),
  path.join(MOBILE, 'com~apple~CloudDocs', 'Shortcuts', 'JARVIS'),
];

function parseNumber(text) {
  const m = String(text).match(/\d[\d.,   ]*/);
  if (!m) return null;
  const tok = m[0].replace(/[   ]/g, '').replace(/[.,]$/, '');
  if (/^\d{1,3}([.,]\d{3})+$/.test(tok)) return Number(tok.replace(/[.,]/g, ''));
  const dec = tok.match(/^(.*)[.,](\d{1,2})$/);
  if (dec) return Number(`${dec[1].replace(/[.,]/g, '')}.${dec[2]}`);
  return Number(tok.replace(/[.,]/g, ''));
}

function parseMinutes(text) {
  const t = String(text).toLowerCase();
  const h = t.match(/(\d+(?:[.,]\d+)?)\s*(h|hr|hrs|hour|hours|std)\b/);
  const m = t.match(/(\d+)\s*(m|min|mins|minutes)\b/);
  if (h || m) return (h ? parseNumber(h[1]) * 60 : 0) + (m ? Number(m[1]) : 0);
  const hm = t.match(/^\s*(\d{1,2}):(\d{2})/);
  if (hm) return Number(hm[1]) * 60 + Number(hm[2]);
  const n = parseNumber(t);
  if (n === null) return null;
  return n <= 16 ? n * 60 : n > 1440 ? n / 60 : n;
}

const KEYS = { steps: 'steps', active_kcal: 'activeKcal', exercise_min: 'exerciseMin', sleep: 'sleepMin', sleep_min: 'sleepMin', resting_hr: 'restingHr' };

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// The newest file: { date, steps, sleepMin, ..., synced (ms) } or null.
function latest() {
  let best = null;
  for (const dir of FOLDERS) {
    let names = [];
    try { names = fs.readdirSync(dir); } catch { continue; }
    for (const name of names) {
      if (!/^health/i.test(name)) continue;
      const file = path.join(dir, name);
      let st;
      try { st = fs.statSync(file); } catch { continue; }
      if (best && st.mtimeMs <= best.synced) continue;
      const out = { synced: st.mtimeMs };
      for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
        const i = line.indexOf(':');
        if (i < 0) continue;
        const key = line.slice(0, i).trim().toLowerCase();
        const value = line.slice(i + 1);
        if (key === 'date') { const m = value.match(/(\d{4})-(\d{1,2})-(\d{1,2})/); if (m) out.date = `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`; continue; }
        const field = KEYS[key];
        if (field) { const n = field === 'sleepMin' ? parseMinutes(value) : parseNumber(value); if (n !== null && !Number.isNaN(n)) out[field] = n; }
      }
      out.date = out.date || (name.match(/\d{4}-\d{2}-\d{2}/) || [today()])[0];
      best = out;
    }
  }
  if (best) best.isToday = best.date === today();
  return best;
}

const fmtSleep = (min) => `${Math.floor(min / 60)}h ${String(Math.round(min % 60)).padStart(2, '0')}m`;
const fmtSteps = (n) => Math.round(n).toLocaleString('de-DE');

module.exports = { latest, fmtSleep, fmtSteps, parseNumber, parseMinutes, FOLDERS };
