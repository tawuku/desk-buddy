const { app, BrowserWindow, Tray, Menu, powerMonitor, screen, ipcMain, nativeImage, Notification, globalShortcut } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const http = require('node:http');
const { exec } = require('node:child_process');
const store = require('./store');
const reminders = require('./reminders');
const goalsWindow = require('./goals-window');

const JARVIS_ROOT = path.join(__dirname, '..', '..');
const STATE_DIR = path.join(JARVIS_ROOT, 'database', 'pet');
const STATE_FILE = path.join(STATE_DIR, 'window-position.json');
const TASKS_FILE = path.join(JARVIS_ROOT, 'TASKS.md');
const README_FILE = path.join(JARVIS_ROOT, 'README.md');

// JARVIS Lite is one LaunchAgent (computer/voice/wake_listener.py), which
// runs the model and speech servers as its own child processes.
const SERVICE_LABELS = {
  voiceWake: 'com.jarvis.voice-wake',
};
const LAUNCH_AGENTS_DIR = path.join(require('node:os').homedir(), 'Library', 'LaunchAgents');
function plistPath(label) {
  return path.join(LAUNCH_AGENTS_DIR, `${label}.plist`);
}

const WINDOW_SIZE = { width: 170, height: 200 }; // the little man + room for speech bubbles
const IDLE_ASLEEP_SECONDS = 5 * 60;
const APP_POLL_MS = 1500;
const WINDOW_DISPLAY_POLL_MS = 2000;
const IDLE_POLL_MS = 5000;
const MINUTE_TICK_MS = 60000;
const RUN_ANIMATION_MS = 700;

// --- Wake panel: an Iron-Man-HUD-style card shown on "hey Jarvis", driven
// live by the voice pipeline's own stages (listening/heard/thinking/reply)
// -- not just a static snapshot. See panel.html/panel-renderer.js for the
// visual side and computer/voice/wake_listener.py for the stage pushes.
const PANEL_SIZE = { width: 520, height: 420 }; // height follows the card (see panel-resize)
const PANEL_AUTO_HIDE_MS = 15000; // manual tray-preview only (no live stages behind it)
const PANEL_MAX_TASKS = 6;
// Loopback-only, like every other JARVIS service's ports (8080 llama-server,
// 8090 tts-server, 18789 gateway) -- this is how computer/voice/wake_listener.py
// tells this already-running app what's happening, since they're separate
// processes (Python vs. Electron) with no other shared channel.
const PANEL_SERVER_PORT = 8092;

// --- JARVIS screen: the full-screen findings briefing (computer/screen/),
// served by the JARVIS app itself on 8094 (computer/voice/screen_server.py)
// so it gets live findings and Piper audio. One window per display: the
// primary display's leads (speaks, runs the briefing), the rest mirror it.
const SCREEN_URL = 'http://127.0.0.1:8094/';
// Minimized: the lead window shrinks to a corner mini-player, mirrors hide.
const SCREEN_MINI_SIZE = { width: 400, height: 116 };
// Kill switch, registered only while the screen is up.
const SCREEN_SHORTCUTS = { close: 'CommandOrControl+Shift+Escape', toggle: 'CommandOrControl+Shift+M' };

// Frontmost-app process name (case-insensitive substring match) -> mood.
// First match wins; unmatched apps fall back to 'idle'.
const APP_MOOD_RULES = [
  { mood: 'coding', patterns: ['code', 'cursor', 'terminal', 'iterm', 'xcode', 'vim', 'zed', 'sublime', 'webstorm', 'intellij', 'pycharm'] },
  { mood: 'listening', patterns: ['zoom', 'facetime', 'slack', 'teams', 'discord', 'meet'] },
  { mood: 'vibing', patterns: ['music', 'spotify', 'podcasts'] },
  { mood: 'browsing', patterns: ['safari', 'chrome', 'arc', 'firefox', 'brave', 'edge'] },
];

// Continuous-time-in-mood nudge thresholds, in minutes. Moods not listed here
// never get a "you've been at this a while" nudge (e.g. listening: don't
// interrupt a call).
const TIME_NUDGE_THRESHOLD_MIN = { browsing: 45, vibing: 45, coding: 90 };
const UNDONE_NUDGE_AWAKE_MS = 2 * 60 * 60 * 1000; // 2h of awake time

let win = null;
let panelWin = null;
let screenWins = [];
let chatWin = null;
let bootWins = [];
let bootTimer = null;
let screenMode = 'full';
let tray = null;
let paused = false;
let lastMood = null;
let moodStreakStart = Date.now();
let nudgedAtLevel = 0;
let awakeMsSinceUndoneNudge = 0;
let currentDisplayId = null;
let running = false;
let appPollTimer = null;
let windowDisplayPollTimer = null;
let idlePollTimer = null;
let minuteTickTimer = null;

// --- Per-display window position persistence ---

