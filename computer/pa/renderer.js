const SERVICE_ROWS = [
  { key: 'voiceWake', name: 'JARVIS (voice, model, speech)' },
];
const JARVIS_KEYS = SERVICE_ROWS.map((r) => r.key);

const jarvisToggle = document.getElementById('jarvis-toggle');
const jarvisSwitch = document.getElementById('jarvis-switch');
const jarvisSub = document.getElementById('jarvis-sub');
const petToggle = document.getElementById('pet-toggle');
const petSwitch = document.getElementById('pet-switch');
const serviceListEl = document.getElementById('service-list');
const toastEl = document.getElementById('toast');

const voiceSpeakerEl = document.getElementById('voice-speaker');
const voiceSpeedEl = document.getElementById('voice-speed');
const voiceSpeedValueEl = document.getElementById('voice-speed-value');
const voiceApplyBtn = document.getElementById('voice-apply');
const voicePreviewBtn = document.getElementById('voice-preview');
const voiceHintEl = document.getElementById('voice-hint');

const threadsInput = document.getElementById('threads-input');
const threadsApplyBtn = document.getElementById('threads-apply');
const threadsHintEl = document.getElementById('threads-hint');

let toastTimer = null;
function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove('visible'), 3500);
}

// --- service rows --------------------------------------------------

const rowButtons = {};

function buildServiceRows() {
  serviceListEl.innerHTML = '';
  for (const { key, name } of SERVICE_ROWS) {
    const row = document.createElement('div');
    row.className = 'service-row';

    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.id = `dot-${key}`;

    const label = document.createElement('span');
    label.className = 'service-name';
    label.textContent = name;

    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'mini-btn';
    toggleBtn.id = `toggle-${key}`;

    const restartBtn = document.createElement('button');
    restartBtn.className = 'mini-btn';
    restartBtn.textContent = 'Restart';
    restartBtn.addEventListener('click', async () => {
      restartBtn.disabled = true;
      const res = await window.pa.restart(key);
      restartBtn.disabled = false;
      toast(res.ok ? `${name} restarted` : `${name}: ${res.error || 'failed'}`);
      refreshStatus();
    });

    toggleBtn.addEventListener('click', async () => {
      toggleBtn.disabled = true;
      const running = toggleBtn.dataset.running === '1';
      const res = running ? await window.pa.stop(key) : await window.pa.start(key);
      toggleBtn.disabled = false;
      if (!res.ok) toast(`${name}: ${res.error || 'failed'}`);
      refreshStatus();
    });

    row.appendChild(dot);
    row.appendChild(label);
    row.appendChild(toggleBtn);
    row.appendChild(restartBtn);
    serviceListEl.appendChild(row);
    rowButtons[key] = toggleBtn;
  }
}

function applyStatus(status) {
  for (const { key } of SERVICE_ROWS) {
    const running = !!status[key];
    const dot = document.getElementById(`dot-${key}`);
    dot.className = `dot ${running ? 'up' : 'down'}`;
    const btn = rowButtons[key];
    btn.textContent = running ? 'Stop' : 'Start';
    btn.dataset.running = running ? '1' : '0';
  }

  const jarvisUpCount = JARVIS_KEYS.filter((k) => status[k]).length;
  jarvisToggle.checked = jarvisUpCount === JARVIS_KEYS.length;
  jarvisToggle.indeterminate = jarvisUpCount > 0 && jarvisUpCount < JARVIS_KEYS.length;
  jarvisSub.textContent = jarvisUpCount ? 'Running -- say "wake up Jarvis"' : 'Off';

  petToggle.checked = !!status.pet;
}

async function refreshStatus() {
  const status = await window.pa.getStatus();
  applyStatus(status);
}

// --- master toggles --------------------------------------------------

jarvisSwitch.addEventListener('click', async (e) => {
  e.preventDefault();
  if (jarvisSwitch.classList.contains('busy')) return;
  jarvisSwitch.classList.add('busy');
  const turningOn = !jarvisToggle.checked || jarvisToggle.indeterminate;
  const res = turningOn ? await window.pa.startJarvis() : await window.pa.stopJarvis();
  jarvisSwitch.classList.remove('busy');
  toast(res.ok ? `JARVIS ${turningOn ? 'started' : 'stopped'}` : 'JARVIS: some services failed, see status');
  refreshStatus();
});

