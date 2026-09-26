console.log('[panel] renderer loaded, bridge present:', typeof window.panel !== 'undefined');

const body = document.body;
const card = document.getElementById('card');
const timeEl = document.getElementById('time');
const tasksEl = document.getElementById('tasks');
const buildLineEl = document.getElementById('build-line');
const statusRowEl = document.getElementById('status-row');
const stageLineEl = document.getElementById('stage-line');
const heardEl = document.getElementById('heard');
const replyEl = document.getElementById('reply');
const stepsEl = document.getElementById('steps');

const FADE_MS = 250;
let dismissTimer = null;
let dismissed = false;
let thinkingTicker = null;
let typewriterTimer = null;

// Stage -> status-line label. "thinking" gets a live elapsed-time suffix
// appended separately (see startThinkingClock) since a cold-model reply can
// legitimately take up to ~30 minutes on this hardware -- see root
// DEVELOPMENT_LOG.md's "Performance on this hardware".
const STAGE_LABEL = {
  boot: 'systems online',
  listening: 'listening...',
  heard: 'got it -- one sec',
  thinking: 'processing',
  reply: 'reply received',
  error: 'something went wrong',
  empty: "didn't catch that",
  speaking: 'speaking',
  sleep: 'standing by -- say "hey Jarvis"',
};

// How long to leave the panel up once a stage is "final" (nothing more is
// coming). Stages not listed here (boot/listening/heard/thinking) never
// auto-hide on their own -- the conversation is still in progress.
function holdMsForStage(stage, textLength) {
  if (stage === 'reply') return Math.max(9000, Math.min(45000, textLength * 55));
  if (stage === 'error') return 12000;
  if (stage === 'empty') return 5000;
  if (stage === 'sleep') return 3000;
  return null;
}

function dismissNow() {
  if (dismissed) return;
  dismissed = true;
  clearTimeout(dismissTimer);
  clearInterval(thinkingTicker);
  clearInterval(typewriterTimer);
  card.classList.remove('visible');
  setTimeout(() => window.panel.dismiss(), FADE_MS);
}

function renderTasks(tasks, moreCount) {
  tasksEl.innerHTML = '';
  if (!tasks.length) {
    const li = document.createElement('li');
    li.className = 'task-empty';
    li.textContent = 'Nothing open -- nice.';
    li.style.setProperty('list-style', 'none');
    tasksEl.appendChild(li);
    return;
  }
  for (const task of tasks) {
    const li = document.createElement('li');
    li.textContent = task;
    tasksEl.appendChild(li);
  }
  if (moreCount > 0) {
    const li = document.createElement('li');
    li.className = 'task-more';
    li.textContent = `+${moreCount} more`;
    tasksEl.appendChild(li);
  }
}

function renderStatus(services) {
  statusRowEl.innerHTML = '';
  for (const [name, up] of Object.entries(services)) {
    const item = document.createElement('div');
    item.className = 'status-item';
    const dot = document.createElement('span');
    dot.className = `dot ${up === null ? '' : up ? 'up' : 'down'}`;
    const label = document.createElement('span');
    label.textContent = name;
    item.appendChild(dot);
    item.appendChild(label);
    statusRowEl.appendChild(item);
  }
}

