// Goals, people, moods and reminder settings -- database/goals.json (personal,
// git-ignored). Written by the Goals window (goals.html), read by the
// reminders (reminders.js) and by JARVIS's briefing (computer/voice/findings.py
// reads the same file). Reminder bookkeeping (when the last water break was
// suggested, etc.) lives separately in database/pet/reminder-state.json so a
// save from the window never races the scheduler.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const JARVIS_ROOT = path.join(__dirname, '..', '..');
const GOALS_FILE = path.join(JARVIS_ROOT, 'database', 'goals.json');
const STATE_FILE = path.join(JARVIS_ROOT, 'database', 'pet', 'reminder-state.json');

const DEFAULT_SETTINGS = {
  userName: '',
  goalNudges: { on: true },
  water: { on: true, everyMin: 60 },
  moodCheck: { on: true, times: ['11:00', '16:00'] },
  cpu: { on: true, threshold: 85 },
  social: { on: true },
  partner: { on: true, name: '' },
  health: { on: true, stepGoal: 8000, sleepGoalH: 7.5 },
  quiet: { from: '22:00', to: '08:00' },
  minGapMin: 20,
};

const EMPTY = { version: 1, goals: [], log: [], skips: [], people: [], moods: [], settings: DEFAULT_SETTINGS, character: 'human' };

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return fallback;
  }
}

function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 1));
  fs.renameSync(tmp, file);
}

function mergeSettings(s = {}) {
  const out = {};
  for (const [k, v] of Object.entries(DEFAULT_SETTINGS)) out[k] = v && typeof v === 'object' && !Array.isArray(v) ? { ...v, ...(s[k] || {}) } : s[k] ?? v;
  return out;
}

function load() {
  const d = readJson(GOALS_FILE, EMPTY);
  return { ...EMPTY, ...d, settings: mergeSettings(d.settings) };
}

function save(data) {
  writeJson(GOALS_FILE, data);
  return data;
}

function update(fn) {
  const d = load();
  fn(d);
  return save(d);
}

const newId = () => crypto.randomBytes(5).toString('hex');

// --- periods & progress (mirrored in computer/voice/findings.py) ----------------
function dayKey(t = Date.now()) {
  const d = new Date(t);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function periodStart(cadence, t = Date.now()) {
  const d = new Date(t);
  d.setHours(0, 0, 0, 0);
  if (cadence === 'weekly') d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // Monday
  if (cadence === 'monthly') d.setDate(1);
  return d.getTime();
}

function periodLength(cadence, t = Date.now()) {
  if (cadence === 'daily') return 1;
  if (cadence === 'weekly') return 7;
  const d = new Date(t);
  return new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
}

function progress(data, goal, t = Date.now()) {
  const start = periodStart(goal.cadence, t);
  const done = data.log.filter((l) => l.goal === goal.id && l.at >= start).length;
  const target = Math.max(1, goal.target || 1);
  const daysIn = Math.floor((t - start) / 86400000) + 1;
  const len = periodLength(goal.cadence, t);
  const expected = goal.cadence === 'daily' ? target : Math.floor((target * daysIn) / len);
  const skippedToday = data.skips.some((s) => s.goal === goal.id && s.day === dayKey(t));
  return { done, target, met: done >= target, behind: done < expected, expected, skippedToday, daysLeft: len - daysIn };
}

function streak(data, goal, t = Date.now()) {
  if (goal.cadence !== 'daily') return 0;
  let n = 0;
  for (let d = new Date(t); ; d.setDate(d.getDate() - 1)) {
    const key = dayKey(d.getTime());
    const hit = data.log.some((l) => l.goal === goal.id && dayKey(l.at) === key);
    if (!hit && key !== dayKey(t)) break;
    if (hit) n += 1;
    if (n > 365) break;
  }
  return n;
}

module.exports = {
  GOALS_FILE, STATE_FILE, DEFAULT_SETTINGS,
  load, save, update, newId,
  loadState: () => readJson(STATE_FILE, {}),
  saveState: (s) => writeJson(STATE_FILE, s),
  dayKey, periodStart, progress, streak,
};
