// The little man's behaviour: a chain of short "acts" (walk somewhere, wave,
// type on a laptop, dance...), picked at random -- weighted by what you're
// doing on the Mac (the mood main.js sends: coding / browsing / listening /
// vibing / idle / asleep / running). Each act is a pose over time for
// sprite.js's drawMan; switching acts blends the old pose into the new one.
// He walks for real by moving his window along the screen, talks in speech
// bubbles now and then, and reacts to clicks and being dragged around.
(() => {
  const canvas = document.getElementById('man');
  const ctx = canvas.getContext('2d');
  const bubbleEl = document.getElementById('bubble');
  const pet = window.pet || {};

  console.log('[pet] renderer loaded, pet bridge present:', !!window.pet);

  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const chance = (p) => Math.random() < p;
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const bump = (t) => Math.sin(clamp01(t) * Math.PI); // 0 -> 1 -> 0
  const ease = (t) => { t = clamp01(t); return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; };

  // --- mood from main.js -------------------------------------------------------
  let mood = 'idle';
  let moodSince = performance.now();
  if (pet.onMood) {
    pet.onMood((m) => {
      if (m === mood) return;
      console.log('[pet] mood ->', m);
      mood = m;
      moodSince = performance.now();
      if (!act || !act.locked) nextAct();
    });
  }
  const streakMin = () => (performance.now() - moodSince) / 60000;

  // --- character (picked in the Goals window / tray) ---------------------------
  let character = 'human';
  if (pet.getCharacter) pet.getCharacter().then((c) => { if (c) character = c; }).catch(() => {});
  if (pet.onCharacter) pet.onCharacter((c) => { character = c; startAct('hello', { ...IDLE.wave, say: ['New look! How do I look?', 'Ta-da, it’s me!'], p: 1 }); });

  // After the boot intro: a big wave with the greeting.
  if (pet.onGreet) pet.onGreet((text) => startAct('greet', { ...IDLE.wave, d: 3200, say: [text], p: 1 }, true));

  // Reminder cards (reminders.js): face you while one is up, celebrate a "done".
  if (pet.onReact) {
    pet.onReact((kind) => {
      if (kind === 'attention') startAct('attention', ATTENTION, true);
      else if (kind === 'attention-end') { if (act && act.name === 'attention') nextAct(); }
      else if (kind === 'celebrate') startAct('cheer', { ...IDLE.jumpJoy, say: ['Yes!! 🎉', 'Proud of you!', 'Look at you go!'], p: 0.9 }, true);
    });
  }
  const lateNight = () => { const h = new Date().getHours(); return h >= 23 || h < 5; };

  // --- blink ---------------------------------------------------------------------
  let nextBlinkAt = performance.now() + rand(2000, 5000);
  let blinkAt = null;
  function blink(now) {
    if (blinkAt === null && now >= nextBlinkAt) blinkAt = now;
    if (blinkAt === null) return 1;
    const t = (now - blinkAt) / 180;
    if (t >= 1) { blinkAt = null; nextBlinkAt = now + rand(2200, 6000); return 1; }
    return Math.abs(1 - 2 * t);
  }

  // --- speech bubble -------------------------------------------------------------
  let bubbleTimer = null;
  function say(text, ms) {
    bubbleEl.textContent = text;
    bubbleEl.classList.add('show');
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => bubbleEl.classList.remove('show'), ms || Math.max(1800, text.length * 75));
  }
  function maybeSay(lines, p = 0.35) { if (lines && lines.length && chance(p)) say(pick(lines)); }

  // --- walking the window ----------------------------------------------------------
  let facing = 1;
  let moveAcc = 0, moveAccY = 0;
  function moveBy(dx, dy = 0) {
    moveAcc += dx;
    moveAccY += dy;
    const n = Math.trunc(moveAcc), m = Math.trunc(moveAccY);
    if (n !== 0 || m !== 0) {
      moveAcc -= n;
      moveAccY -= m;
      if (pet.moveWindowBy) pet.moveWindowBy(n, m);
    }
  }
  async function bounds() {
    try { return pet.getBounds ? await pet.getBounds() : null; } catch { return null; }
  }
  // Pick somewhere to walk to -- any direction -- that stays on screen.
  async function planWalk(a, reach, vertical = true) {
    const b = await bounds();
    let dx = rand(40, reach) * (chance(0.5) ? 1 : -1);
    let dy = vertical && chance(0.55) ? rand(30, reach * 0.6) * (chance(0.5) ? 1 : -1) : 0;
    if (b) {
      const minX = b.area.x, maxX = b.area.x + b.area.width - b.width;
      const minY = b.area.y, maxY = b.area.y + b.area.height - b.height;
      if (b.x + dx < minX || b.x + dx > maxX) dx = -dx;
      if (b.y + dy < minY || b.y + dy > maxY) dy = -dy;
      dx = Math.max(minX - b.x, Math.min(maxX - b.x, dx));
      dy = Math.max(minY - b.y, Math.min(maxY - b.y, dy));
    }
    a.dx = dx;
    a.dy = dy;
    a.dist = Math.hypot(dx, dy) * (dx < 0 ? -1 : 1);
    a.moved = 0;
    a.ready = true;
  }

  // Which way he shows while walking a (dx, dy) path.
  function walkView(dx, dy) {
    if (Math.abs(dy) > Math.abs(dx) * 1.2) return dy > 0 ? 'front' : 'back';
    return 'side';
  }

  // --- poses -------------------------------------------------------------------------
  const base = (now, o = {}) => ({
    facing,
    eyeOpen: blink(now),
    pupil: { x: Math.sin(now / 2300) * 0.4, y: 0 },
    armF: { s: 0.08 + Math.sin(now / 1100) * 0.04, e: 0.15 },
    armB: { s: -0.08 - Math.sin(now / 1100) * 0.04, e: 0.15 },
    legF: { h: 0, k: 0 },
    legB: { h: 0, k: 0 },
    ...o,
  });
  const SIT = { legF: { h: 1.45, k: 0.05 }, legB: { h: 1.35, k: 0.15 } };
  function walkLimbs(ph, amp = 0.55, run = false) {
    const s = Math.sin(ph);
    return {
      legF: { h: amp * s, k: -Math.max(0, Math.cos(ph)) * (run ? 1.4 : 0.8) },
      legB: { h: -amp * s, k: -Math.max(0, -Math.cos(ph)) * (run ? 1.4 : 0.8) },
      armF: { s: -amp * 0.9 * s, e: run ? 1.5 : 0.35 },
      armB: { s: amp * 0.9 * s, e: run ? 1.5 : 0.35 },
    };
  }
  const sitDesk = (now, o = {}) => base(now, {
    ...SIT,
    props: { laptop: true },
    pupil: { x: 0.7, y: 0.6 },
    headTilt: 0.08,
    fx: { typing: now / 200 },
    ...o,
  });
  const withPhone = (now, o = {}) => base(now, {
    props: { phone: true },
    armF: { s: 1.05, e: 1.55 + Math.sin(now / 260) * 0.05 },
    pupil: { x: 0.4, y: 1 },
    headTilt: 0.18,
    ...o,
  });
  const headset = (now, o = {}) => base(now, { props: { headset: true }, ...o });
  const phones = (now, o = {}) => base(now, { props: { headphones: true }, ...o });

  // --- acts ------------------------------------------------------------------------
  // { w: weight, d: ms or [min,max] or Infinity, say: [lines], p: say chance,
  //   start(a), pose(t, a, now, dt) }
  const walkAct = (w, speed, reach, extra = {}) => ({
    w, d: 14000, p: 0.25,
    start: (a) => planWalk(a, reach, !extra.backwards),
    pose(t, a, now, dt) {
      if (!a.ready) return base(now);
      const total = Math.abs(a.dist) || 1;
      if (Math.abs(a.moved) >= total) { a.done = true; return base(now); }
      const dir = Math.sign(a.dx) || 1;
      facing = extra.backwards ? -dir : dir;
      const step = speed * dt / 1000;
      moveBy((a.dx / total) * step, (a.dy / total) * step);
      a.moved += step;
      a.ph = (a.ph || 0) + step * (extra.backwards ? 0.22 : 0.16);
      a.view = extra.backwards ? 'side' : walkView(a.dx, a.dy);
      if (extra.backwards) {
        const s = Math.sin(a.ph);
        return base(now, { legF: { h: 0.12 * s, k: -0.7 * Math.max(0, s) }, legB: { h: -0.12 * s, k: -0.7 * Math.max(0, -s) },
          armF: { s: 0.3, e: 1.2 }, armB: { s: -0.2, e: 0.4 }, headTurn: 0.8, mouth: 'o' });
      }
      const pose = base(now, { ...walkLimbs(a.ph), headTurn: 0.55, view: a.view });
      return extra.phone ? { ...pose, props: { phone: true }, armF: { s: 1.05, e: 1.55 }, pupil: { x: 0.4, y: 1 }, headTilt: 0.18 } : pose;
    },
    ...extra.def,
  });

  const IDLE = {
    stand: { w: 6, d: [3000, 6000], start: (a) => { a.front = chance(0.6); }, pose: (t, a, now) => base(now, {
      view: a.front ? 'front' : 'side', headTurn: 0.35 + Math.sin(now / 1400) * 0.45, pupil: { x: Math.sin(now / 1400) * 0.8, y: 0 } }) },
    turnAround: { w: 1, d: 2600, say: ['Just checking behind me.', 'Did someone say snacks?'], p: 0.25, pose(t, a, now) {
      const v = t < 800 ? 'front' : t < 1800 ? 'back' : 'front';
      return base(now, { view: v, headTilt: v === 'back' ? Math.sin(now / 300) * 0.1 : 0 });
    } },
    wander: walkAct(5, 38, 320, { def: { say: ["I'm walking here!", 'Just a little stroll.', 'Patrol time.', 'Stretching my legs.'] } }),
    jog: walkAct(0.8, 110, 420, { def: { say: ['Cardio!', 'Gotta go fast!'], p: 0.6,
      pose(t, a, now, dt) {
        if (!a.ready) return base(now);
        const total = Math.abs(a.dist) || 1;
        if (a.moved >= total) { a.done = true; return base(now); }
        facing = Math.sign(a.dx) || 1;
        const step = 110 * dt / 1000;
        moveBy((a.dx / total) * step, (a.dy / total) * step); a.moved += step; a.ph = (a.ph || 0) + step * 0.11;
        const view = walkView(a.dx, a.dy);
        return base(now, { ...walkLimbs(a.ph, 0.9, true), lean: view === 'side' ? 0.22 : 0, view, mouth: 'open', fx: view === 'side' ? { speed: now / 1000 } : {} });
      } } }),
    moonwalk: walkAct(0.5, 30, 160, { backwards: true, def: { say: ['Hee-hee!', 'Moonwalk!', 'Smooth criminal.'], p: 0.8 } }),
    wave: { w: 2, d: 2200, say: ['Hey there!', 'Hi!', 'Yo!', 'Hello, human!'], p: 0.7,
      pose: (t, a, now) => base(now, { view: 'front', armF: { s: 2.2, e: -0.5 + Math.sin(now / 120) * 0.45 }, eyes: 'happy', mouth: 'grin', headTurn: 0.6 }) },
    stretch: { w: 2, d: 3200, say: ['Aaaah…', '*stretch*'], p: 0.4, pose(t, a, now) {
      const k = bump(t / 3200);
      return base(now, { view: 'front', armF: { s: 2.45 * k, e: -0.3 * k }, armB: { s: -2.45 * k, e: 0.3 * k }, y: 1.6 * k,
        eyes: k > 0.5 ? 'closed' : 'open', mouth: k > 0.6 ? 'yawn' : 'o' });
    } },
    yawn: { w: 1, d: 2400, say: ['*yawn*', 'So sleepy…'], p: 0.5, pose: (t, a, now) => base(now, {
      armF: { s: 2.25, e: 2.05 }, mouth: 'yawn', eyes: 'closed', headTilt: -0.1 }) },
    checkWatch: { w: 1.5, d: 2600, say: ['Is it lunch yet?', 'Tick tock…', 'Time flies!'], p: 0.6,
      pose: (t, a, now) => base(now, { armF: { s: 1.35, e: 1.5 }, headTilt: 0.15, pupil: { x: 0.6, y: 0.8 }, brows: 'raised', mouth: 'flat' }) },
    sitRest: { w: 2, d: [6000, 10000], say: ['Just chilling.', '♪ la la la ♪'], p: 0.3,
      pose: (t, a, now) => base(now, { ...SIT, armF: { s: -0.7, e: 0.2 }, armB: { s: -0.8, e: 0.2 }, lean: -0.15,
        eyes: 'sleepy', mouth: 'whistle', fx: { notes: now / 1000 } }) },
    jumpJoy: { w: 1, d: 1800, say: ['Wheee!', 'Yay!', 'Woohoo!'], p: 0.6, pose(t, a, now) {
      const air = Math.abs(Math.sin((t / 600) * Math.PI));
      return base(now, { view: 'front', y: air * 9, armF: { s: 2.4, e: -0.4 + Math.sin(now / 90) * 0.3 }, armB: { s: -2.4, e: 0.4 - Math.sin(now / 90) * 0.3 },
        legF: { h: 0.6 * air, k: -1.2 * air }, legB: { h: 0.3 * air, k: -1.0 * air }, eyes: 'happy', mouth: 'laugh', squash: air < 0.1 ? 0.1 : 0 });
    } },
    squats: { w: 0.8, d: 4200, say: ['Leg day! 1… 2… 3…', 'Gotta stay fit.'], p: 0.7, pose(t, a, now) {
      const c = (1 - Math.cos((t / 1050) * Math.PI * 2)) / 2;
      return base(now, { legF: { h: 0.95 * c, k: -1.9 * c }, legB: { h: 0.85 * c, k: -1.8 * c }, armF: { s: 1.5 * c, e: 0 }, armB: { s: 1.4 * c, e: 0 },
        lean: 0.3 * c, mouth: c > 0.5 ? 'o' : 'flat', fx: t > 2500 ? { sweat: 1 } : {} });
    } },
    trip: { w: 0.5, d: 3800, pose(t, a, now, dt) {
      if (t < 600) { moveBy(38 * dt / 1000 * facing); return base(now, { ...walkLimbs(t / 90), headTurn: 0.55 }); }
      if (t < 1100) {
        const k = ease((t - 600) / 500);
        return base(now, { tilt: 1.1 * k, x: -8 * k, armF: { s: 2.4, e: Math.sin(now / 60) }, armB: { s: -2.4, e: -Math.sin(now / 60) },
          legB: { h: -0.8 * k, k: 0 }, eyes: 'wide', brows: 'up', mouth: 'open' });
      }
      if (t < 2300) {
        if (!a.said) { a.said = true; say(pick(['Oops!', 'I meant to do that.', 'Ow…', 'Nobody saw that.'])); }
        return base(now, { tilt: 1.1, x: -8, armF: { s: 2.5, e: 0.2 }, armB: { s: -2.5, e: -0.2 }, legB: { h: -0.8, k: 0 },
          eyes: 'dizzy', mouth: 'frown', fx: { stars: now / 1000 } });
      }
      const k = ease((t - 2300) / 900);
      return base(now, { tilt: 1.1 * (1 - k), x: -8 * (1 - k), eyes: k < 1 ? 'dizzy' : 'open', mouth: 'flat', fx: { stars: now / 1000 },
        armF: { s: 0.8 * (1 - k), e: 0.6 }, armB: { s: 0.6, e: 0.5 } });
    } },
    thinking: { w: 1.2, d: 3800, pose(t, a, now) {
      const aha = t > 2800;
      if (aha && !a.said) { a.said = true; maybeSay(['Aha!', 'Got it!', 'Eureka!'], 0.6); }
      return base(now, { armF: aha ? { s: 2.2, e: -0.7 } : { s: 2.05, e: 2.35 }, armB: { s: 0.6, e: 1.6 }, headTilt: aha ? 0 : 0.12,
        pupil: { x: 0.5, y: -0.8 }, brows: aha ? 'up' : 'raised', eyes: aha ? 'happy' : 'open', mouth: aha ? 'grin' : 'flat',
        fx: aha ? { bulb: 1 } : { question: clamp01(t / 400) } });
    } },
    tada: { w: 0.7, d: 1800, say: ['Ta-da!', 'Nailed it.', "You're welcome."], p: 0.7,
      pose: (t, a, now) => base(now, { view: 'front', armF: { s: 2.3, e: -0.4 }, armB: { s: -2.3, e: 0.4 },
        mouth: 'grin', eyes: 'happy', fx: { sparkle: now / 400 } }) },
    coffee: { w: 1.2, d: 4500, say: ["Coffee o'clock ☕", 'Ahh, fuel.'], p: 0.3, pose(t, a, now) {
      const sip = bump(((t % 1500) - 600) / 700);
      return base(now, { props: { mug: true }, armF: { s: 1.0 + sip * 0.95, e: 1.4 + sip * 0.8 }, eyes: sip > 0.4 ? 'closed' : 'open',
        mouth: sip > 0.4 ? 'o' : 'smile', fx: { steam: now / 300 } });
    } },
    phoneCheck: { w: 1, d: 4000, say: ['Just checking…', 'Any news?'], p: 0.2, pose: (t, a, now) => withPhone(now, { mouth: 'flat' }) },
    water: { w: 0.4, d: 2400, say: ['Drink some water! 💧', 'Hydration check!'], p: 1,
      pose: (t, a, now) => base(now, { props: { mug: true }, armF: { s: 1.8, e: 0.6 }, mouth: 'grin', eyes: 'happy' }) },
    sneeze: { w: 0.4, d: 2200, pose(t, a, now) {
      if (t < 1200) return base(now, { headTilt: -0.2 * ease(t / 1200), lean: -0.12 * ease(t / 1200), eyes: 'closed', mouth: 'o', brows: 'up' });
      if (!a.said) { a.said = true; say('Achoo!', 1400); }
      const k = bump((t - 1200) / 700);
      return base(now, { lean: 0.35 * k, headTilt: 0.2 * k, eyes: 'closed', mouth: 'open', armF: { s: 1.4, e: 1.2 } });
    } },
    dance: { w: 0.6, d: 3000, pose: (t, a, now) => danceBase(now, false) },
  };

  function danceBase(now, withPhonesOn) {
    const beat = (now / 400) * Math.PI;
    const b = Math.sin(beat);
    const pose = base(now, { y: Math.abs(b) * 3, armF: { s: 1.6 + b * 1.2, e: 0.5 }, armB: { s: 1.6 - b * 1.2, e: 0.5 },
      legF: { h: 0.3 * Math.max(0, b), k: -0.5 * Math.max(0, b) }, legB: { h: 0.3 * Math.max(0, -b), k: -0.5 * Math.max(0, -b) },
      headTilt: b * 0.15, eyes: 'happy', mouth: Math.abs(b) > 0.7 ? 'o' : 'smile', fx: { notes: now / 1000 } });
    if (withPhonesOn) pose.props = { headphones: true };
    return pose;
  }

  const CODING = {
    typing: { w: 6, d: [6000, 12000], p: 0.15,
      say: ["console.log('why')", 'Tabs > spaces.', 'Just one more bug…', 'Compiling…', 'It works on my machine™', 'git push --force? 😬', '// TODO: fix later'],
      start: (a) => { a.tongue = chance(0.25); },
      pose: (t, a, now) => sitDesk(now, { armF: { s: 1.25 + Math.sin(now / 70) * 0.07, e: 0.35 }, armB: { s: 1.2 + Math.sin(now / 70 + 1.7) * 0.07, e: 0.35 },
        mouth: a.tongue ? 'tongue' : 'flat', brows: 'neutral' }) },
    thinkCode: { w: 2, d: 4200, pose(t, a, now) {
      const aha = t > 3000;
      if (aha && !a.said) { a.said = true; maybeSay(['Oh! I know!', 'Aha!', 'That’s the bug!'], 0.5); }
      return sitDesk(now, { armF: aha ? { s: 2.2, e: -0.7 } : { s: 2.05, e: 2.35 }, pupil: { x: 0.4, y: -0.8 }, brows: aha ? 'up' : 'raised',
        mouth: aha ? 'grin' : 'flat', fx: aha ? { bulb: 1 } : { question: clamp01(t / 400) } });
    } },
    facepalm: { w: 1.2, d: 2800, say: ['Who wrote this? …oh. Me.', "Why won't you work?!", '404: brain not found', 'Undefined is not a function. Again.'], p: 0.8,
      pose: (t, a, now) => sitDesk(now, { armF: { s: 2.4, e: 2.3 }, brows: 'sad', mouth: 'frown', eyes: 'closed', fx: { sweat: 1 } }) },
    celebrate: { w: 1, d: 2200, say: ['It compiles!!', 'Ship it! 🚀', 'Tests pass!', 'I am a genius.'], p: 0.8,
      pose: (t, a, now) => sitDesk(now, { armF: { s: 2.4, e: -0.4 + Math.sin(now / 90) * 0.3 }, armB: { s: -2.4, e: 0.4 - Math.sin(now / 90) * 0.3 },
        eyes: 'happy', mouth: 'laugh', fx: { sparkle: now / 400 } }) },
    sip: { w: 1.2, d: 4000, pose(t, a, now) {
      const sip = bump(((t % 1600) - 600) / 700);
      return sitDesk(now, { props: { laptop: true, mug: true }, armF: { s: 1.0 + sip * 0.95, e: 1.4 + sip * 0.8 }, eyes: sip > 0.4 ? 'closed' : 'open',
        mouth: sip > 0.4 ? 'o' : 'flat', fx: { steam: now / 300 } });
    } },
    rubEyes: { w: 0.4, d: 2600, say: ['Screen break?', 'My eyes…'], p: 0.6,
      pose: (t, a, now) => sitDesk(now, { armF: { s: 2.2, e: 2.3 + Math.sin(now / 90) * 0.1 }, armB: { s: 2.1, e: 2.3 }, eyes: 'closed', mouth: t > 1500 ? 'yawn' : 'flat' }) },
    stretchSit: { w: 0.5, d: 2800, pose(t, a, now) {
      if (!a.said && streakMin() > 60) { a.said = true; say(`${Math.round(streakMin())} minutes of code. Break time?`, 3200); }
      const k = bump(t / 2800);
      return sitDesk(now, { armF: { s: 2.45 * k + 1.2 * (1 - k), e: -0.3 * k }, armB: { s: -2.45 * k + 1.2 * (1 - k), e: 0.3 * k }, eyes: k > 0.5 ? 'closed' : 'open', mouth: 'o' });
    } },
  };

  const BROWSING = {
    scroll: { w: 5, d: [5000, 9000], pose: (t, a, now) => withPhone(now, { mouth: Math.sin(now / 3000) > 0.6 ? 'smile' : 'flat' }) },
    laugh: { w: 1.3, d: 2600, say: ['lol', '😂 this guy', 'Haha, classic.', 'The internet is wild.'], p: 0.6,
      pose: (t, a, now) => withPhone(now, { mouth: 'laugh', eyes: 'happy', lean: Math.sin(now / 70) * 0.05, y: Math.abs(Math.sin(now / 90)) }) },
    shock: { w: 1, d: 2000, say: ['Wait, what?!', 'No way!', 'Oh my…'], p: 0.7,
      pose: (t, a, now) => withPhone(now, { eyes: 'wide', brows: 'up', mouth: 'open', lean: -0.15, headTilt: 0, fx: { exclaim: 1 } }) },
    walkPhone: walkAct(1.5, 24, 200, { phone: true, def: { say: ['Just one more tab…', 'Research. Totally.', '*scroll scroll*'], p: 0.3 } }),
    look: { w: 1, d: 3000, pose: (t, a, now) => base(now, { headTurn: Math.sin(now / 900) * 0.8, pupil: { x: Math.sin(now / 900), y: 0 } }) },
  };

  const CALL = {
    nod: { w: 4, d: 5000, say: ['Mhm, mhm.', 'Right, right.'], p: 0.2,
      pose: (t, a, now) => headset(now, { headTilt: 0.08 * Math.sin(now / 260), mouth: Math.sin(now / 1300) > 0 ? 'smile' : 'flat' }) },
    talk: { w: 3, d: 4000, say: ['Great point!', 'Can you see my screen?', "Let's circle back.", 'Sorry, you go ahead.'], p: 0.25,
      pose: (t, a, now) => headset(now, { mouth: Math.sin(now / 90) > 0 ? 'talk' : 'smile', armF: { s: 0.9 + Math.sin(now / 500) * 0.5, e: 1.2 },
        brows: Math.sin(now / 1200) > 0.7 ? 'up' : 'neutral' }) },
    mute: { w: 0.8, d: 2800, say: ["You're on mute!", 'Sorry, I was on mute.'], p: 0.9,
      pose: (t, a, now) => headset(now, { armF: { s: 2.1, e: 2.5 }, eyes: 'wide', mouth: 'o', brows: 'up' }) },
    thumbsUp: { w: 1, d: 2000, say: ['Sounds good!', '👍'], p: 0.5,
      pose: (t, a, now) => headset(now, { armF: { s: 1.7, e: -0.4 }, mouth: 'grin', eyes: 'happy' }) },
    notes: { w: 1.2, d: 4000, pose: (t, a, now) => headset(now, { armF: { s: 1.2 + Math.sin(now / 80) * 0.08, e: 1.3 }, headTilt: 0.2, pupil: { x: 0.5, y: 1 }, mouth: 'flat' }) },
  };

  const MUSIC = {
    dance: { w: 4, d: 6000, pose: (t, a, now) => danceBase(now, true) },
    airGuitar: { w: 2, d: 4500, say: ['Air guitar solo! 🎸', '🤘'], p: 0.6,
      pose: (t, a, now) => phones(now, { armB: { s: 1.25, e: -0.1 }, armF: { s: 0.9 + Math.sin(now / 60) * 0.25, e: 1.0 },
        headTilt: 0.25 * Math.abs(Math.sin(now / 180)), lean: 0.15, legF: { h: 0.3, k: 0 }, legB: { h: -0.3, k: 0 },
        mouth: Math.sin(now / 400) > 0 ? 'open' : 'grin', eyes: 'closed', fx: { notes: now / 1000 } }) },
    headbang: { w: 1.5, d: 3000, say: ['🤘🤘'], p: 0.3,
      pose: (t, a, now) => phones(now, { headTilt: 0.35 * Math.abs(Math.sin(now / 150)), lean: 0.2, mouth: 'open', eyes: 'closed', fx: { notes: now / 1000 } }) },
    disco: { w: 1.5, d: 3500, say: ['Saturday night!', 'Disco time 🕺'], p: 0.5, pose(t, a, now) {
      const up = Math.floor(now / 500) % 2 === 0;
      return phones(now, { armF: up ? { s: 2.35, e: -0.5 } : { s: 0.6, e: 1.4 }, armB: { s: 0.2, e: 1.6 }, x: Math.sin(now / 250) * 1.5,
        legF: { h: 0.2, k: -0.3 }, mouth: 'grin', eyes: 'happy', fx: { sparkle: now / 400 } });
    } },
    spin: { w: 1, d: 1600, pose(t, a, now) {
      facing = Math.floor(t / 200) % 2 ? -1 : 1;
      return phones(now, { armF: { s: 1.6, e: 0 }, armB: { s: 1.6, e: 0 }, eyes: 'happy', mouth: 'grin', fx: { notes: now / 1000 } });
    } },
  };

  const SLEEP = {
    snooze: { w: 6, d: [15000, 30000], pose: (t, a, now) => base(now, { ...SIT, lean: 0.25, headTilt: 0.4 + Math.sin(now / 2500) * 0.05,
      armF: { s: 0.5, e: 0.8 }, armB: { s: 0.4, e: 0.8 }, eyes: 'closed', mouth: 'o', fx: { zzz: now / 1000, snot: now / 700 } }) },
    jolt: { w: 1, d: 2200, pose(t, a, now) {
      if (!a.said) { a.said = true; maybeSay(["Huh? I'm awake!", '…five more minutes.'], 0.5); }
      const k = bump(t / 2200);
      return base(now, { ...SIT, lean: 0.25 * (1 - k), headTilt: 0.4 * (1 - k), eyes: k > 0.3 ? 'wide' : 'closed', brows: 'up', mouth: 'o',
        armF: { s: 0.5, e: 0.8 }, armB: { s: 0.4, e: 0.8 } });
    } },
  };

  const RUN = {
    run: { w: 1, d: Infinity, start: (a) => { a.lastX = null; maybeSay(['Coming!', 'Wait for me!', 'On my way!'], 0.5); },
      pose(t, a, now) {
        // main.js carries the window across screens; face the way it's going.
        if (!a.polling) {
          a.polling = true;
          bounds().then((b) => { if (b && a.lastX !== null && b.x !== a.lastX) facing = b.x > a.lastX ? 1 : -1; if (b) a.lastX = b.x; a.polling = false; });
        }
        return base(now, { ...walkLimbs(now / 50, 0.9, true), lean: 0.25, mouth: 'open', fx: { speed: now / 1000 } });
      } },
  };

  // A reminder card is up above him: face you, point up at it.
  const ATTENTION = { d: Infinity, pose: (t, a, now) => base(now, {
    view: 'front', armF: { s: 2.3 + Math.sin(now / 250) * 0.12, e: -0.6 }, eyes: 'open', pupil: { x: 0.3, y: -0.9 },
    brows: 'up', mouth: 'smile', y: Math.abs(Math.sin(now / 400)) * 1.2 }) };

  const BY_MOOD = { idle: IDLE, coding: CODING, browsing: BROWSING, listening: CALL, vibing: MUSIC, asleep: SLEEP, running: RUN };

  function weightFor(name, def) {
    let w = def.w;
    if (mood === 'coding' && streakMin() > 60 && (name === 'stretchSit' || name === 'rubEyes')) w *= 5;
    if (lateNight() && name === 'yawn') w *= 4;
    return w;
  }

  // --- reactions: clicks and drags ----------------------------------------------------
  const REACTIONS = {
    hi: { d: 2000, say: ['Hi!', 'Hey you!', "What's up?", 'Oh, hello!'],
      pose: (t, a, now) => base(now, { view: 'front', armF: { s: 2.2, e: -0.5 + Math.sin(now / 110) * 0.45 }, eyes: 'happy', mouth: 'grin' }) },
    tickle: { d: 2000, say: ['Hehe, that tickles!', 'Stop it! 😆', 'Hahaha!'],
      pose: (t, a, now) => base(now, { mouth: 'laugh', eyes: 'happy', blush: 1, lean: Math.sin(now / 60) * 0.08, armF: { s: 0.9, e: 1.8 }, armB: { s: 0.9, e: 1.8 } }) },
    startle: { d: 1500, say: ['Whoa!', 'You scared me!', 'Eep!'], pose(t, a, now) {
      const air = bump(t / 600);
      return base(now, { y: air * 10, eyes: 'wide', brows: 'up', mouth: 'open', armF: { s: 2.3, e: -0.3 }, armB: { s: -2.3, e: 0.3 }, fx: { exclaim: 1 } });
    } },
    highFive: { d: 1800, say: ['High five!', 'Up top!'], pose: (t, a, now) => base(now, { armF: { s: 2.25, e: -0.6 }, mouth: 'grin', eyes: 'happy', headTurn: 0.7 }) },
    blush: { d: 2200, say: ['Aww 🥰', "You're the best.", 'Stop, you’re making me blush.'],
      pose: (t, a, now) => base(now, { view: 'front', blush: 1, eyes: 'happy', mouth: 'smile', armF: { s: 0.5, e: 2.0 }, armB: { s: 0.5, e: 2.0 }, fx: { heart: (t / 2200) } }) },
    salute: { d: 1800, say: ['Reporting for duty!', 'Sir, yes sir!'], pose: (t, a, now) => base(now, { armF: { s: 2.9, e: 1.65 }, mouth: 'flat', brows: 'angry' }) },
    busy: { d: 2000, say: ["Hey, I'm working here!", 'Busy! 😤', 'Shh, coding.'],
      pose: (t, a, now) => sitDesk(now, { brows: 'angry', mouth: 'flat', armF: { s: 0.9, e: 2.0 }, armB: { s: 0.9, e: 2.0 }, headTurn: 0.9 }) },
  };

  let clicks = [];
  function onClick() {
    const now = performance.now();
    clicks = clicks.filter((c) => now - c < 3000).concat(now);
    if (clicks.length >= 5) {
      clicks = [];
      return startAct('dizzy', { d: 1800, say: ['Okay okay, I get it!', '@_@'], p: 1,
        pose: (t, a, n) => base(n, { eyes: 'dizzy', mouth: 'frown', fx: { stars: n / 1000 }, lean: Math.sin(n / 200) * 0.1 }) }, true);
    }
    const names = Object.keys(REACTIONS).filter((n) => (n === 'busy') === (mood === 'coding') || (n !== 'busy' && chance(0.3)));
    const name = pick(names.length ? names : ['hi']);
    startAct(name, { ...REACTIONS[name], p: 0.9 }, true);
  }

  // Drag vs. click: real mouse movement, not CSS app-region.
  let dragging = false, dragMoved = 0, lastX = 0, lastY = 0;
  window.addEventListener('mousedown', (e) => { dragging = true; dragMoved = 0; lastX = e.screenX; lastY = e.screenY; });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const dx = e.screenX - lastX, dy = e.screenY - lastY;
    lastX = e.screenX; lastY = e.screenY;
    dragMoved += Math.abs(dx) + Math.abs(dy);
    if (dragMoved > 5 && (!act || act.name !== 'dangle')) {
      startAct('dangle', { d: Infinity, say: ['Whoa-oa-oa!', 'Put me down!', 'Wheee!', 'Where are we going?'], p: 0.8,
        pose: (t, a, now) => base(now, { y: 4, legF: { h: Math.sin(now / 110) * 0.6, k: -0.4 }, legB: { h: -Math.sin(now / 110) * 0.6, k: -0.4 },
          armF: { s: 2.4, e: -0.2 + Math.sin(now / 90) * 0.3 }, armB: { s: -2.4, e: 0.2 - Math.sin(now / 90) * 0.3 }, eyes: 'wide', brows: 'up', mouth: 'open', fx: { sweat: 1 } }) }, true);
    }
    if ((dx || dy) && pet.moveWindowBy) pet.moveWindowBy(dx, dy);
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    if (dragMoved < 5) return onClick();
    const far = dragMoved > 500;
    startAct('land', { d: far ? 2000 : 900, say: far ? ["I'm okay!", '@_@', 'That was a ride.'] : ['Thanks for the lift.'], p: far ? 0.9 : 0.3,
      pose(t, a, now) {
        const sq = bump(t / 350) * 0.22;
        return base(now, { squash: sq, eyes: far ? 'dizzy' : 'open', mouth: far ? 'frown' : 'smile', fx: far ? { stars: now / 1000 } : {} });
      } }, true);
  });

  // --- scheduler + pose blending ---------------------------------------------------------
  let act = null;
  let lastPose = base(performance.now());
  let blendFrom = null, blendStart = 0;
  const BLEND_MS = 260;

  function startAct(name, def, locked = false) {
    const now = performance.now();
    const d = Array.isArray(def.d) ? rand(def.d[0], def.d[1]) : def.d;
    act = { name, def, d, t0: now, locked, done: false };
    blendFrom = lastPose;
    blendStart = now;
    if (def.start) def.start(act);
    maybeSay(def.say, def.p ?? 0.35);
  }

  function nextAct() {
    const acts = BY_MOOD[mood] || IDLE;
    const names = Object.keys(acts);
    const weights = names.map((n) => (act && act.name === n && names.length > 1 ? 0 : weightFor(n, acts[n])));
    let r = Math.random() * weights.reduce((a, b) => a + b, 0);
    let chosen = names[0];
    for (let i = 0; i < names.length; i++) { r -= weights[i]; if (r <= 0) { chosen = names[i]; break; } }
    startAct(chosen, acts[chosen]);
  }

  function lerpPose(a, b, k) {
    if (typeof a === 'number' && typeof b === 'number') return a + (b - a) * k;
    if (a && b && typeof a === 'object' && typeof b === 'object' && !Array.isArray(b)) {
      const out = { ...b };
      for (const key of Object.keys(b)) if (key !== 'props' && key !== 'fx' && key in a) out[key] = lerpPose(a[key], b[key], k);
      return out;
    }
    return b;
  }

  let lastFrame = performance.now();
  function frame() {
    const now = performance.now();
    const dt = Math.min(64, now - lastFrame);
    lastFrame = now;
    if (!act || act.done || (now - act.t0 > act.d && act.name !== 'dangle')) {
      if (act && act.locked && act.name === 'dangle' && dragging) { /* keep dangling */ } else nextAct();
    }
    let pose = act.def.pose(now - act.t0, act, now, dt);
    if (blendFrom && now - blendStart < BLEND_MS) pose = lerpPose(blendFrom, pose, ease((now - blendStart) / BLEND_MS));
    pose.facing = facing;
    pose.character = character;
    lastPose = pose;
    drawMan(ctx, canvas.width, canvas.height, pose);
    requestAnimationFrame(frame);
  }

  startAct('hello', { ...IDLE.wave, say: ['Hi! Your little helper is here.'], p: 1 });
  if (pet.getUserName) pet.getUserName().then((n) => { if (n) say(`Hi ${n}! Your little helper is here.`); }).catch(() => {});
  requestAnimationFrame(frame);
})();