petSwitch.addEventListener('click', async (e) => {
  e.preventDefault();
  if (petSwitch.classList.contains('busy')) return;
  petSwitch.classList.add('busy');
  const turningOn = !petToggle.checked;
  const res = turningOn ? await window.pa.start('pet') : await window.pa.stop('pet');
  petSwitch.classList.remove('busy');
  toast(res.ok ? `Pet ${turningOn ? 'started' : 'stopped'}` : `Pet: ${res.error || 'failed'}`);
  refreshStatus();
});

// --- voice settings --------------------------------------------------

// Piper voices installed in models/piper (English only). Voice + speed live
// in config/voice.json; JARVIS picks a change up on its next sentence.
const VOICE_LABELS = {
  'en_GB-alan-medium': 'Alan (British)',
  'en_GB-northern_english_male-medium': 'Northern English',
  'en_US-ryan-high': 'Ryan (American)',
};

async function loadVoiceOptions() {
  const { voices, stored, error } = await window.pa.getVoiceOptions();
  voiceSpeakerEl.innerHTML = '';
  for (const v of voices) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = VOICE_LABELS[v] || v;
    voiceSpeakerEl.appendChild(opt);
  }
  if (stored.voice) voiceSpeakerEl.value = stored.voice;
  voiceSpeedEl.value = stored.speed || 1.0;
  voiceSpeedValueEl.textContent = `${(stored.speed || 1.0).toFixed(2)}x`;
  if (error) {
    voiceHintEl.textContent = `Couldn't read voices: ${error}`;
    voiceHintEl.classList.add('warn');
  } else {
    voiceHintEl.textContent = "Preview plays it right away. Save applies to JARVIS's next sentence -- no restart.";
    voiceHintEl.classList.remove('warn');
  }
}

voiceSpeedEl.addEventListener('input', () => {
  voiceSpeedValueEl.textContent = `${Number.parseFloat(voiceSpeedEl.value).toFixed(2)}x`;
});

voicePreviewBtn.addEventListener('click', async () => {
  voicePreviewBtn.disabled = true;
  voicePreviewBtn.textContent = 'Speaking...';
  const res = await window.pa.previewVoice({
    voice: voiceSpeakerEl.value,
    speed: Number.parseFloat(voiceSpeedEl.value),
  });
  voicePreviewBtn.disabled = false;
  voicePreviewBtn.innerHTML = '&#9654; Preview';
  if (!res.ok) toast(`Preview: ${res.error || 'failed'}`);
});

voiceApplyBtn.addEventListener('click', async () => {
  const res = await window.pa.setVoice({
    voice: voiceSpeakerEl.value,
    speed: Number.parseFloat(voiceSpeedEl.value),
  });
  toast(res.ok ? 'Voice saved' : `Voice: ${res.error || 'failed'}`);
});

// --- performance / threads --------------------------------------------

async function loadThreads() {
  const { threads, cpuCount } = await window.pa.getThreads();
  threadsInput.value = threads || '';
  threadsInput.max = cpuCount;
  threadsHintEl.textContent = `This machine has ${cpuCount} CPU threads. Leave 1-2 free for everything else -- JARVIS ran into subprocess timeouts and reliability issues when the model server used every core.`;
}

threadsApplyBtn.addEventListener('click', async () => {
  const n = Number.parseInt(threadsInput.value, 10);
  if (!Number.isFinite(n) || n < 1) {
    toast('Enter a valid thread count');
    return;
  }
  threadsApplyBtn.disabled = true;
  await window.pa.setThreads(n);
  threadsApplyBtn.disabled = false;
  toast('Saved. Applies next time JARVIS starts.');
});

// --- advanced --------------------------------------------------

document.getElementById('open-logs').addEventListener('click', () => window.pa.openLogs());
document.getElementById('open-chat').addEventListener('click', async () => {
  const { ok } = await window.pa.openChat();
  if (!ok) toast('Turn on the desktop pet first -- it opens the chat (⌘⇧J).');
});
document.getElementById('open-goals').addEventListener('click', async () => {
  const { ok } = await window.pa.openGoals();
  if (!ok) toast('Turn on the desktop pet first -- it keeps your goals and reminders.');
});
document.getElementById('open-screen').addEventListener('click', async () => {
  const { ok } = await window.pa.openScreen(true);
  if (!ok) toast('Turn on JARVIS and the desktop pet first -- the pet shows the screen.');
});

// --- init --------------------------------------------------

buildServiceRows();
refreshStatus();
loadVoiceOptions();
loadThreads();
setInterval(refreshStatus, 4000);
