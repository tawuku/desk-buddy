// Where JARVIS's heavy parts run. With config/remote.json present (written
// by computer/server/connect.sh) the language model, speech recognition
// and/or the voice live on another computer -- the "brain" -- and this Mac
// only listens, plays audio and draws. Used by the pet (boot intro, panel)
// and by PA. The token never leaves this module.
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const REMOTE_FILE = path.join(__dirname, '..', '..', 'config', 'remote.json');
const SERVICES = ['llm', 'stt', 'tts'];

function config() {
  try {
    const cfg = JSON.parse(fs.readFileSync(REMOTE_FILE, 'utf8'));
    if (!cfg.host || cfg.enabled === false) return null;
    return { host: cfg.host, port: cfg.port || 8090, token: cfg.token || '', services: cfg.services || SERVICES };
  } catch {
    return null;
  }
}

// { remote: false } when everything runs on this Mac; otherwise
// { remote: true, host, services, reachable, health: {llm, stt, tts}, ms }.
function status(timeoutMs = 4000) {
  const cfg = config();
  if (!cfg) return Promise.resolve({ remote: false });
  const base = { remote: true, host: cfg.host, services: cfg.services };
  const started = Date.now();
  return new Promise((resolve) => {
    const down = () => resolve({ ...base, reachable: false, health: {}, ms: null });
    const req = http.get({ host: cfg.host, port: cfg.port, path: '/health', timeout: timeoutMs,
      headers: { Authorization: `Bearer ${cfg.token}` } }, (res) => {
      let raw = '';
      res.on('data', (chunk) => { raw += chunk; });
      res.on('end', () => {
        if (res.statusCode !== 200) return down();
        try {
          resolve({ ...base, reachable: true, health: JSON.parse(raw), ms: Date.now() - started });
        } catch {
          down();
        }
      });
    });
    req.on('timeout', () => { req.destroy(); down(); });
    req.on('error', down);
  });
}

module.exports = { status, SERVICES };
