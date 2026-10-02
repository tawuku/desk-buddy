const jarvisToggle = document.getElementById('jarvis-toggle');
const jarvisSwitch = document.getElementById('jarvis-switch');
const jarvisSub = document.getElementById('jarvis-sub');
const petToggle = document.getElementById('pet-toggle');
const petSwitch = document.getElementById('pet-switch');
const jarvisRestartBtn = document.getElementById('jarvis-restart');
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

// --- status ----------------------------------------------------------

let brainState = { remote: false };

function applyStatus(status) {
  const running = !!status.voiceWake;
  jarvisToggle.checked = running;
  jarvisRestartBtn.disabled = !running;
  const brainDown = brainState.remote && !brainState.reachable;
  jarvisSub.textContent = !running ? 'Off' : brainDown ? "Running, but its brain isn't answering" : 'Running -- say "wake up Jarvis"';
  jarvisSub.classList.toggle('warn', running && brainDown);
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
  const turningOn = !jarvisToggle.checked;
  const res = turningOn ? await window.pa.startJarvis() : await window.pa.stopJarvis();
  jarvisSwitch.classList.remove('busy');
  toast(res.ok ? `JARVIS ${turningOn ? 'started' : 'stopped'}` : "JARVIS didn't respond -- see Open logs");
  refreshStatus();
});

jarvisRestartBtn.addEventListener('click', async () => {
  jarvisRestartBtn.disabled = true;
  const res = await window.pa.restart('voiceWake');
  toast(res.ok ? 'JARVIS restarted' : `JARVIS: ${res.error || 'failed'}`);
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

// Piper voices (English only) installed in models/piper -- or on the remote
// brain when the voice runs there. Voice + speed live in config/voice.json; JARVIS picks a change up on its next sentence.
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

// --- brain: on another computer (Brain card) or on this Mac (Performance) --

const BRAIN_PARTS = ['llm', 'stt', 'tts'];
const brainCard = document.getElementById('brain-card');
const threadsCard = document.getElementById('threads-card');
const brainCheckBtn = document.getElementById('brain-check');

async function refreshBrain() {
  brainState = await window.pa.getBrain();
  const modelHere = !brainState.remote || !brainState.services.includes('llm');
  brainCard.hidden = !brainState.remote;
  if (threadsCard.hidden && modelHere) loadThreads();
  threadsCard.hidden = !modelHere;
  if (!brainState.remote) return;

  const { host, services, reachable, health, ms } = brainState;
  document.getElementById('brain-dot').className = `dot ${reachable ? 'up' : 'bad'}`;
  document.getElementById('brain-line').textContent = reachable ? `Connected to ${host} (${ms} ms)` : `Can't reach ${host}`;
  for (const part of BRAIN_PARTS) {
    const there = services.includes(part);
    const dot = document.getElementById(`brain-dot-${part}`);
    const where = document.getElementById(`brain-where-${part}`);
    if (!there) {
      dot.className = 'dot up';
      where.textContent = 'on this Mac';
    } else {
      dot.className = `dot ${!reachable ? 'down' : health[part] ? 'up' : 'bad'}`;
      where.textContent = !reachable ? 'on the other computer' : health[part] ? 'on the other computer' : 'not running there';
    }
  }
  const hint = document.getElementById('brain-hint');
  hint.classList.toggle('warn', !reachable);
  hint.textContent = reachable
    ? 'This Mac listens, plays sound and shows the pet; the rest runs on the other computer. Keep that one on and awake.'
    : "Is the other computer on, awake and on the network? JARVIS can't answer until it is.";
}

brainCheckBtn.addEventListener('click', async () => {
  brainCheckBtn.disabled = true;
  await refreshBrain();
  brainCheckBtn.disabled = false;
  refreshStatus();
});

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
  const res = await window.pa.setThreads(n);
  threadsApplyBtn.disabled = false;
  toast(res.ok ? 'Saved. Applies next time JARVIS starts.' : `Couldn't save: ${res.error || 'failed'}`);
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

refreshBrain().then(refreshStatus);
loadVoiceOptions();
setInterval(refreshStatus, 4000);
setInterval(refreshBrain, 15000);