function loadPositions() {
  try {
    const raw = fs.readFileSync(STATE_FILE, 'utf8');
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && parsed.byDisplay) return parsed.byDisplay;
  } catch {
    // no saved positions yet
  }
  return {};
}

let positionsByDisplay = loadPositions();

function savePositionForDisplay(displayId, x, y) {
  positionsByDisplay[displayId] = { x, y };
  try {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    fs.writeFileSync(STATE_FILE, JSON.stringify({ byDisplay: positionsByDisplay }));
  } catch {
    // best-effort; losing the saved position isn't worth surfacing an error for
  }
}

function defaultPositionForDisplay(display) {
  const { workArea } = display;
  return {
    x: workArea.x + workArea.width - WINDOW_SIZE.width - 24,
    y: workArea.y + workArea.height - WINDOW_SIZE.height - 24,
  };
}

function notify(title, body) {
  if (!Notification.isSupported()) return;
  new Notification({ title, body }).show();
}

// --- Window ---

function createWindow() {
  const primary = screen.getPrimaryDisplay();
  currentDisplayId = primary.id;
  const pos = positionsByDisplay[primary.id] || defaultPositionForDisplay(primary);

  win = new BrowserWindow({
    width: WINDOW_SIZE.width,
    height: WINDOW_SIZE.height,
    x: pos.x,
    y: pos.y,
    frame: false,
    transparent: true,
    hasShadow: false,
    resizable: false,
    movable: true,
    skipTaskbar: true,
    alwaysOnTop: true,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });

  win.setAlwaysOnTop(true, 'floating');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setIgnoreMouseEvents(false);

  win.webContents.on('console-message', (event) => {
    console.log(`[renderer] ${event.message} (${event.sourceId}:${event.lineNumber})`);
  });
  win.webContents.on('did-fail-load', (_e, code, desc) => {
    console.error(`[renderer] failed to load: ${code} ${desc}`);
  });
  win.webContents.on('render-process-gone', (_e, details) => {
    console.error('[renderer] process gone:', details);
  });

  win.loadFile(path.join(__dirname, 'index.html'));

  let saveTimer = null;
  win.on('moved', () => {
    if (running) return; // don't persist intermediate frames of a run animation
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const b = win.getBounds();
      savePositionForDisplay(currentDisplayId, b.x, b.y);
    }, 400);
  });
}

// --- Frontmost-app mood detection (process name only, no permission needed) ---

function detectFrontmostMood() {
  exec(
    `osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true'`,
    { timeout: 2000 },
    (err, stdout) => {
      if (err || paused || running) return;
      const name = stdout.trim().toLowerCase();
      let mood = 'idle';
      for (const rule of APP_MOOD_RULES) {
        if (rule.patterns.some((p) => name.includes(p))) {
          mood = rule.mood;
          break;
        }
      }
      applyMood(mood);
    }
  );
}

function applyMood(mood) {
  if (mood === lastMood) return;
  lastMood = mood;
  moodStreakStart = Date.now();
  nudgedAtLevel = 0;
  if (win && !win.isDestroyed()) {
    win.webContents.send('mood-update', mood);
  }
}

// --- Multi-monitor: follow the frontmost window's screen ---
// Reading another app's window position (not just its name) is a step up in
// access -- it goes through System Events too, but this call needs macOS
// Accessibility permission for whichever process runs it. The first attempt
// after granting/denying may show a system prompt; until granted, this just
// fails silently and the pet stays put (no crash, no repeated nagging).

function detectFrontmostWindowDisplay() {
  if (paused || running) return;
  exec(
    `osascript -e '
    tell application "System Events"
      set frontApp to first application process whose frontmost is true
      try
        set b to position of front window of frontApp
        set s to size of front window of frontApp
        return ((item 1 of b) as string) & "," & ((item 2 of b) as string) & "," & ((item 1 of s) as string) & "," & ((item 2 of s) as string)
      end try
    end tell'`,
    { timeout: 2000 },
    (err, stdout) => {
      if (err) return;
      const parts = stdout.trim().split(',').map(Number);
      if (parts.length !== 4 || parts.some((n) => Number.isNaN(n))) return;
      const [x, y, w, h] = parts;
      const centerPoint = { x: Math.round(x + w / 2), y: Math.round(y + h / 2) };
      const display = screen.getDisplayNearestPoint(centerPoint);
      if (display.id !== currentDisplayId) runToDisplay(display);
    }
  );
}

function runToDisplay(display) {
  if (!win || win.isDestroyed() || running) return;
  running = true;
  currentDisplayId = display.id;
  win.webContents.send('mood-update', 'running');

  const target = positionsByDisplay[display.id] || defaultPositionForDisplay(display);
  const start = win.getBounds();
  const steps = Math.max(1, Math.round(RUN_ANIMATION_MS / 16));
  let i = 0;

  const timer = setInterval(() => {
    i += 1;
    const t = i / steps;
    const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; // easeInOutQuad
    if (win && !win.isDestroyed()) {
      win.setPosition(
        Math.round(start.x + (target.x - start.x) * eased),
        Math.round(start.y + (target.y - start.y) * eased)
      );
    }
    if (i >= steps) {
      clearInterval(timer);
      running = false;
      savePositionForDisplay(display.id, target.x, target.y);
      lastMood = null; // force the real app-driven mood to be re-pushed
      detectFrontmostMood();
    }
  }, 16);
}

