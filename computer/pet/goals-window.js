// The "Goals & reminders" window (goals.html): goals, people to stay in
// touch with, reminder settings, and the pet's character. Opened from the
// pet's tray menu or PA (POST 127.0.0.1:8092/goals).
const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('node:path');
const store = require('./store');
const health = require('./health');

let win = null;
let onCharacter = () => {};

function view() {
  const d = store.load();
  return {
    ...d,
    goals: d.goals.filter((g) => !g.archived).map((g) => ({ ...g, progress: store.progress(d, g), streak: store.streak(d, g) })),
    people: d.people.map((p) => ({ ...p, daysSince: p.lastContact ? Math.floor((Date.now() - p.lastContact) / 86400000) : null })),
    recentMoods: d.moods.slice(-7),
    health: health.latest(),
  };
}

ipcMain.handle('goals-get', () => view());
ipcMain.handle('goals-save-goal', (_e, g) => {
  store.update((d) => {
    const clean = { title: String(g.title || '').trim().slice(0, 120), cadence: ['daily', 'weekly', 'monthly'].includes(g.cadence) ? g.cadence : 'daily',
      target: Math.max(1, Math.min(31, Number(g.target) || 1)), why: String(g.why || '').slice(0, 200), remind: g.remind !== false,
      remindAt: /^\d{1,2}:\d{2}$/.test(g.remindAt || '') ? g.remindAt : null };
    if (!clean.title) return;
    const existing = d.goals.find((x) => x.id === g.id);
    if (existing) Object.assign(existing, clean);
    else d.goals.push({ id: store.newId(), created: Date.now(), ...clean });
  });
  return view();
});
ipcMain.handle('goals-delete-goal', (_e, id) => { store.update((d) => { const g = d.goals.find((x) => x.id === id); if (g) g.archived = true; }); return view(); });
ipcMain.handle('goals-log', (_e, id) => { store.update((d) => d.log.push({ goal: id, at: Date.now() })); return view(); });
ipcMain.handle('goals-unlog', (_e, id) => {
  store.update((d) => { for (let i = d.log.length - 1; i >= 0; i--) if (d.log[i].goal === id) { d.log.splice(i, 1); break; } });
  return view();
});
ipcMain.handle('goals-save-person', (_e, p) => {
  store.update((d) => {
    const clean = { name: String(p.name || '').trim().slice(0, 60), relation: ['family', 'friend', 'partner'].includes(p.relation) ? p.relation : 'friend',
      everyDays: Math.max(1, Math.min(365, Number(p.everyDays) || 7)) };
    if (!clean.name) return;
    const existing = d.people.find((x) => x.id === p.id);
    if (existing) Object.assign(existing, clean);
    else d.people.push({ id: store.newId(), lastContact: null, ...clean });
  });
  return view();
});
ipcMain.handle('goals-delete-person', (_e, id) => { store.update((d) => { d.people = d.people.filter((x) => x.id !== id); }); return view(); });
ipcMain.handle('goals-contacted', (_e, id) => { store.update((d) => { const p = d.people.find((x) => x.id === id); if (p) p.lastContact = Date.now(); }); return view(); });
ipcMain.handle('goals-save-settings', (_e, s) => { store.update((d) => { d.settings = { ...d.settings, ...s }; }); return view(); });
ipcMain.handle('goals-set-character', (_e, c) => { store.update((d) => { d.character = String(c); }); onCharacter(String(c)); return view(); });

function open() {
  app.focus({ steal: true });
  if (win && !win.isDestroyed()) { win.show(); win.focus(); return; }
  win = new BrowserWindow({
    width: 560, height: 720, minWidth: 460, minHeight: 520, title: 'Goals & reminders', backgroundColor: '#f3f4f9',
    titleBarStyle: 'hiddenInset',
    webPreferences: { preload: path.join(__dirname, 'goals-preload.js'), contextIsolation: true, nodeIntegration: false },
  });
  win.loadFile(path.join(__dirname, 'goals.html'));
  win.on('closed', () => { win = null; });
}

module.exports = { open, setCharacterListener: (fn) => { onCharacter = fn; } };
