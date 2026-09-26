// Pop-up reminders above the pet: goal check-ins, water breaks, a mood
// check-in, high-CPU alerts, "call your mum" nudges and ideas for your
// partner. A card appears in a small window just above the pet
// (popup.html); the pet turns to face you while it's up.
//
// Checked once a minute. Nothing pops up during quiet hours, while you're
// away (idle > 5 min), on a call (the "listening" mood), while the JARVIS
// screen / boot intro is up, or less than minGapMin after the last card.
const { BrowserWindow, ipcMain, powerMonitor, screen } = require('electron');
const os = require('node:os');
const path = require('node:path');
const { exec } = require('node:child_process');
const http = require('node:http');
const store = require('./store');
const health = require('./health');

const POPUP_WIDTH = 360;
const AWAY_SECONDS = 5 * 60;
const CPU_SAMPLE_MS = 20000;
const CPU_WINDOW = 9; // samples (3 min) that must all be over the threshold
const CPU_COOLDOWN_MS = 45 * 60000;

let ctx = null; // { petWindow(), petMood(), paused(), screenBusy(), react(kind) }
let popupWin = null;
let current = null; // the card on screen: { kind, ... }
const cpuSamples = [];
let lastCpuTimes = null;

// --- content --------------------------------------------------------------------
const pick = (a) => a[Math.floor(Math.random() * a.length)];

const PARTNER_IDEAS = [
  'Leave a handwritten note somewhere she will find it tomorrow morning.',
  'Plan a surprise picnic: her favourite snacks, a blanket, a nice spot.',
  'Cook her favourite dinner tonight -- phone away, candles on.',
  'Send her a voice message saying one specific thing you love about her.',
  'Book a table at the place you went on one of your first dates.',
  'Make a playlist of songs that remind you of her and send it.',
  'Bring her flowers for no reason at all -- "no reason" is the reason.',
  'Plan a movie night with her pick, her snacks, zero complaints.',
  'Ask her about something she mentioned last week and really listen.',
  'Take something off her plate this week -- an errand, a chore, a booking.',
  'Print a photo of the two of you and frame it.',
  'Plan a day trip for the weekend and keep the destination a surprise.',
  'Give her a proper massage tonight, no phone in the other hand.',
  'Write down three things she did this month that made your life better, and tell her.',
  'Surprise her with breakfast in bed this weekend.',
  'Pick up her favourite coffee or treat on your way home.',
  'Plan a "yes day": she chooses everything, you say yes.',
  'Learn to cook one dish from her culture or childhood and make it for her.',
  'Text her in the middle of the day just to say you are thinking of her.',
  'Take a walk together after dinner, just the two of you.',
  'Book a small experience: a cooking class, pottery, a concert.',
  'Recreate your first date as closely as you can.',
  'Hide little notes in her bag or jacket pocket.',
  'Tell her friends or family something great about her when she can hear it.',
];

const WATER_LINES = [
  'Time for a water break! Your brain is ~75% water.',
  'Hydration check -- grab a glass of water and stretch your legs.',
  'Water break! Stand up, drink up, look at something far away for 20 seconds.',
  "You've been at it a while. Water, a stretch, then back to crushing it.",
];

const MOOD_ADVICE = {
  great: 'Love that! Ride the wave -- now is a great time to tackle your hardest goal.',
  okay: 'Solid. A five-minute walk or a glass of water might bump it up a notch.',
  tired: 'Take ten minutes away from the screen and drink some water. Future you says thanks.',
  stressed: 'Try box breathing: in for 4, hold 4, out for 4, hold 4 -- four rounds. You have got this.',
};

function cpuTip(top) {
  const name = (top[0] && top[0].name) || '';
  if (/mediaanalysisd|photoanalysisd/i.test(name)) return 'Photos is analysing your library -- it calms down on its own, or quit Photos for now.';
  if (/mds|mdworker|spotlight/i.test(name)) return 'Spotlight is indexing. It usually finishes by itself in a while.';
  if (/OneDrive|Dropbox|bird|cloudd/i.test(name)) return 'A cloud sync is busy. Pausing it for an hour helps if things feel slow.';
  if (/Chrome|Safari|firefox|Arc/i.test(name)) return 'Browser tabs are eating CPU -- close the ones you are not using.';
  if (/llama|whisper|python/i.test(name)) return 'That is JARVIS thinking hard. It settles once the answer is done.';
  return 'Quit the apps you are not using, or check Activity Monitor for runaways.';
}