// --- Idle -> asleep ---

function checkIdle() {
  if (paused || running) return;
  const idleSeconds = powerMonitor.getSystemIdleTime();
  if (idleSeconds >= IDLE_ASLEEP_SECONDS) applyMood('asleep');
}

// --- Minute-granularity checks: time-spent nudges, TASKS.md nudges ---

function humanMinutes(ms) {
  return Math.round(ms / 60000);
}

function checkTimeNudge() {
  const threshold = TIME_NUDGE_THRESHOLD_MIN[lastMood];
  if (!threshold) return;
  const minutes = (Date.now() - moodStreakStart) / 60000;
  const level = Math.floor(minutes / threshold);
  if (level > 0 && level > nudgedAtLevel) {
    nudgedAtLevel = level;
    notify('JARVIS', `You've been ${lastMood} for about ${Math.round(minutes)} min -- worth a break?`);
  }
}

function oldestOpenTask() {
  let text;
  try {
    text = fs.readFileSync(TASKS_FILE, 'utf8');
  } catch {
    return null;
  }
  const match = text.split('\n').find((line) => /^-\s*\[ \]\s*\S/.test(line));
  if (!match) return null;
  return match.replace(/^-\s*\[ \]\s*/, '').trim();
}

function checkUndoneNudge() {
  if (lastMood === 'asleep') return; // clock only runs while you're around
  awakeMsSinceUndoneNudge += MINUTE_TICK_MS;
  if (awakeMsSinceUndoneNudge < UNDONE_NUDGE_AWAKE_MS) return;
  const task = oldestOpenTask();
  awakeMsSinceUndoneNudge = 0;
  if (task) notify('JARVIS', `Still open: "${task}" -- want to knock that out?`);
}

function minuteTick() {
  if (paused || running) return;
  checkTimeNudge();
  checkUndoneNudge();
}

// --- JARVIS service control, exposed through the tray so none of this needs
// a terminal. ---

function restartService(label, humanName) {
  exec(`launchctl kickstart -k gui/${process.getuid()}/${label}`, (err) => {
    if (err) {
      notify('JARVIS', `Couldn't restart ${humanName}: ${err.message}`);
    } else {
      notify('JARVIS', `Restarting ${humanName}...`);
    }
  });
}

// --- Voice wake toggle: unlike the other services, this one has a real
// on/off switch (privacy-sensitive -- it's the microphone), not just
// restart. "On" = the LaunchAgent is loaded (and RunAtLoad means it'll
// still be on at next login too, until toggled off again). ---

function isVoiceWakeEnabled(callback) {
  exec(`launchctl list ${SERVICE_LABELS.voiceWake}`, (err) => callback(!err));
}

function setVoiceWakeEnabled(enabled) {
  const plist = plistPath(SERVICE_LABELS.voiceWake);
  const cmd = enabled ? `launchctl load -w "${plist}"` : `launchctl unload -w "${plist}"`;
  exec(cmd, (err) => {
    if (err && !enabled) return; // already unloaded -- fine
    notify('JARVIS', enabled ? 'Voice wake is on -- say "wake up Jarvis"' : 'Voice wake is off');
    refreshTrayMenu();
  });
}

// --- Wake panel: a small card showing todos + service status, triggered by
// "hey Jarvis" (computer/voice/wake_listener.py POSTs to the local HTTP
// server below the instant the wake word fires -- deliberately not tied to
// waiting for the actual LLM reply, which can take anywhere from seconds to
// 10-30 minutes on this hardware; see the root DEVELOPMENT_LOG.md). The
// reply itself is still spoken (XTTS-v2, Build 006) and notified separately,
// same as before -- this panel is an immediate acknowledgment + status
// glance, not a chat transcript. ---

function allOpenTasks(limit) {
  let text;
  try {
    text = fs.readFileSync(TASKS_FILE, 'utf8');
  } catch {
    return { tasks: [], moreCount: 0 };
  }
  const all = text
    .split('\n')
    .filter((line) => /^-\s*\[ \]\s*\S/.test(line))
    .map((line) => line.replace(/^-\s*\[ \]\s*/, '').trim());
  return { tasks: all.slice(0, limit), moreCount: Math.max(0, all.length - limit) };
}

function readBuildLine() {
  try {
    const text = fs.readFileSync(README_FILE, 'utf8');
    const build = text.match(/\*\*Current build:\*\*\s*(.+?)\s*$/m);
    if (!build) return 'JARVIS';
    let line = `Build ${build[1].trim()}`;
    return line.length > 90 ? `${line.slice(0, 87)}...` : line;
  } catch {
    return 'JARVIS';
  }
}

