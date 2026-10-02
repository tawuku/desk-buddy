const { app, BrowserWindow, ipcMain, Menu, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { exec, execFile } = require('node:child_process');

const JARVIS_ROOT = path.join(__dirname, '..', '..');
const LAUNCH_AGENTS_DIR = path.join(os.homedir(), 'Library', 'LaunchAgents');
const UID = process.getuid();
const ENV_FILE = path.join(JARVIS_ROOT, 'config', '.env.local_model');
const VOICE_PY = path.join(JARVIS_ROOT, 'computer', 'voice', 'venv', 'bin', 'python3');
const TTS_PY = path.join(JARVIS_ROOT, 'computer', 'voice', 'tts.py');

// JARVIS Lite: one app (computer/voice/wake_listener.py) runs everything --
// wake word, speech recognition, the local model and the Piper voice -- as
// the com.jarvis.voice-wake LaunchAgent. The pet (HUD panel) is separate.
const SERVICES = {
  voiceWake: { label: 'com.jarvis.voice-wake', name: 'JARVIS' },
  pet: { label: 'com.jarvis.pet', name: 'Desktop pet (HUD)' },
};
const JARVIS_GROUP = ['voiceWake'];

function plistPath(label) {
  return path.join(LAUNCH_AGENTS_DIR, `${label}.plist`);
}

// --- launchd control ---------------------------------------------------
// Nothing starts at login (RunAtLoad=false and `launchctl disable`d while
// off -- see stopService); PA is the on/off switch. PA starts/stops them via bootstrap/bootout (the modern load/unload pair) plus an
// explicit kickstart to actually run a job that bootstrap alone won't
// start (RunAtLoad is off). bootout fully removes a KeepAlive=true job
// from launchd so it can't relaunch itself -- a plain `stop`/`kill` would
// just get restarted immediately for gateway/llama/tts/voice-wake.

function isLoaded(label) {
  return new Promise((resolve) => {
    exec(`launchctl print gui/${UID}/${label}`, { timeout: 4000 }, (err) => resolve(!err));
  });
}

function isRunning(label) {
  return new Promise((resolve) => {
    // `launchctl list <label>` (a single exact label) is a different
    // format from the bare `launchctl list` table: a legacy plist-style
    // dict dump. It carries a `"PID" = <n>;` entry only while the job is
    // actually running; a loaded-but-stopped job has no PID key at all,
    // and an unloaded job makes the command exit non-zero.
    exec(`launchctl list ${label}`, { timeout: 4000 }, (err, stdout) => {
      if (err) return resolve(false);
      resolve(/"PID"\s*=\s*\d+;/.test(stdout));
    });
  });
}

async function startService(key) {
  const svc = SERVICES[key];
  if (!svc) return { ok: false, error: `unknown service ${key}` };
  // Services are `launchctl disable`d while off (so KeepAlive jobs don't
  // come back at login); re-enable before loading.
  await new Promise((resolve) => {
    exec(`launchctl enable gui/${UID}/${svc.label}`, { timeout: 4000 }, () => resolve());
  });
  const loaded = await isLoaded(svc.label);
  if (!loaded) {
    const bootstrapped = await new Promise((resolve) => {
      exec(`launchctl bootstrap gui/${UID} "${plistPath(svc.label)}"`, { timeout: 6000 }, (err) => resolve(!err));
    });
    if (!bootstrapped) return { ok: false, error: `could not load ${svc.label}` };
  }
  return new Promise((resolve) => {
    exec(`launchctl kickstart -k gui/${UID}/${svc.label}`, { timeout: 6000 }, (err) => {
      resolve(err ? { ok: false, error: err.message } : { ok: true });
    });
  });
}

async function stopService(key) {
  const svc = SERVICES[key];
  if (!svc) return { ok: false, error: `unknown service ${key}` };
  // Disable too, so "off" survives a restart: launchd loads every plist in
  // ~/Library/LaunchAgents at login and starts KeepAlive jobs regardless
  // of RunAtLoad.
  await new Promise((resolve) => {
    exec(`launchctl disable gui/${UID}/${svc.label}`, { timeout: 4000 }, () => resolve());
  });
  const loaded = await isLoaded(svc.label);
  if (!loaded) return { ok: true }; // already off
  return new Promise((resolve) => {
    exec(`launchctl bootout gui/${UID}/${svc.label}`, { timeout: 6000 }, (err) => {
      resolve(err ? { ok: false, error: err.message } : { ok: true });
    });
  });
}

async function restartService(key) {
  const svc = SERVICES[key];
  if (!svc) return { ok: false, error: `unknown service ${key}` };
  const loaded = await isLoaded(svc.label);
  if (!loaded) return startService(key);
  return new Promise((resolve) => {
    exec(`launchctl kickstart -k gui/${UID}/${svc.label}`, { timeout: 6000 }, (err) => {
      resolve(err ? { ok: false, error: err.message } : { ok: true });
    });
  });
}

async function getStatus() {
  const entries = await Promise.all(
    Object.keys(SERVICES).map(async (key) => [key, await isRunning(SERVICES[key].label)]),
  );
  return Object.fromEntries(entries);
}

// --- voice (Piper, via computer/voice/tts.py) ---------------------------
// tts.py owns config/voice.json and knows which voices are installed
// (models/piper/*.onnx); PA just calls it. Changes apply to JARVIS's next
// sentence -- no restart.

function runTts(args, timeoutMs = 30000) {
  return new Promise((resolve) => {
    execFile(VOICE_PY, [TTS_PY, ...args], { timeout: timeoutMs }, (err, stdout, stderr) => {
      resolve({ err, stdout, stderr });
    });
  });
}

async function getVoiceOptions() {
  const { err, stdout } = await runTts(['--list']);
  if (err) return { voices: [], stored: { voice: null, speed: 1.0 }, error: err.message };
  const { voices, config } = JSON.parse(stdout);
  return { voices, stored: config };
}

function setVoice({ voice, speed }) {
  const file = path.join(JARVIS_ROOT, 'config', 'voice.json');
  let cfg = {};
  try { cfg = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { /* new file */ }
  if (voice) cfg.voice = voice;
  if (speed) cfg.speed = Math.round(speed * 100) / 100;
  fs.writeFileSync(file, `${JSON.stringify(cfg, null, 2)}\n`);
  return { ok: true };
}

const PREVIEW_TEXT = "Hey there, what's up? Right now it's 14 degrees and cloudy outside.";

async function previewVoice({ voice, speed }) {
  const { err, stderr } = await runTts(['--voice', voice, '--speed', String(speed), PREVIEW_TEXT]);
  return err ? { ok: false, error: (stderr || err.message).slice(0, 200) } : { ok: true };
}

// --- performance: llama.cpp thread count --------------------------------

function readThreadCount() {
  try {
    const text = fs.readFileSync(ENV_FILE, 'utf8');
    const match = text.match(/^JARVIS_MODEL_THREADS=(\d+)/m);
    return match ? Number.parseInt(match[1], 10) : null;
  } catch {
    return null;
  }
}

function writeThreadCount(n) {
  const text = fs.readFileSync(ENV_FILE, 'utf8');
  const next = text.replace(/^JARVIS_MODEL_THREADS=\d+/m, `JARVIS_MODEL_THREADS=${n}`);
  fs.writeFileSync(ENV_FILE, next);
}

// --- window ---------------------------------------------------------

let win = null;

function createWindow() {
  win = new BrowserWindow({
    width: 420,
    height: 680,
    resizable: false,
    title: 'PA',
    backgroundColor: '#0b0e11',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, 'index.html'));
}

// --- IPC ---------------------------------------------------------------

ipcMain.handle('pa:get-status', () => getStatus());

ipcMain.handle('pa:start', (_e, key) => startService(key));
ipcMain.handle('pa:stop', (_e, key) => stopService(key));
ipcMain.handle('pa:restart', (_e, key) => restartService(key));

ipcMain.handle('pa:start-jarvis', async () => {
  const results = await Promise.all(JARVIS_GROUP.map(startService));
  return { ok: results.every((r) => r.ok), results };
});
ipcMain.handle('pa:stop-jarvis', async () => {
  const results = await Promise.all(JARVIS_GROUP.map(stopService));
  return { ok: results.every((r) => r.ok), results };
});

ipcMain.handle('pa:get-voice-options', () => getVoiceOptions());
ipcMain.handle('pa:set-voice', (_e, opts) => setVoice(opts));

ipcMain.handle('pa:preview-voice', (_e, opts) => previewVoice(opts));

ipcMain.handle('pa:get-threads', () => ({ threads: readThreadCount(), cpuCount: os.cpus().length }));
ipcMain.handle('pa:set-threads', (_e, n) => {
  writeThreadCount(n);
  return { ok: true };
});

// The pet app shows the JARVIS screen on every display (computer/pet/main.js,
// POST /screen on its loopback port 8092).
function postToPet(urlPath, body) {
  return new Promise((resolve) => {
    const req = require('node:http').request(
      { host: '127.0.0.1', port: 8092, path: urlPath, method: 'POST', timeout: 2000, headers: { 'Content-Type': 'application/json' } },
      (res) => { res.resume(); resolve({ ok: res.statusCode === 200 }); },
    );
    req.on('timeout', () => { req.destroy(); resolve({ ok: false }); });
    req.on('error', () => resolve({ ok: false }));
    req.end(JSON.stringify(body || {}));
  });
}
ipcMain.handle('pa:open-goals', () => postToPet('/goals'));
ipcMain.handle('pa:open-chat', () => postToPet('/chat'));

ipcMain.handle('pa:open-screen', (_e, autostart) => new Promise((resolve) => {
  const req = require('node:http').request(
    { host: '127.0.0.1', port: 8092, path: '/screen', method: 'POST', timeout: 2000, headers: { 'Content-Type': 'application/json' } },
    (res) => { res.resume(); resolve({ ok: res.statusCode === 200 }); },
  );
  req.on('timeout', () => { req.destroy(); resolve({ ok: false }); });
  req.on('error', () => resolve({ ok: false }));
  req.end(JSON.stringify({ autostart: !!autostart }));
}));

ipcMain.handle('pa:open-logs', () => {
  shell.openPath(path.join(JARVIS_ROOT, 'logs'));
  return { ok: true };
});

app.whenReady().then(() => {
  Menu.setApplicationMenu(null);
  // PA runs on the pet's shared Electron binary, so without this the Dock
  // shows Electron's default icon instead of PA's (PA.app only sets Finder's).
  if (app.dock) app.dock.setIcon(path.join(__dirname, 'icon.png'));
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  // PA is a control panel, not a background service -- quitting it never
  // touches whatever it started. Services keep running until told to stop.
  if (process.platform !== 'darwin') app.quit();
});