// --- the popup window -------------------------------------------------------------
function popupPosition(height) {
  const pet = ctx.petWindow();
  const b = pet && !pet.isDestroyed() ? pet.getBounds() : null;
  const area = screen.getDisplayMatching(b || { x: 0, y: 0, width: 1, height: 1 }).workArea;
  if (!b) return { x: area.x + area.width - POPUP_WIDTH - 20, y: area.y + 20 };
  let x = Math.round(b.x + b.width / 2 - POPUP_WIDTH / 2);
  x = Math.max(area.x + 8, Math.min(area.x + area.width - POPUP_WIDTH - 8, x));
  let y = b.y - height + 36; // the card's tail points down at his head
  const below = y < area.y + 8;
  if (below) y = b.y + b.height - 10;
  return { x, y, below };
}

function show(card) {
  close(false);
  current = card;
  const state = store.loadState();
  state.lastPopup = Date.now();
  store.saveState(state);
  const pos = popupPosition(170);
  popupWin = new BrowserWindow({
    width: POPUP_WIDTH, height: 170, x: pos.x, y: pos.y,
    frame: false, transparent: true, hasShadow: false, resizable: false, movable: false,
    skipTaskbar: true, alwaysOnTop: true, show: false, focusable: true, backgroundColor: '#00000000',
    webPreferences: { preload: path.join(__dirname, 'popup-preload.js'), contextIsolation: true, nodeIntegration: false },
  });
  popupWin.setAlwaysOnTop(true, 'floating');
  popupWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  popupWin.webContents.once('did-finish-load', () => {
    if (!popupWin || popupWin.isDestroyed()) return;
    popupWin.webContents.send('popup-card', { ...card, below: pos.below });
    popupWin.showInactive();
  });
  popupWin.on('closed', () => { popupWin = null; });
  popupWin.loadFile(path.join(__dirname, 'popup.html'));
  ctx.react('attention');
}

function close(reactDone = true) {
  if (popupWin && !popupWin.isDestroyed()) popupWin.destroy();
  popupWin = null;
  if (current && reactDone) ctx.react('attention-end');
  current = null;
}

ipcMain.on('popup-resize', (_e, height) => {
  if (!popupWin || popupWin.isDestroyed()) return;
  const h = Math.max(90, Math.min(420, Math.round(height)));
  const pos = popupPosition(h);
  popupWin.setBounds({ x: pos.x, y: pos.y, width: POPUP_WIDTH, height: h });
});

// A button on the card. Returns an optional follow-up message to show.
ipcMain.handle('popup-action', (_e, action) => {
  const card = current;
  if (!card) return null;
  const state = store.loadState();
  let reply = null;
  switch (card.kind) {
    case 'goal':
      if (action === 'done') {
        store.update((d) => d.log.push({ goal: card.goalId, at: Date.now() }));
        const d = store.load();
        const g = d.goals.find((x) => x.id === card.goalId);
        const p = g && store.progress(d, g);
        const s = g && store.streak(d, g);
        reply = p ? (p.met ? `Nice! "${g.title}" is done for ${g.cadence === 'daily' ? 'today' : `this ${g.cadence.replace('ly', '')}`}.${s > 1 ? ` ${s}-day streak 🔥` : ''}` : `Logged -- ${p.done} of ${p.target} so far.`) : 'Logged!';
        ctx.react('celebrate');
      } else if (action === 'skip') {
        store.update((d) => d.skips.push({ goal: card.goalId, day: store.dayKey() }));
        reply = "Okay, skipping today. Tomorrow's a new one.";
      } else {
        state.goalSnooze = { ...(state.goalSnooze || {}), [card.goalId]: Date.now() + 90 * 60000 };
        reply = pick(['No stress -- I will check in again later.', 'Got it. Maybe block 20 minutes for it this afternoon?']);
      }
      break;
    case 'water':
      if (action === 'done') { state.lastWater = Date.now(); reply = 'Refreshed! 💧'; ctx.react('celebrate'); } else state.lastWater = Date.now() - (60 - 15) * 60000; // snooze 15 min
      break;
    case 'mood':
      store.update((d) => d.moods.push({ at: Date.now(), mood: action }));
      state.lastMoodCheck = Date.now();
      reply = MOOD_ADVICE[action] || 'Thanks for telling me.';
      ctx.react(action === 'great' ? 'celebrate' : 'attention');
      break;
    case 'social':
      state.social = { ...(state.social || {}), [card.personId]: Date.now() };
      if (action === 'done') {
        store.update((d) => { const p = d.people.find((x) => x.id === card.personId); if (p) p.lastContact = Date.now(); });
        reply = 'That will have made their day. ❤️';
        ctx.react('celebrate');
      }
      break;
    case 'partner':
      if (action === 'another') {
        const d = store.load();
        current = partnerCard(d);
        return { card: current };
      }
      break;
    case 'cpu':
      if (action === 'monitor') exec('open -a "Activity Monitor"');
      break;
    case 'voice': {
      // A reminder / timer JARVIS set by voice -- report back to it.
      const req = http.request({ host: '127.0.0.1', port: 8094, path: action === 'snooze' ? '/reminders/snooze' : '/reminders/done',
        method: 'POST', timeout: 2000, headers: { 'Content-Type': 'application/json' } }, (res) => res.resume());
      req.on('error', () => {});
      req.end(JSON.stringify({ id: card.reminderId, minutes: 10 }));
      if (action === 'snooze') reply = "Okay, I'll remind you again in 10 minutes.";
      break;
    }
    case 'health':
      if (action === 'walk') { reply = 'Enjoy the walk! 🌳'; ctx.react('celebrate'); }
      break;
    default:
      break;
  }
  store.saveState(state);
  if (!reply) setTimeout(() => close(), 50);
  else setTimeout(() => { if (current === card) close(); }, 5000);
  return reply ? { reply } : null;
});