function checkServiceUp(label) {
  return new Promise((resolve) => {
    exec(`launchctl list ${label}`, { timeout: 2000 }, (err) => resolve(!err));
  });
}

// JARVIS's child servers answer on loopback ports (model 8080, speech 8093).
function checkPortUp(port, urlPath = '/') {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: urlPath, timeout: 1500 }, (res) => {
      res.resume();
      resolve(true);
    });
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

// Like checkPortUp, but only a 200 counts -- llama-server answers 503 while
// the model is still loading.
function checkHealthy(port, urlPath) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: urlPath, timeout: 1500 }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

async function gatherPanelData() {
  const { tasks, moreCount } = allOpenTasks(PANEL_MAX_TASKS);
  const [voiceWake, model, speech] = await Promise.all([
    checkServiceUp(SERVICE_LABELS.voiceWake),
    checkPortUp(8080, '/health'),
    checkPortUp(8093),
  ]);
  return {
    time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    tasks,
    moreCount,
    buildLine: readBuildLine(),
    services: { jarvis: voiceWake, model, speech },
    autoHideMs: PANEL_AUTO_HIDE_MS,
  };
}

function panelPosition() {
  // Top-center, HUD-style -- the old top-right corner card worked for a
  // glance-sized status snapshot, but a live conversation surface earns a
  // more prominent, deliberate spot.
  const display = screen.getPrimaryDisplay();
  const { workArea } = display;
  return {
    x: workArea.x + Math.round((workArea.width - PANEL_SIZE.width) / 2),
    y: workArea.y + 16,
  };
}