function formatElapsed(ms) {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function startThinkingClock() {
  clearInterval(thinkingTicker);
  const startedAt = Date.now();
  const tick = () => {
    stageLineEl.textContent = `${STAGE_LABEL.thinking} · ${formatElapsed(Date.now() - startedAt)}`;
  };
  tick();
  thinkingTicker = setInterval(tick, 1000);
}

function typeText(el, text) {
  clearInterval(typewriterTimer);
  el.textContent = '';
  if (!text) return;
  // Fast reveal, capped so a long reply doesn't take forever to finish
  // appearing -- this is a HUD flourish, not a real-time transcript.
  const totalMs = 900;
  const stepMs = Math.max(6, Math.floor(totalMs / text.length));
  let i = 0;
  const cursor = document.createElement('span');
  cursor.className = 'cursor';
  typewriterTimer = setInterval(() => {
    i += 1;
    el.textContent = text.slice(0, i);
    el.appendChild(cursor);
    if (i >= text.length) clearInterval(typewriterTimer);
  }, stepMs);
}

function applyStage(stage, text) {
  clearInterval(thinkingTicker);
  body.dataset.stage = stage;

  if (stage === 'listening') {
    // Mid-conversation (a reply is already on screen): keep it visible but
    // dimmed while JARVIS waits for a follow-up question.
    stageLineEl.textContent = replyEl.textContent ? 'listening -- ask a follow-up' : STAGE_LABEL.listening;
    replyEl.classList.add('dim');
  } else if (stage === 'heard') {
    stageLineEl.textContent = STAGE_LABEL.heard;
    heardEl.textContent = text || '';
    replyEl.textContent = '';
    replyEl.classList.remove('dim');
    stepsEl.innerHTML = '';
    body.classList.remove('has-steps');
  } else if (stage === 'thinking') {
    heardEl.textContent = text || heardEl.textContent;
    replyEl.textContent = '';
    startThinkingClock();
  } else if (stage === 'reply' || stage === 'speaking') {
    stageLineEl.textContent = STAGE_LABEL[stage];
    replyEl.classList.remove('dim');
    typeText(replyEl, text || '');
  } else if (stage === 'sleep') {
    stageLineEl.textContent = STAGE_LABEL.sleep;
  } else if (stage === 'error' || stage === 'empty') {
    stageLineEl.textContent = text || STAGE_LABEL[stage];
    replyEl.textContent = '';
  } else {
    stageLineEl.textContent = STAGE_LABEL.boot;
  }

  const hold = holdMsForStage(stage, (text || '').length);
  clearTimeout(dismissTimer);
  if (hold !== null) dismissTimer = setTimeout(dismissNow, hold);
}

window.panel.onData((data) => {
  console.log('[panel] data received:', JSON.stringify(data));
  dismissed = false;
  timeEl.textContent = data.time;
  renderTasks(data.tasks, data.moreCount);
  buildLineEl.textContent = data.buildLine;
  renderStatus(data.services);
  applyStage('boot');

  requestAnimationFrame(() => {
    requestAnimationFrame(() => card.classList.add('visible'));
  });

  // A manual tray-triggered preview (no voice pipeline behind it) still
  // wants the old "show, then go away" behavior.
  if (data.autoHideMs) {
    clearTimeout(dismissTimer);
    dismissTimer = setTimeout(dismissNow, data.autoHideMs);
  }
});

// One row per data source JARVIS is fetching ("Weather · Berlin  ✓ 13°C").
// Same label = same row, updated in place as it goes running -> done/failed.
const STEP_ICON = { running: '◌', done: '✓', failed: '✕' };

function upsertStep(label, status, detail) {
  body.classList.add('has-steps');
  let li = [...stepsEl.children].find((el) => el.dataset.label === label);
  if (!li) {
    li = document.createElement('li');
    li.dataset.label = label;
    li.innerHTML = '<span class="icon"></span><span class="label"></span><span class="detail"></span>';
    stepsEl.appendChild(li);
  }
  li.className = status || 'running';
  li.querySelector('.icon').textContent = STEP_ICON[status] || STEP_ICON.running;
  li.querySelector('.label').textContent = label;
  li.querySelector('.detail').textContent = detail || '';
}

window.panel.onState((data) => {
  console.log('[panel] state:', JSON.stringify(data));
  dismissed = false;
  if (timeEl.textContent === '') timeEl.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  card.classList.add('visible');
  if (data.stage === 'step') {
    upsertStep(data.text, data.status, data.detail);
    return;
  }
  applyStage(data.stage, data.text);
});

// Keep the (transparent) window exactly as tall as the card, so the empty
// part never sits invisibly over whatever is below it on screen.
new ResizeObserver(() => {
  window.panel.resize(card.getBoundingClientRect().height + 24);
}).observe(card);

document.body.addEventListener('click', dismissNow);