ipcMain.on('popup-dismiss', () => close());

// --- scheduling -------------------------------------------------------------------
const minutesOf = (hhmm) => { const [h, m] = String(hhmm || '0:0').split(':').map(Number); return h * 60 + (m || 0); };
const nowMinutes = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); };

function inQuietHours(settings) {
  const from = minutesOf(settings.quiet.from), to = minutesOf(settings.quiet.to), n = nowMinutes();
  return from > to ? n >= from || n < to : n >= from && n < to;
}

function goalCard(d, state) {
  if (!d.settings.goalNudges.on) return null;
  const snooze = state.goalSnooze || {};
  const nudged = state.goalNudged || {};
  const today = store.dayKey();
  const n = nowMinutes();
  for (const g of d.goals.filter((x) => !x.archived && x.remind !== false)) {
    const p = store.progress(d, g);
    if (p.met || p.skippedToday || (snooze[g.id] || 0) > Date.now()) continue;
    // Daily goals: from their reminder time (default 17:00); weekly/monthly
    // ones only when behind pace. At most twice a day each.
    const at = g.remindAt ? minutesOf(g.remindAt) : 17 * 60;
    if (g.cadence === 'daily' && n < at) continue;
    if (g.cadence !== 'daily' && !p.behind) continue;
    const times = nudged[g.id]?.day === today ? nudged[g.id].n : 0;
    if (times >= 2) continue;
    state.goalNudged = { ...nudged, [g.id]: { day: today, n: times + 1 } };
    const left = g.cadence === 'daily' ? 'today' : `${p.done}/${p.target} this ${g.cadence.replace('ly', '')}${p.daysLeft ? `, ${p.daysLeft} day${p.daysLeft === 1 ? '' : 's'} left` : ''}`;
    return {
      kind: 'goal', goalId: g.id, icon: '🎯', title: 'Goal check-in',
      text: `${g.title} -- ${left}. ${g.why ? `Remember why: ${g.why}` : 'Done yet?'}`,
      buttons: [{ id: 'done', label: 'Done ✓', primary: true }, { id: 'later', label: 'Not yet' }, { id: 'skip', label: 'Skip today' }],
    };
  }
  return null;
}

function socialCard(d, state) {
  if (!d.settings.social.on) return null;
  const nudged = state.social || {};
  const due = d.people
    .filter((p) => p.everyDays > 0)
    .map((p) => ({ p, days: p.lastContact ? (Date.now() - p.lastContact) / 86400000 : Infinity }))
    .filter(({ p, days }) => days >= p.everyDays && Date.now() - (nudged[p.id] || 0) > 20 * 3600000)
    .sort((a, b) => b.days / b.p.everyDays - a.days / a.p.everyDays)[0];
  if (!due) return null;
  const { p, days } = due;
  const since = Number.isFinite(days) ? `It's been ${Math.floor(days)} days since you talked to ${p.name}.` : `When did you last talk to ${p.name}?`;
  return {
    kind: 'social', personId: p.id, icon: p.relation === 'family' ? '🏡' : p.relation === 'partner' ? '💞' : '📞', title: 'Stay in touch',
    text: `${since} ${pick(['Give them a quick call?', 'A short call or voice note goes a long way.', 'Send a message -- it takes a minute.'])}`,
    buttons: [{ id: 'done', label: 'Called ✓', primary: true }, { id: 'later', label: 'Tomorrow' }],
  };
}