function createPanelWindow() {
  const pos = panelPosition();
  const w = new BrowserWindow({
    width: PANEL_SIZE.width,
    height: PANEL_SIZE.height,
    x: pos.x,
    y: pos.y,
    frame: false,
    transparent: true,
    hasShadow: false,
    resizable: false,
    movable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'panel-preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  w.setAlwaysOnTop(true, 'floating');
  w.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  w.webContents.on('console-message', (event) => {
    console.log(`[panel-renderer] ${event.message} (${event.sourceId}:${event.lineNumber})`);
  });
  w.webContents.on('did-fail-load', (_e, code, desc) => {
    console.error(`[panel-renderer] failed to load: ${code} ${desc}`);
  });
  const loaded = new Promise((resolve) => w.webContents.once('did-finish-load', resolve));
  w.loadFile(path.join(__dirname, 'panel.html'));
  return { window: w, loaded };
}

// Recreated on every call rather than reused: gatherPanelData() already
// takes tens of ms (a few `launchctl list` child processes), and a fresh
// window sidesteps ever racing "send data before the renderer's listener is
// registered" -- BrowserWindow creation is cheap and this fires rarely
// (once per "hey Jarvis," not a hot path).
async function showPanel() {
  const old = panelWin;
  const { window: w, loaded } = createPanelWindow();
  panelWin = w;
  const [data] = await Promise.all([gatherPanelData(), loaded]);
  if (panelWin !== w || w.isDestroyed()) return; // superseded or quit raced us
  w.webContents.send('panel-data', data);
  w.showInactive();
  if (old && !old.isDestroyed()) old.destroy();
  return w;
}

// Pushes one voice-pipeline stage (listening/heard/thinking/reply/error/
// empty -- see panel-renderer.js's STAGE_LABEL) into whatever panel window
// is currently open, creating one first if the wake word just fired and
// nothing's showing yet. Stages with nothing left to say (reply/error/
// empty) get their own hold-then-fade timer client-side; everything else
// stays up until the next stage arrives -- a cold reply on this hardware
// can legitimately take up to ~30 minutes (see root DEVELOPMENT_LOG.md).
async function pushPanelState(stage, text, extra = {}) {
  // While the JARVIS screen is up it is the display; don't stack the HUD on it.
  if (screenWins.some((w) => !w.isDestroyed())) return;
  let w = panelWin;
  if (!w || w.isDestroyed()) {
    w = await showPanel();
    if (!w) return;
  }
  if (w.isDestroyed()) return;
  w.webContents.send('panel-state', { ...extra, stage, text });
}

// The card grows with its step list / reply; the transparent window follows
// so it never blocks clicks on the screen below the visible card.
ipcMain.on('panel-resize', (_e, height) => {
  if (!panelWin || panelWin.isDestroyed()) return;
  const h = Math.max(120, Math.min(820, Math.round(Number(height) || 0)));
  panelWin.setSize(PANEL_SIZE.width, h);
});

ipcMain.on('panel-dismiss', () => {
  if (panelWin && !panelWin.isDestroyed()) panelWin.destroy();
});

// --- Chat: type to JARVIS (computer/screen/chat.html, served by JARVIS on
// 8094 -- POST /ask streams the answer). ⌘⇧J toggles it from anywhere.
const CHAT_SHORTCUT = 'CommandOrControl+Shift+J';

function toggleChat() {
  if (chatWin && !chatWin.isDestroyed() && chatWin.isVisible() && chatWin.isFocused()) {
    chatWin.hide();
    return;
  }
  if (!chatWin || chatWin.isDestroyed()) {
    const { workArea } = screen.getPrimaryDisplay();
    chatWin = new BrowserWindow({
      width: 440, height: 640, minWidth: 360, minHeight: 420,
      x: workArea.x + workArea.width - 460, y: workArea.y + workArea.height - 660,
      title: 'Chat with JARVIS', titleBarStyle: 'hiddenInset', backgroundColor: '#05080e', show: false,
      webPreferences: { preload: path.join(__dirname, 'chat-preload.js'), contextIsolation: true, nodeIntegration: false },
    });
    chatWin.webContents.on('did-fail-load', () => {
      chatWin.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(
        '<body style="background:#05080e;color:#cfe6ff;font:15px -apple-system;display:grid;place-items:center;height:90vh;text-align:center">'
        + '<div><b>JARVIS isn\'t running</b><br><br>Switch it on in PA, then press ⌘⇧J again.</div></body>'));
    });
    chatWin.on('close', (e) => { if (!app.isQuitting) { e.preventDefault(); chatWin.hide(); } });
    chatWin.loadURL(`${SCREEN_URL}chat`);
    chatWin.once('ready-to-show', () => { app.focus({ steal: true }); chatWin.show(); chatWin.webContents.send('chat-shown'); });
    return;
  }
  if (chatWin.webContents.getURL().startsWith('data:')) chatWin.loadURL(`${SCREEN_URL}chat`); // JARVIS may be up now
  app.focus({ steal: true });
  chatWin.show();
  chatWin.focus();
  chatWin.webContents.send('chat-shown');
}

ipcMain.on('chat-hide', () => { if (chatWin && !chatWin.isDestroyed()) chatWin.hide(); });

function closeScreens() {
  const old = screenWins;
  screenWins = [];
  for (const w of old) if (!w.isDestroyed()) w.destroy();
  if (old.length) reportScreenClosed();
  unregisterScreenShortcuts();
}

function registerScreenShortcuts() {
  unregisterScreenShortcuts();
  globalShortcut.register(SCREEN_SHORTCUTS.close, closeScreens);
  globalShortcut.register(SCREEN_SHORTCUTS.toggle, () => setScreenMode(screenMode === 'mini' ? 'full' : 'mini'));
}

function unregisterScreenShortcuts() {
  for (const accel of Object.values(SCREEN_SHORTCUTS)) {
    if (globalShortcut.isRegistered(accel)) globalShortcut.unregister(accel);
  }
}

function setScreenMode(mode) {
  const live = screenWins.filter((w) => !w.isDestroyed());
  if (!live.length || (mode !== 'mini' && mode !== 'full')) return;
  screenMode = mode;
  const [lead, ...mirrors] = live;
  const primary = screen.getPrimaryDisplay();
  if (mode === 'mini') {
    const { workArea } = primary;
    for (const w of mirrors) w.hide();
    lead.setAlwaysOnTop(true, 'floating');
    lead.setBounds({
      x: workArea.x + workArea.width - SCREEN_MINI_SIZE.width - 20,
      y: workArea.y + workArea.height - SCREEN_MINI_SIZE.height - 20,
      ...SCREEN_MINI_SIZE,
    });
  } else {
    lead.setAlwaysOnTop(true, 'screen-saver');
    lead.setBounds(primary.bounds);
    for (const w of mirrors) w.showInactive();
    lead.focus();
  }
  for (const w of live) w.webContents.send('screen-mode', mode);
}

ipcMain.on('screen-mode', (_e, mode) => setScreenMode(mode));
ipcMain.on('screen-close', () => closeScreens());

// destroy() skips the page's own unload report, so tell JARVIS directly --
// it waits on the screen's phase to know when a briefing is over.
function reportScreenClosed() {
  const req = http.request({ host: '127.0.0.1', port: 8094, path: '/screen/status', method: 'POST', timeout: 1500,
    headers: { 'Content-Type': 'application/json' } }, (res) => res.resume());
  req.on('timeout', () => req.destroy());
  req.on('error', () => {});
  req.end(JSON.stringify({ phase: 'closed' }));
}

function showScreens({ autostart = false } = {}) {
  closeScreens();
  screenMode = 'full';
  const primary = screen.getPrimaryDisplay();
  const displays = [primary, ...screen.getAllDisplays().filter((d) => d.id !== primary.id)];
  screenWins = displays.map((display, i) => {
    const w = new BrowserWindow({
      ...display.bounds,
      frame: false,
      resizable: false,
      movable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      enableLargerThanScreen: true,
      show: false,
      backgroundColor: '#03060b',
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        backgroundThrottling: false,
        autoplayPolicy: 'no-user-gesture-required', // it speaks without a click
        preload: path.join(__dirname, 'screen-preload.js'),
      },
    });
    // Above the menu bar and Dock, on every Space, like a screen saver.
    w.setAlwaysOnTop(true, 'screen-saver');
    w.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    w.setBounds(display.bounds);
    const query = new URLSearchParams({ embedded: '1' });
    if (i > 0) query.set('role', 'mirror');
    else if (autostart) query.set('autostart', '1');
    w.webContents.on('console-message', (event) => {
      if (event.level === 'error') console.log(`[screen] ${event.message} (${event.sourceId}:${event.lineNumber})`);
    });
    w.webContents.on('did-fail-load', (_e, code, desc) => {
      console.error(`[screen] failed to load: ${code} ${desc}`);
      closeScreens();
      notify('JARVIS', "The JARVIS screen needs JARVIS running -- switch it on in PA.");
    });
    w.once('ready-to-show', () => (i === 0 ? (w.show(), w.focus()) : w.showInactive()));
    // Closing any one (DISMISS / Esc / auto-close) closes them all.
    w.on('closed', () => {
      if (!screenWins.includes(w)) return;
      const rest = screenWins.filter((x) => x !== w);
      screenWins = [];
      for (const x of rest) if (!x.isDestroyed()) x.destroy();
      reportScreenClosed();
      unregisterScreenShortcuts();
    });
    w.loadURL(`${SCREEN_URL}?${query}`);
    return w;
  });
  // The HUD card would sit on top of the lead screen -- it has taken over.
  if (panelWin && !panelWin.isDestroyed()) panelWin.destroy();
  registerScreenShortcuts();
}

// --- Boot intro: full-screen on every display while JARVIS starts up
// (computer/boot/boot.sh -> POST /boot). Follows the real startup: the voice
// app's screen server (8094), speech recognition (8093) and the model
// (8080), then greets out loud through JARVIS and fades out.
const BOOT_MAX_MS = 90000;

function userName() {
  try {
    return JSON.parse(fs.readFileSync(path.join(JARVIS_ROOT, 'config', 'jarvis_sources.json'), 'utf8')).user_name || 'sir';
  } catch {
    return 'sir';
  }
}

function sayViaJarvis(text) {
  const req = http.request({ host: '127.0.0.1', port: 8094, path: '/say', method: 'POST', timeout: 3000,
    headers: { 'Content-Type': 'application/json' } }, (res) => res.resume());
  req.on('timeout', () => req.destroy());
  req.on('error', () => {});
  req.end(JSON.stringify({ text }));
}

function closeBoot() {
  clearInterval(bootTimer);
  bootTimer = null;
  const old = bootWins;
  bootWins = [];
  for (const w of old) if (!w.isDestroyed()) w.destroy();
}

function showBoot() {
  closeBoot();
  // Remember that the intro played this boot, so the first "hey Jarvis"
  // doesn't play it again (computer/voice/wake_listener.py intro_after_reboot).
  exec('sysctl -n kern.boottime', (err, out) => {
    const m = !err && String(out).match(/sec = (\d+)/);
    if (!m) return;
    try {
      fs.mkdirSync(STATE_DIR, { recursive: true });
      fs.writeFileSync(path.join(STATE_DIR, 'intro-boot.txt'), m[1]);
    } catch { /* best-effort */ }
  });
  const primary = screen.getPrimaryDisplay();
  const displays = [primary, ...screen.getAllDisplays().filter((d) => d.id !== primary.id)];
  const hour = new Date().getHours();
  const part = hour >= 5 && hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
  const name = userNameFromConfig();
  const greeting = FULL_JARVIS ? `Good ${part}, ${userName()}. JARVIS is online.` : `Good ${part}${name ? `, ${name}` : ''}! I'm up.`;
  bootWins = displays.map((display, i) => {
    const w = new BrowserWindow({
      ...display.bounds,
      frame: false, resizable: false, movable: false, skipTaskbar: true, alwaysOnTop: true,
      enableLargerThanScreen: true, show: false, backgroundColor: '#000000',
      webPreferences: { contextIsolation: true, nodeIntegration: false, backgroundThrottling: false,
        preload: path.join(__dirname, 'boot-preload.js') },
    });
    w.setAlwaysOnTop(true, 'screen-saver');
    w.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    w.setBounds(display.bounds);
    w.once('ready-to-show', () => (i === 0 ? w.show() : w.showInactive()));
    w.loadFile(path.join(JARVIS_ROOT, 'computer', 'screen', 'boot.html'), {
      query: { greeting, ...(i > 0 ? { role: 'mirror' } : {}), ...(FULL_JARVIS ? {} : { petOnly: '1' }) },
    });
    return w;
  });
  const started = Date.now();
  let greeted = false;
  const up = { voice: false, speech: false, model: false }; // sticky: a busy moment isn't "down"
  bootTimer = setInterval(async () => {
    // Pet only: nothing else to wait for -- the intro just plays.
    const [voice, speech, model] = FULL_JARVIS
      ? await Promise.all([checkPortUp(8094, '/screen/poll'), checkPortUp(8093), checkHealthy(8080, '/health')])
      : [true, true, true];
    up.voice ||= voice;
    up.speech ||= speech;
    up.model ||= model;
    const ready = up.voice && up.speech && up.model;
    const status = { hud: true, ...up, ready, elapsed: Date.now() - started, timedOut: Date.now() - started > BOOT_MAX_MS };
    for (const w of bootWins) if (!w.isDestroyed()) w.webContents.send('boot-status', status);
    if (ready && !greeted) {
      greeted = true;
      if (FULL_JARVIS) sayViaJarvis(greeting);
      // The pet waves hello as the intro ends (its speech bubble has the greeting).
      setTimeout(() => sendToPet('pet-greet', greeting.replace(' JARVIS is online.', '')), FULL_JARVIS ? 5500 : 6000);
    }
    if (status.timedOut) clearInterval(bootTimer);
  }, 600);
}