// Apple Health (iPhone Shortcut): a walk nudge in the afternoon when steps
// are behind, and a gentle card in the morning after a short night.
function healthCard(d, state) {
  const cfg = d.settings.health;
  if (!cfg || !cfg.on) return null;
  const h = health.latest();
  if (!h || !h.isToday || Date.now() - h.synced > 4 * 3600000) return null;
  const hour = new Date().getHours();
  const day = store.dayKey();
  if (h.sleepMin && hour >= 7 && hour < 12 && state.sleepCard !== day && h.sleepMin < (cfg.sleepGoalH - 1) * 60) {
    state.sleepCard = day;
    return { kind: 'health', icon: '😴', title: 'Short night',
      text: `You slept ${health.fmtSleep(h.sleepMin)}. Go easy on yourself today: water, some daylight, and maybe an early night.`,
      buttons: [{ id: 'ok', label: 'Will do', primary: true }] };
  }
  if (h.steps !== undefined && hour >= 15 && hour < 20 && state.walkCard !== day && h.steps < cfg.stepGoal * 0.5) {
    state.walkCard = day;
    const pct = Math.round((h.steps / cfg.stepGoal) * 100);
    return { kind: 'health', icon: '🚶', title: 'Time for a walk?',
      text: `You're at ${health.fmtSteps(h.steps)} steps -- ${pct}% of your goal. A 15-minute walk adds about 1.500, and clears your head too.`,
      buttons: [{ id: 'walk', label: 'Going now ✓', primary: true }, { id: 'later', label: 'Later' }] };
  }
  return null;
}

function partnerCard(d) {
  const name = d.settings.partner.name || 'your girlfriend';
  return {
    kind: 'partner', icon: '💝', title: `Idea for ${name}`,
    text: pick(PARTNER_IDEAS),
    buttons: [{ id: 'ok', label: 'Love it', primary: true }, { id: 'another', label: 'Another idea' }],
  };
}

function cpuCard() {
  return new Promise((resolve) => {
    exec('ps -Aceo pcpu,comm -r | head -4', { timeout: 3000 }, (err, out) => {
      const top = err ? [] : out.trim().split('\n').slice(1).map((l) => { const m = l.trim().match(/^([\d.]+)\s+(.*)$/); return m ? { cpu: Number(m[1]), name: m[2] } : null; }).filter(Boolean);
      const avg = cpuSamples.length ? Math.round(cpuSamples.reduce((a, b) => a + b, 0) / cpuSamples.length) : 0;
      resolve({
        kind: 'cpu', icon: '🔥', title: 'Your Mac is working hard',
        text: `CPU at ${avg}%${cpuSamples.length >= CPU_WINDOW ? ' for the last few minutes' : ' right now'}${top[0] ? ` -- mostly ${top[0].name} (${Math.round(top[0].cpu)}%)` : ''}. ${cpuTip(top)}`,
        buttons: [{ id: 'monitor', label: 'Activity Monitor', primary: true }, { id: 'ok', label: 'Got it' }],
      });
    });
  });
}

function sampleCpu() {
  const cpus = os.cpus();
  const t = cpus.reduce((a, c) => { const tt = c.times; a.idle += tt.idle; a.total += tt.user + tt.nice + tt.sys + tt.idle + tt.irq; return a; }, { idle: 0, total: 0 });
  if (lastCpuTimes) {
    const busy = 1 - (t.idle - lastCpuTimes.idle) / Math.max(1, t.total - lastCpuTimes.total);
    cpuSamples.push(Math.round(busy * 100));
    while (cpuSamples.length > CPU_WINDOW) cpuSamples.shift();
  }
  lastCpuTimes = t;
}