ipcMain.on('boot-done', () => closeBoot());

function readJsonBody(req) {
  return new Promise((resolve) => {
    let raw = '';
    req.on('data', (chunk) => { raw += chunk; });
    req.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve({});
      }
    });
    req.on('error', () => resolve({}));
  });
}

function startPanelServer() {
  const server = http.createServer((req, res) => {
    if (req.method === 'POST' && req.url === '/wake') {
      // Legacy one-shot trigger, kept for anything that hasn't moved to
      // /state yet -- equivalent to pushPanelState('boot').
      showPanel();
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end('ok');
      return;
    }
    if (req.method === 'POST' && req.url === '/chat') {
      toggleChat();
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end('ok');
      return;
    }
    if (req.method === 'POST' && req.url === '/notify/card') {
      readJsonBody(req).then((card) => {
        const shown = card && card.title ? reminders.showInfo(card) : false;
        res.writeHead(200, { 'Content-Type': 'text/plain' });
        res.end(shown ? 'shown' : 'skipped');
      });
      return;
    }
    if (req.method === 'POST' && req.url === '/reminder/card') {
      readJsonBody(req).then((card) => {
        if (card && card.id) reminders.showVoiceReminder(card);
        res.writeHead(200, { 'Content-Type': 'text/plain' });
        res.end('ok');
      });
      return;
    }
    if (req.method === 'POST' && req.url === '/reminder/test') {
      reminders.demo();
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end('ok');
      return;
    }
    if (req.method === 'POST' && req.url === '/goals') {
      goalsWindow.open();
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end('ok');
      return;
    }
    if (req.method === 'POST' && req.url === '/boot') {
      showBoot();
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end('ok');
      return;
    }
    if (req.method === 'POST' && req.url === '/screen') {
      readJsonBody(req).then(({ autostart }) => {
        showScreens({ autostart: !!autostart });
        res.writeHead(200, { 'Content-Type': 'text/plain' });
        res.end('ok');
      });
      return;
    }
    if (req.method === 'POST' && req.url === '/screen/mode') {
      readJsonBody(req).then(({ mode }) => {
        setScreenMode(mode);
        res.writeHead(200, { 'Content-Type': 'text/plain' });
        res.end('ok');
      });
      return;
    }
    if (req.method === 'POST' && req.url === '/screen/close') {
      closeScreens();
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end('ok');
      return;
    }
    if (req.method === 'POST' && req.url === '/state') {
      readJsonBody(req).then(({ stage, text, ...extra }) => {
        pushPanelState(stage || 'boot', text, extra);
        res.writeHead(200, { 'Content-Type': 'text/plain' });
        res.end('ok');
      });
      return;
    }
    res.writeHead(404);
    res.end();
  });
  server.on('error', (err) => console.error('[panel-server]', err.message));
  server.listen(PANEL_SERVER_PORT, '127.0.0.1');
}

// --- Tray ---

function buildTray() {
  tray = new Tray(nativeImage.createEmpty());
  tray.setTitle('🐾');
  tray.setToolTip('JARVIS desktop pet');
  refreshTrayMenu();
}

function refreshTrayMenu() {
  isVoiceWakeEnabled((voiceWakeOn) => {
    if (!tray || tray.isDestroyed()) return;
    const menu = Menu.buildFromTemplate([
      { label: 'JARVIS Pet', enabled: false },
      { type: 'separator' },
      {
        label: 'Pause reactions',
        type: 'checkbox',
        checked: paused,
        click: (item) => {
          paused = item.checked;
          if (!paused) {
            lastMood = null;
            detectFrontmostMood();
          }
        },
      },
      {
        label: 'Reset position',
        click: () => {
          if (!win) return;
          const display = screen.getDisplayNearestPoint(win.getBounds());
          const pos = defaultPositionForDisplay(display);
          win.setPosition(pos.x, pos.y);
          savePositionForDisplay(display.id, pos.x, pos.y);
        },
      },
      { type: 'separator' },
      { label: 'Goals & reminders…', click: () => goalsWindow.open() },
      {
        label: 'Character',
        submenu: Object.entries({ human: 'Little man', cat: 'Tux the cat', panda: 'Bao the panda', robot: 'Bolt the robot', fox: 'Fin the fox' })
          .map(([id, label]) => ({ label, type: 'radio', checked: (store.load().character || 'human') === id,
            click: () => { store.update((d) => { d.character = id; }); sendToPet('pet-character', id); } })),
      },
      { label: 'Test a reminder', click: () => reminders.demo() },
      { type: 'separator' },
      ...(FULL_JARVIS ? [
      { label: 'Chat with JARVIS   ⌘⇧J', click: toggleChat },
      { label: 'Show JARVIS panel', click: showPanel },
      { label: 'Open JARVIS screen', click: () => showScreens() },
      { label: 'Brief me on all screens', click: () => showScreens({ autostart: true }) },
      { label: 'Minimize JARVIS screen   ⌘⇧M', click: () => setScreenMode(screenMode === 'mini' ? 'full' : 'mini') },
      { label: 'Close JARVIS screen   ⌘⇧⎋', click: closeScreens },
      { label: 'Play boot intro', click: showBoot },
      {
        label: 'Voice wake ("wake up Jarvis")',
        type: 'checkbox',
        checked: voiceWakeOn,
        click: (item) => setVoiceWakeEnabled(item.checked),
      },
      {
        label: 'Restart JARVIS',
        click: () => restartService(SERVICE_LABELS.voiceWake, 'JARVIS'),
      },
      ] : []),
      { type: 'separator' },
      { label: 'Quit', click: () => app.quit() },
    ]);
    tray.setContextMenu(menu);
  });
}

// The renderer walks him around by moving the window; it needs the screen
// edges so he turns around instead of walking off.
ipcMain.handle('pet-get-character', () => store.load().character || 'human');

// Name for greetings: the Goals window's "Your name", else JARVIS's config.
function userNameFromConfig() {
  const saved = store.load().settings.userName;
  if (saved) return saved;
  try {
    return JSON.parse(fs.readFileSync(path.join(JARVIS_ROOT, 'config', 'jarvis_sources.json'), 'utf8')).user_name || '';
  } catch {
    return '';
  }
}
ipcMain.handle('pet-get-name', () => userNameFromConfig());

// "pet" = installed with ./install.sh --pet-only: no JARVIS voice app, so its
// menu items (voice wake, JARVIS screen, boot intro...) are hidden.
function installMode() {
  try {
    return fs.readFileSync(path.join(JARVIS_ROOT, 'config', 'mode'), 'utf8').trim();
  } catch {
    return fs.existsSync(path.join(JARVIS_ROOT, 'computer', 'voice', 'venv')) ? 'full' : 'pet';
  }
}
const FULL_JARVIS = installMode() !== 'pet';

function sendToPet(channel, value) {
  if (win && !win.isDestroyed()) win.webContents.send(channel, value);
}

ipcMain.handle('pet-bounds', () => {
  if (!win || win.isDestroyed()) return null;
  const b = win.getBounds();
  return { ...b, area: screen.getDisplayMatching(b).workArea };
});

ipcMain.on('move-window-by', (_event, { dx, dy }) => {
  if (!win || win.isDestroyed() || running) return;
  const b = win.getBounds();
  win.setPosition(Math.round(b.x + dx), Math.round(b.y + dy));
});

app.whenReady().then(() => {
  app.dock?.hide();
  createWindow();
  buildTray();
  startPanelServer();
  if (FULL_JARVIS) globalShortcut.register(CHAT_SHORTCUT, toggleChat);
  goalsWindow.setCharacterListener((c) => { sendToPet('pet-character', c); refreshTrayMenu(); });
  reminders.init({
    petWindow: () => win,
    petMood: () => lastMood,
    paused: () => paused,
    screenBusy: () => screenWins.some((w) => !w.isDestroyed()) || bootWins.some((w) => !w.isDestroyed()),
    react: (kind) => sendToPet('pet-react', kind),
  });
  detectFrontmostMood();
  appPollTimer = setInterval(detectFrontmostMood, APP_POLL_MS);
  windowDisplayPollTimer = setInterval(detectFrontmostWindowDisplay, WINDOW_DISPLAY_POLL_MS);
  idlePollTimer = setInterval(checkIdle, IDLE_POLL_MS);
  minuteTickTimer = setInterval(minuteTick, MINUTE_TICK_MS);
});

app.on('window-all-closed', () => {
  // No dock/taskbar presence; quitting is via the tray menu only, but if the
  // window is ever closed some other way, don't leave an orphaned process.
  app.quit();
});

app.on('will-quit', () => globalShortcut.unregisterAll());
app.on('before-quit', () => { app.isQuitting = true; });

app.on('before-quit', () => {
  clearInterval(appPollTimer);
  clearInterval(windowDisplayPollTimer);
  clearInterval(idlePollTimer);
  clearInterval(minuteTickTimer);
});