async function tick() {
  if (current) return; // one card at a time
  const d = store.load();
  const s = d.settings;
  const state = store.loadState();
  const now = Date.now();
  if (ctx.paused() || ctx.screenBusy() || inQuietHours(s)) return;
  if (powerMonitor.getSystemIdleTime() >= AWAY_SECONDS) {
    state.awaySince = state.awaySince || now; // being away counts as a break
    store.saveState(state);
    return;
  }
  if (state.awaySince) {
    if (now - state.awaySince > 10 * 60000) state.lastWater = now;
    delete state.awaySince;
  }
  if (ctx.petMood() === 'listening') return; // on a call
  const gapOk = now - (state.lastPopup || 0) >= s.minGapMin * 60000;

  let card = null;
  if (s.cpu.on && cpuSamples.length >= CPU_WINDOW && cpuSamples.every((c) => c >= s.cpu.threshold) && now - (state.lastCpu || 0) > CPU_COOLDOWN_MS) {
    card = await cpuCard();
    state.lastCpu = now;
  }
  if (!card && s.water.on && now - (state.lastWater || (state.lastWater = now)) >= s.water.everyMin * 60000 && now - (state.lastPopup || 0) >= 10 * 60000) {
    card = { kind: 'water', icon: '💧', title: 'Water break', text: pick(WATER_LINES),
      buttons: [{ id: 'done', label: 'Done ✓', primary: true }, { id: 'later', label: 'In 15 min' }] };
  }
  if (!card && gapOk) card = healthCard(d, state) || socialCard(d, state) || goalCard(d, state);
  if (!card && gapOk && s.moodCheck.on) {
    const n = nowMinutes();
    const slot = (s.moodCheck.times || []).map(minutesOf).filter((m) => n >= m && n < m + 120).pop();
    const lastDay = state.lastMoodCheck ? store.dayKey(state.lastMoodCheck) : '';
    const lastMin = state.lastMoodCheck ? new Date(state.lastMoodCheck).getHours() * 60 + new Date(state.lastMoodCheck).getMinutes() : -1;
    if (slot !== undefined && !(lastDay === store.dayKey() && lastMin >= slot)) {
      state.lastMoodCheck = now;
      card = { kind: 'mood', icon: '😊', title: 'Mood check-in', text: 'How are you feeling right now?',
        buttons: [{ id: 'great', label: 'Great', primary: true }, { id: 'okay', label: 'Okay' }, { id: 'tired', label: 'Tired' }, { id: 'stressed', label: 'Stressed' }] };
    }
  }
  if (!card && gapOk && s.partner.on && now - (state.lastPartner || 0) > 2.5 * 86400000) {
    const h = new Date().getHours();
    if (h >= 12 && h < 21) { card = partnerCard(d); state.lastPartner = now; }
  }
  store.saveState(state);
  if (card) show(card);
}

function init(context) {
  ctx = context;
  sampleCpu();
  setInterval(sampleCpu, CPU_SAMPLE_MS);
  setInterval(() => tick().catch((e) => console.error('[reminders]', e)), 60000);
  setTimeout(() => tick().catch(() => {}), 15000);
}

// Tray "Test a reminder": show one of each kind in turn.
let demoIndex = 0;
async function demo() {
  const d = store.load();
  const cards = [
    { kind: 'mood', icon: '😊', title: 'Mood check-in', text: 'How are you feeling right now?',
      buttons: [{ id: 'great', label: 'Great', primary: true }, { id: 'okay', label: 'Okay' }, { id: 'tired', label: 'Tired' }, { id: 'stressed', label: 'Stressed' }] },
    { kind: 'water', icon: '💧', title: 'Water break', text: pick(WATER_LINES), buttons: [{ id: 'done', label: 'Done ✓', primary: true }, { id: 'later', label: 'In 15 min' }] },
    partnerCard(d),
    await cpuCard(),
  ];
  show(cards[demoIndex++ % cards.length]);
}

// A due reminder / timer from JARVIS (POST 8092/reminder/card). Replaces
// whatever card is up -- it's time-critical.
function showVoiceReminder({ id, text, kind }) {
  const timer = kind === 'timer';
  show({
    kind: 'voice', reminderId: id, icon: timer ? '⏱️' : '⏰', title: timer ? "Time's up!" : 'Reminder',
    text: timer ? `Your ${text} is done.` : text.charAt(0).toUpperCase() + text.slice(1),
    buttons: [{ id: 'done', label: 'Done ✓', primary: true }, { id: 'snooze', label: 'Snooze 10 min' }],
  });
}

module.exports = { init, demo, close, showVoiceReminder, isShowing: () => !!current };
