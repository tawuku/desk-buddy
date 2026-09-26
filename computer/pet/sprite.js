// Pet sprites -- flat-color, outlined canvas drawing, one skeleton for every
// character. A pure function of a "pose": jointed arms and legs (angles), a
// face (eyes / brows / mouth), a view (front / side / back, so he can walk
// any direction), props and effects. Every gesture in renderer.js is just a
// pose over time; every character (PET_CHARACTERS) can do every gesture.
// Shared by the Electron renderer, preview.html and goals.html.

const MAN_PALETTE = {
  outline: '#2b2118',
  skin: '#f2c29b',
  skinShade: '#d9a57c',
  hand: null,          // defaults to skin
  hair: '#3b2a20',
  hairShine: '#5a4232',
  shirt: '#3d7fd9',
  shirtShade: '#2d62ad',
  pants: '#3a3f55',
  pantsShade: '#2a2e40',
  shoe: '#1f1b18',
  sole: null,          // a white sneaker sole when set
  white: '#fefefe',
  pupil: '#241a12',
  iris: null,          // coloured iris (animals); null = plain dark eyes
  eyeLine: '#2b2118',  // closed / happy eye lines
  brow: '#3b2a20',
  mouth: '#7a2e2e',
  tongue: '#f0909f',
  blush: '#f0909f',
  prop: '#2a2d36',
  propLight: '#8a93a8',
  screen: '#9fd4ff',
  mug: '#f4f1ea',
  coffee: '#6b4226',
  fx: '#ffffff',
  heart: '#ff6b81',
  star: '#ffd54a',
  sweat: '#8fd3ff',
  note: '#c3a6ff',
  fur: '#1d1d22',
  furShade: '#111114',
  face: '#fbfbfd',     // muzzle / blaze / cheeks
  earInner: '#f29bb0',
  pack: '#8a5a33',
  packShade: '#6b4426',
  led: '#5ff2ff',
};

// The characters. `head` picks the head drawing; the rest is palette and
// outfit on the same body.
const PET_CHARACTERS = {
  human: { label: 'Little man', blurb: 'Your classic sidekick', head: 'human', palette: {} },
  cat: {
    label: 'Tux the cat', blurb: 'Hoodie, backpack, attitude', head: 'cat', hoodie: true, backpack: true, tail: 'cat', longSleeves: true,
    palette: { hand: '#1d1d22', shirt: '#2f6fe0', shirtShade: '#2457b8', pants: '#1d1d22', pantsShade: '#111114', shoe: '#1f1f24', sole: '#f4f4f6',
      iris: '#c9e05a', eyeLine: '#e4e4ec', brow: '#8d8d99', pupil: '#101014' },
  },
  panda: {
    label: 'Bao the panda', blurb: 'Snacks first, then goals', head: 'panda', hoodie: true, longSleeves: true, tail: null,
    palette: { fur: '#f6f6f4', furShade: '#dcdcd8', hand: '#1d1d22', shirt: '#3fae6a', shirtShade: '#2f8a52', pants: '#1d1d22', pantsShade: '#111114',
      shoe: '#f4f4f6', sole: '#c9ccd6', iris: '#ffffff', eyeLine: '#e4e4ec', brow: '#55555f' },
  },
  robot: {
    label: 'Bolt the robot', blurb: 'Beep boop, drink water', head: 'robot', longSleeves: true, robot: true,
    palette: { fur: '#b9c3cf', furShade: '#8f9aa8', hand: '#8f9aa8', shirt: '#9aa6b4', shirtShade: '#7d8896', pants: '#6c7684', pantsShade: '#58616d', shoe: '#3b4350' },
  },
  fox: {
    label: 'Fin the fox', blurb: 'Quick, clever, a bit sly', head: 'fox', hoodie: true, longSleeves: true, tail: 'fox',
    palette: { fur: '#e8843c', furShade: '#c96a26', hand: '#3a2418', shirt: '#1f9e9a', shirtShade: '#177a77', pants: '#3a2418', pantsShade: '#2a1a10',
      shoe: '#2a2d36', sole: '#f4f4f6', face: '#fbf3ea', earInner: '#fbe3cf', brow: '#8a4a1e' },
  },
};

let C = MAN_PALETTE; // palette of the character being drawn
let CH = PET_CHARACTERS.human;

// Design grid: 85 x 100 units; the canvas is scaled to fit its width.
const GRID_W = 85;
const GROUND = 95;
const LIMB = { upperArm: 8, foreArm: 7.5, thigh: 8.5, shin: 8.5, torso: 16, headR: 12.5 };

// Angles are radians from straight down; positive swings forward (toward
// the way he faces). Elbow/knee angles are relative to the upper segment.
//
// pose fields (all optional):
//   character         key of PET_CHARACTERS (default 'human')
//   view              'side' (default, faces `facing`) | 'front' | 'back'
//   x, y              horizontal offset / jump height (grid units, y up)
//   facing            1 = right, -1 = left (side view)
//   lean              upper-body lean around the hip (+ = forward)
//   tilt              whole-body tilt around the feet (falling over)
//   armF, armB        {s: shoulder, e: elbow}   front (near) / back (far) arm;
//                     front/back views: armF = his arm on screen right
//   legF, legB        {h: hip, k: knee}
//   headTilt          radians; headTurn -1..1 (face features shift)
//   eyes              'open' | 'closed' | 'happy' | 'wide' | 'dizzy' | 'wink' | 'sleepy'
//   eyeOpen 0..1      blink; pupil {x, y} -1..1 look direction
//   brows             'neutral' | 'up' | 'angry' | 'sad' | 'raised'
//   mouth             'smile' | 'grin' | 'open' | 'o' | 'flat' | 'frown' | 'laugh'
//                     | 'yawn' | 'tongue' | 'whistle' | 'talk'
//   blush 0..1, squash 0..0.3, tailWag -1..1
//   props             {laptop, phone, mug, headphones, headset}
//   fx                {zzz, stars, heart, sweat, exclaim, question, notes, snot,
//                      bulb, speed, sparkle, steam, typing} -- animation phases
function drawMan(ctx, width, height, pose = {}) {
  const p = {
    x: 0, y: 0, facing: 1, lean: 0, tilt: 0, view: 'side',
    armF: { s: 0.08, e: 0.12 }, armB: { s: -0.08, e: 0.12 },
    legF: { h: 0, k: 0 }, legB: { h: 0, k: 0 },
    headTilt: 0, headTurn: 0.35,
    eyes: 'open', eyeOpen: 1, pupil: { x: 0, y: 0 },
    brows: 'neutral', mouth: 'smile', blush: 0, squash: 0, tailWag: null,
    props: {}, fx: {},
    ...pose,
  };
  CH = PET_CHARACTERS[p.character] || PET_CHARACTERS.human;
  C = { ...MAN_PALETTE, ...CH.palette };
  if (!C.hand) C.hand = C.skin;
  if (p.tailWag === null) p.tailWag = Math.sin((typeof performance !== 'undefined' ? performance.now() : 0) / 700);
  if (p.view !== 'side') { p.headTurn = 0; p.facing = 1; }

  const s = width / GRID_W;
  ctx.save();
  ctx.clearRect(0, 0, width, height);
  ctx.scale(s, s);
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  const cx = GRID_W / 2 + p.x;
  const drop = (leg) => Math.cos(leg.h) * LIMB.thigh + Math.cos(leg.h + leg.k) * LIMB.shin;
  const hipY = GROUND - Math.max(drop(p.legF), drop(p.legB)) - p.y;

  // Shadow stays on the ground, shrinking as he jumps.
  ctx.fillStyle = 'rgba(0,0,0,0.18)';
  ctx.beginPath();
  ctx.ellipse(cx, GROUND + 1.5, 13 / (1 + p.y / 25), 2.2 / (1 + p.y / 25), 0, 0, Math.PI * 2);
  ctx.fill();

  ctx.translate(cx, 0);
  ctx.scale(p.facing, 1);
  ctx.translate(-cx, 0);
  ctx.translate(cx, GROUND);
  ctx.rotate(p.tilt);
  ctx.scale(1 + p.squash * 0.6, 1 - p.squash);
  ctx.translate(-cx, -GROUND);

  const shoulderY = hipY - LIMB.torso;
  const upper = (fn) => {
    ctx.save();
    ctx.translate(cx, hipY);
    ctx.rotate(p.lean);
    ctx.translate(-cx, -hipY);
    fn();
    ctx.restore();
  };

  if (p.view === 'side') drawSide(ctx, cx, hipY, shoulderY, p, upper);
  else drawFrontBack(ctx, cx, hipY, shoulderY, p, upper, p.view === 'back');
  ctx.restore();

  const headTop = { x: cx + 1 * p.facing, y: shoulderY - 10.5 - LIMB.headR };
  drawFx(ctx, s, headTop, p);
}

function drawSide(ctx, cx, hipY, shoulderY, p, upper) {
  if (CH.tail) drawTail(ctx, cx - 4, hipY - 1, -1, p.tailWag);
  upper(() => {
    if (CH.backpack) {
      ctx.fillStyle = C.outline; roundRect(ctx, cx - 13, shoulderY + 0.5, 7.5, 14, 2.5); ctx.fill();
      ctx.fillStyle = C.pack; roundRect(ctx, cx - 12.2, shoulderY + 1.3, 6, 12.4, 2); ctx.fill();
      ctx.fillStyle = C.packShade; roundRect(ctx, cx - 12.2, shoulderY + 1.3, 6, 4, 2); ctx.fill();
    }
    drawArm(ctx, cx - 4, shoulderY + 2.5, p.armB, true);
  });
  drawLeg(ctx, cx - 2.5, hipY, p.legB, true, false);
  drawLeg(ctx, cx + 2.5, hipY, p.legF, false, false);
  upper(() => {
    drawTorso(ctx, cx, shoulderY, hipY, 'side');
    if (p.props.laptop) drawLaptop(ctx, cx, hipY, p);
    drawHead(ctx, cx + 1, shoulderY - 10.5, p, 'side');
    const hand = drawArm(ctx, cx + 4, shoulderY + 2.5, p.armF, false);
    if (CH.backpack) seg(ctx, cx + 2.5, shoulderY + 0.5, cx + 3.5, shoulderY + 9, C.pack, 1.6);
    if (p.props.phone) drawPhone(ctx, hand);
    if (p.props.mug) drawMug(ctx, hand, p.fx.steam || 0);
  });
}

// Front and back share one body: legs side by side, arms out to the sides.
// Side-view arm angles are translated: arms raised above ~100 degrees stay
// raised (out to the side); lower ones fold in front of the chest.
function frontArm(arm, mirror) {
  const raised = Math.abs(arm.s) > 1.8;
  const s = raised ? Math.abs(arm.s) : Math.abs(arm.s) * 0.3 + 0.08;
  const e = raised ? arm.e * Math.sign(arm.s || 1) : -Math.abs(arm.e);
  return mirror ? { s: -s, e: -e } : { s, e };
}

function drawFrontBack(ctx, cx, hipY, shoulderY, p, upper, back) {
  if (CH.tail && !back) drawTail(ctx, cx + 3, hipY, 1, p.tailWag);
  drawLeg(ctx, cx - 3.6, hipY, p.legB, false, true);
  drawLeg(ctx, cx + 3.6, hipY, p.legF, false, true);
  upper(() => {
    if (back) {
      drawTorso(ctx, cx, shoulderY, hipY, 'back');
      if (CH.tail) drawTail(ctx, cx, hipY - 1, 1, p.tailWag);
      if (CH.backpack) {
        ctx.fillStyle = C.outline; roundRect(ctx, cx - 8.5, shoulderY + 1, 17, 15.5, 4); ctx.fill();
        ctx.fillStyle = C.pack; roundRect(ctx, cx - 7.5, shoulderY + 2, 15, 13.5, 3.2); ctx.fill();
        ctx.fillStyle = C.packShade; roundRect(ctx, cx - 7.5, shoulderY + 2, 15, 5, 3); ctx.fill();
        ctx.fillStyle = C.packShade; roundRect(ctx, cx - 4.5, shoulderY + 9.5, 9, 4.5, 1.5); ctx.fill();
      }
    } else {
      drawTorso(ctx, cx, shoulderY, hipY, 'front');
      if (CH.backpack) {
        seg(ctx, cx - 5, shoulderY + 0.5, cx - 5.5, shoulderY + 11, C.pack, 1.8);
        seg(ctx, cx + 5, shoulderY + 0.5, cx + 5.5, shoulderY + 11, C.pack, 1.8);
      }
    }
    // Arms: screen-right = armF, screen-left = armB (mirrored).
    const right = drawArm(ctx, cx + 7.5, shoulderY + 2.5, frontArm(p.armF, false), false);
    drawArm(ctx, cx - 7.5, shoulderY + 2.5, frontArm(p.armB, true), false);
    drawHead(ctx, cx, shoulderY - 10.5, p, back ? 'back' : 'front');
    if (!back && p.props.phone) drawPhone(ctx, right);
    if (!back && p.props.mug) drawMug(ctx, right, p.fx.steam || 0);
  });
}

// --- body ------------------------------------------------------------------

function seg(ctx, x0, y0, x1, y1, color, w) {
  ctx.strokeStyle = C.outline;
  ctx.lineWidth = w + 2;
  ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
  ctx.strokeStyle = color;
  ctx.lineWidth = w;
  ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
}

function polar(x, y, angle, len) {
  return { x: x + Math.sin(angle) * len, y: y + Math.cos(angle) * len };
}

function shade(hex, amt) {
  const n = parseInt(hex.slice(1), 16);
  const f = (v) => Math.max(0, Math.min(255, Math.round(v * (1 - amt))));
  return `rgb(${f(n >> 16)},${f((n >> 8) & 255)},${f(n & 255)})`;
}

function drawArm(ctx, x, y, arm, back) {
  const elbow = polar(x, y, arm.s, LIMB.upperArm);
  const hand = polar(elbow.x, elbow.y, arm.s + arm.e, LIMB.foreArm);
  const sleeve = back ? C.shirtShade : C.shirt;
  const fore = CH.longSleeves ? sleeve : back ? C.skinShade : C.skin;
  seg(ctx, x, y, elbow.x, elbow.y, sleeve, 4.2);
  seg(ctx, elbow.x, elbow.y, hand.x, hand.y, fore, CH.longSleeves ? 3.8 : 3.4);
  ctx.fillStyle = C.outline;
  ctx.beginPath(); ctx.arc(hand.x, hand.y, 2.7, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = back ? shade(C.hand, 0.15) : C.hand;
  ctx.beginPath(); ctx.arc(hand.x, hand.y, 1.9, 0, Math.PI * 2); ctx.fill();
  return hand;
}

function drawLeg(ctx, x, y, leg, back, front) {
  const color = back ? C.pantsShade : C.pants;
  if (front) {
    // Seen from the front: a lifted leg reads as a shorter leg (knee up).
    const kneeY = y + Math.cos(leg.h) * LIMB.thigh;
    const footY = kneeY + Math.cos(leg.h + leg.k) * LIMB.shin;
    const out = Math.sign(x - GRID_W / 2) * 0.6;
    seg(ctx, x, y, x + out, kneeY, color, 5);
    seg(ctx, x + out, kneeY, x + out * 1.4, footY, color, 4.6);
    ctx.fillStyle = C.outline;
    ctx.beginPath(); ctx.ellipse(x + out * 1.4, footY + 0.8, 3.6, 2.5, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = C.shoe;
    ctx.beginPath(); ctx.ellipse(x + out * 1.4, footY + 0.6, 2.7, 1.7, 0, 0, Math.PI * 2); ctx.fill();
    if (C.sole) { ctx.fillStyle = C.sole; ctx.fillRect(x + out * 1.4 - 2.8, footY + 1.4, 5.6, 1.1); }
    return;
  }
  const knee = polar(x, y, leg.h, LIMB.thigh);
  const foot = polar(knee.x, knee.y, leg.h + leg.k, LIMB.shin);
  seg(ctx, x, y, knee.x, knee.y, color, 5);
  seg(ctx, knee.x, knee.y, foot.x, foot.y, color, 4.4);
  const a = leg.h + leg.k;
  ctx.save();
  ctx.translate(foot.x, foot.y);
  ctx.rotate(-a * 0.6);
  ctx.fillStyle = C.outline;
  ctx.beginPath(); ctx.ellipse(1.6, 0.4, 4.4, 2.6, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = C.shoe;
  ctx.beginPath(); ctx.ellipse(1.6, 0.2, 3.4, 1.7, 0, 0, Math.PI * 2); ctx.fill();
  if (C.sole) { ctx.fillStyle = C.sole; ctx.fillRect(-1.6, 1.1, 6.6, 1.1); }
  ctx.restore();
}

function drawTail(ctx, x, y, dir, wag) {
  const fox = CH.tail === 'fox';
  const tipX = x + dir * (fox ? 13 : 11) + wag * 2.5;
  const tipY = y - (fox ? 12 : 15);
  const midX = x + dir * (fox ? 12 : 10);
  const midY = y + 1;
  const w = fox ? 6.5 : 3.2;
  ctx.strokeStyle = C.outline;
  ctx.lineWidth = w + 2;
  ctx.beginPath(); ctx.moveTo(x, y); ctx.quadraticCurveTo(midX, midY, tipX, tipY); ctx.stroke();
  ctx.strokeStyle = C.fur;
  ctx.lineWidth = w;
  ctx.beginPath(); ctx.moveTo(x, y); ctx.quadraticCurveTo(midX, midY, tipX, tipY); ctx.stroke();
  if (fox) {
    ctx.fillStyle = C.face;
    ctx.beginPath(); ctx.arc(tipX, tipY, 3.1, 0, Math.PI * 2); ctx.fill();
  }
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawTorso(ctx, cx, shoulderY, hipY, view) {
  const wide = view === 'side' ? 0 : 1.5;
  ctx.fillStyle = C.outline;
  roundRect(ctx, cx - 8 - wide, shoulderY - 1, 16 + wide * 2, hipY - shoulderY + 4, 5);
  ctx.fill();
  ctx.fillStyle = C.shirt;
  roundRect(ctx, cx - 7 - wide, shoulderY, 14 + wide * 2, hipY - shoulderY + 2, 4.2);
  ctx.fill();
  ctx.fillStyle = C.pants;
  ctx.fillRect(cx - 7 - wide, hipY - 2, 14 + wide * 2, 4);
  const mid = view === 'side' ? cx + 0.5 : cx;
  if (CH.robot) {
    ctx.fillStyle = C.shirtShade;
    roundRect(ctx, mid - 4, shoulderY + 3, 8, 7, 1.5); ctx.fill();
    if (view !== 'back') {
      const glow = 0.6 + 0.4 * Math.sin((typeof performance !== 'undefined' ? performance.now() : 0) / 400);
      ctx.fillStyle = `rgba(95,242,255,${glow})`;
      ctx.beginPath(); ctx.arc(mid, shoulderY + 6.5, 2, 0, Math.PI * 2); ctx.fill();
    }
    return;
  }
  if (CH.hoodie) {
    ctx.fillStyle = C.shirtShade;
    if (view === 'back') {
      ctx.beginPath(); ctx.ellipse(cx, shoulderY + 3, 7, 4.5, 0, 0, Math.PI * 2); ctx.fill();
    } else {
      // pocket + drawstrings
      roundRect(ctx, mid - 5, hipY - 7.5, 10, 4.8, 1.8); ctx.fill();
      ctx.strokeStyle = '#f1f1f5'; ctx.lineWidth = 0.9;
      for (const dx of view === 'side' ? [1.5] : [-1.8, 1.8]) {
        ctx.beginPath(); ctx.moveTo(mid + dx, shoulderY + 0.8); ctx.lineTo(mid + dx * 1.15, shoulderY + 6); ctx.stroke();
      }
    }
    return;
  }
  if (view === 'back') return;
  ctx.fillStyle = C.skin;
  ctx.beginPath();
  ctx.moveTo(mid - 3, shoulderY); ctx.lineTo(mid + 3, shoulderY); ctx.lineTo(mid, shoulderY + 3.5);
  ctx.closePath(); ctx.fill();
  ctx.fillStyle = '#9fd4ff';
  ctx.beginPath(); ctx.arc(mid + 3.5, shoulderY + 6, 1.1, 0, Math.PI * 2); ctx.fill();
}

// --- heads -------------------------------------------------------------------

function drawHead(ctx, hx, hy, p, view) {
  const r = LIMB.headR;
  ctx.save();
  ctx.translate(hx, hy + r * 0.8);
  ctx.rotate(p.headTilt);
  ctx.translate(-hx, -(hy + r * 0.8));
  const t = view === 'side' ? p.headTurn * 3.2 : 0;

  // neck
  ctx.fillStyle = CH.head === 'human' ? C.skinShade : C.furShade;
  ctx.fillRect(hx - 2.5, hy + r - 3, 5, 4);
  if (CH.hoodie) {
    // the hood bunches up behind the head
    ctx.fillStyle = C.outline;
    ctx.beginPath(); ctx.ellipse(hx - (view === 'side' ? 3 : 0), hy + r - 1, view === 'side' ? 8 : 10.5, 4.6, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = C.shirtShade;
    ctx.beginPath(); ctx.ellipse(hx - (view === 'side' ? 3 : 0), hy + r - 1, view === 'side' ? 7 : 9.5, 3.6, 0, 0, Math.PI * 2); ctx.fill();
  }
  if (p.props.headphones || p.props.headset) drawHeadphonesBack(ctx, hx, hy, r);

  const HEADS = { human: headHuman, cat: headCat, panda: headPanda, robot: headRobot, fox: headFox };
  (HEADS[CH.head] || headHuman)(ctx, hx, hy, r, t, p, view);

  if (view !== 'back') {
    if (p.fx.snot) drawSnot(ctx, hx + t * 1.3 + 1.5, hy + 4.8, p.fx.snot);
    if (p.fx.sweat) drawSweat(ctx, hx + r - 1, hy - 5, p.fx.sweat);
  }
  if (p.props.headphones || p.props.headset) drawHeadphonesFront(ctx, hx, hy, r, t, !!p.props.headset && view !== 'back');
  ctx.restore();
}

function headCircle(ctx, hx, hy, r, fill) {
  ctx.fillStyle = C.outline;
  ctx.beginPath(); ctx.arc(hx, hy, r + 1, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = fill;
  ctx.beginPath(); ctx.arc(hx, hy, r, 0, Math.PI * 2); ctx.fill();
}

function face(ctx, x, y, p, opts = {}) {
  if (p.blush > 0) {
    ctx.globalAlpha = Math.min(1, p.blush) * 0.8;
    ctx.fillStyle = C.blush;
    ctx.beginPath();
    ctx.ellipse(x - 7, y + 4, 2.6, 1.5, 0, 0, Math.PI * 2);
    ctx.ellipse(x + 7, y + 4, 2.6, 1.5, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }
  drawEyes(ctx, x, y, p, opts.eyeGap || 4.6);
  drawBrows(ctx, x, y - 5, p.brows);
  if (opts.mouth !== false) drawMouth(ctx, x + (opts.mouthDx || 0), y + (opts.mouthDy || 6.2), p.mouth, opts.animal);
}

function headHuman(ctx, hx, hy, r, t, p, view) {
  const ear = (x) => {
    ctx.fillStyle = C.outline; ctx.beginPath(); ctx.arc(x, hy + 1.5, 3, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = C.skinShade; ctx.beginPath(); ctx.arc(x, hy + 1.5, 2, 0, Math.PI * 2); ctx.fill();
  };
  if (view !== 'side') { ear(hx - r + 0.5); ear(hx + r - 0.5); }
  headCircle(ctx, hx, hy, r, C.skin);
  if (view === 'side') ear(hx - r + 0.8 + t * 0.3);
  if (view === 'back') {
    ctx.fillStyle = C.hair;
    ctx.beginPath(); ctx.arc(hx, hy, r, Math.PI * 0.92, Math.PI * 2.08); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.ellipse(hx, hy + 2, r, r * 0.75, 0, 0, Math.PI); ctx.fill();
    return;
  }
  drawHair(ctx, hx, hy, r, t);
  face(ctx, hx + t, hy + 1, p);
  ctx.strokeStyle = C.skinShade;
  ctx.lineWidth = 1.1;
  ctx.beginPath(); ctx.moveTo(hx + t * 1.3 + 0.6, hy + 2.8); ctx.lineTo(hx + t * 1.3 + 1.4, hy + 4.4); ctx.stroke();
}

function drawHair(ctx, hx, hy, r, t) {
  ctx.fillStyle = C.outline;
  ctx.beginPath();
  ctx.arc(hx, hy, r + 1, Math.PI * 1.02, Math.PI * 1.98);
  ctx.lineTo(hx + r + 1, hy - 2);
  ctx.quadraticCurveTo(hx + 4 + t, hy - 6, hx - 2 + t, hy - 4.5);
  ctx.quadraticCurveTo(hx - 8, hy - 3, hx - r - 1, hy - 1);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = C.hair;
  ctx.beginPath();
  ctx.arc(hx, hy, r, Math.PI * 1.04, Math.PI * 1.96);
  ctx.lineTo(hx + r, hy - 2.5);
  ctx.quadraticCurveTo(hx + 4 + t, hy - 7, hx - 2 + t, hy - 5.5);
  ctx.quadraticCurveTo(hx - 8, hy - 4, hx - r, hy - 1.5);
  ctx.closePath();
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(hx - 3 + t, hy - r + 1);
  ctx.quadraticCurveTo(hx + 2 + t, hy - r - 5, hx + 7 + t, hy - r + 0.5);
  ctx.quadraticCurveTo(hx + 3 + t, hy - r + 2, hx - 3 + t, hy - r + 1);
  ctx.fill();
  ctx.fillStyle = C.hairShine;
  ctx.beginPath(); ctx.ellipse(hx - 4 + t * 0.5, hy - r + 3.5, 3, 1.1, -0.3, 0, Math.PI * 2); ctx.fill();
}

function catEars(ctx, hx, hy, r, t, inner, tall = 1, tip = null) {
  for (const side of [-1, 1]) {
    const bx = hx + side * 7 + t * 0.4;
    const apexX = hx + side * 9.5 + t * 0.5;
    const apexY = hy - r - 6 * tall;
    ctx.fillStyle = C.outline;
    ctx.beginPath(); ctx.moveTo(bx - 5.5, hy - r + 5); ctx.lineTo(apexX, apexY - 1); ctx.lineTo(bx + 5.5, hy - r + 5); ctx.closePath(); ctx.fill();
    ctx.fillStyle = C.fur;
    ctx.beginPath(); ctx.moveTo(bx - 4.3, hy - r + 5); ctx.lineTo(apexX, apexY); ctx.lineTo(bx + 4.3, hy - r + 5); ctx.closePath(); ctx.fill();
    if (tip) {
      ctx.fillStyle = tip;
      ctx.beginPath(); ctx.moveTo(apexX - 1.8, apexY + 3.4); ctx.lineTo(apexX, apexY); ctx.lineTo(apexX + 1.8, apexY + 3.4); ctx.closePath(); ctx.fill();
    }
    if (inner) {
      ctx.fillStyle = C.earInner;
      ctx.beginPath(); ctx.moveTo(bx - 2.3, hy - r + 4.5); ctx.lineTo(apexX, apexY + 3.5); ctx.lineTo(bx + 2.3, hy - r + 4.5); ctx.closePath(); ctx.fill();
    }
  }
}

function headCat(ctx, hx, hy, r, t, p, view) {
  catEars(ctx, hx, hy, r, t, view !== 'back');
  headCircle(ctx, hx, hy, r, C.fur);
  if (view === 'back') return;
  const x = hx + t;
  // white blaze up the middle + muzzle
  ctx.fillStyle = C.face;
  ctx.beginPath();
  ctx.moveTo(x - 1, hy - r + 2); ctx.lineTo(x + 1, hy - r + 2); ctx.lineTo(x + 3.2, hy + 1); ctx.lineTo(x - 3.2, hy + 1); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.ellipse(x, hy + 5.5, 7.5, 5.2, 0, 0, Math.PI * 2); ctx.fill();
  // whiskers
  ctx.strokeStyle = 'rgba(235,235,242,0.85)';
  ctx.lineWidth = 0.6;
  for (const side of [-1, 1]) for (const dy of [-0.8, 0.8]) {
    ctx.beginPath(); ctx.moveTo(x + side * 6.5, hy + 5 + dy); ctx.lineTo(x + side * 13, hy + 4 + dy * 2.2); ctx.stroke();
  }
  face(ctx, x, hy - 0.5, p, { eyeGap: 5.2, mouthDy: 6.8, animal: true });
  ctx.fillStyle = C.earInner;
  ctx.beginPath(); ctx.moveTo(x - 1.4, hy + 3.4); ctx.lineTo(x + 1.4, hy + 3.4); ctx.lineTo(x, hy + 4.8); ctx.closePath(); ctx.fill();
}

function headPanda(ctx, hx, hy, r, t, p, view) {
  for (const side of [-1, 1]) {
    ctx.fillStyle = C.outline; ctx.beginPath(); ctx.arc(hx + side * 9 + t * 0.4, hy - r + 2.5, 4.6, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#1d1d22'; ctx.beginPath(); ctx.arc(hx + side * 9 + t * 0.4, hy - r + 2.5, 3.8, 0, Math.PI * 2); ctx.fill();
  }
  headCircle(ctx, hx, hy, r, C.fur);
  if (view === 'back') return;
  const x = hx + t;
  ctx.fillStyle = '#1d1d22';
  for (const side of [-1, 1]) {
    ctx.beginPath(); ctx.ellipse(x + side * 4.9, hy + 0.2, 3.3, 4.2, side * -0.5, 0, Math.PI * 2); ctx.fill();
  }
  face(ctx, x, hy - 0.2, p, { eyeGap: 4.9, mouthDy: 6.8, animal: true });
  ctx.fillStyle = '#1d1d22';
  ctx.beginPath(); ctx.ellipse(x, hy + 4, 1.8, 1.2, 0, 0, Math.PI * 2); ctx.fill();
}

function headFox(ctx, hx, hy, r, t, p, view) {
  catEars(ctx, hx, hy, r, t, view !== 'back', 1.35, '#3a2418');
  headCircle(ctx, hx, hy, r, C.fur);
  if (view === 'back') return;
  const x = hx + t;
  ctx.fillStyle = C.face;
  ctx.beginPath();
  ctx.moveTo(x - r + 1, hy + 1);
  ctx.quadraticCurveTo(x - 5, hy + 1, x, hy + 4);
  ctx.quadraticCurveTo(x + 5, hy + 1, x + r - 1, hy + 1);
  ctx.quadraticCurveTo(x + r - 2, hy + r - 1, x, hy + r);
  ctx.quadraticCurveTo(x - r + 2, hy + r - 1, x - r + 1, hy + 1);
  ctx.fill();
  face(ctx, x, hy - 1, p, { eyeGap: 5, mouthDy: 7.5, animal: true });
  ctx.fillStyle = '#1d1d22';
  ctx.beginPath(); ctx.ellipse(x, hy + 4.6, 1.7, 1.2, 0, 0, Math.PI * 2); ctx.fill();
}

function headRobot(ctx, hx, hy, r, t, p, view) {
  const blink = Math.floor((typeof performance !== 'undefined' ? performance.now() : 0) / 600) % 2;
  // antenna
  seg(ctx, hx, hy - r, hx + t * 0.3, hy - r - 5, C.furShade, 1.2);
  ctx.fillStyle = blink ? '#ff5d6c' : '#b8323f';
  ctx.beginPath(); ctx.arc(hx + t * 0.3, hy - r - 6, 1.9, 0, Math.PI * 2); ctx.fill();
  // ear bolts
  for (const side of view === 'side' ? [-1] : [-1, 1]) {
    ctx.fillStyle = C.outline; ctx.beginPath(); ctx.arc(hx + side * (r + 0.2), hy + 1, 2.8, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = C.furShade; ctx.beginPath(); ctx.arc(hx + side * (r + 0.2), hy + 1, 1.9, 0, Math.PI * 2); ctx.fill();
  }
  ctx.fillStyle = C.outline;
  roundRect(ctx, hx - r - 0.8, hy - r + 0.5, (r + 0.8) * 2, r * 2 - 1, 6); ctx.fill();
  ctx.fillStyle = C.fur;
  roundRect(ctx, hx - r + 0.2, hy - r + 1.5, r * 2 - 0.4, r * 2 - 3, 5.2); ctx.fill();
  if (view === 'back') {
    ctx.strokeStyle = C.furShade; ctx.lineWidth = 0.9;
    for (let i = -1; i <= 1; i++) { ctx.beginPath(); ctx.moveTo(hx - 5, hy + 2 + i * 2.5); ctx.lineTo(hx + 5, hy + 2 + i * 2.5); ctx.stroke(); }
    return;
  }
  const x = hx + t;
  ctx.fillStyle = '#1c2330';
  roundRect(ctx, x - 9, hy - 4.5, 18, 9.5, 3.5); ctx.fill();
  // LED eyes and mouth
  ctx.save();
  ctx.shadowColor = C.led; ctx.shadowBlur = 3;
  ctx.fillStyle = C.led; ctx.strokeStyle = C.led; ctx.lineWidth = 1.3;
  for (const side of [-1, 1]) {
    const ex = x + side * 4.3 + p.pupil.x * 0.6;
    const ey = hy + p.pupil.y * 0.5;
    const kind = p.eyes === 'wink' && side > 0 ? 'happy' : p.eyes;
    if (kind === 'happy') { ctx.beginPath(); ctx.moveTo(ex - 2, ey + 1); ctx.quadraticCurveTo(ex, ey - 2.2, ex + 2, ey + 1); ctx.stroke(); }
    else if (kind === 'closed' || p.eyeOpen < 0.2) { ctx.beginPath(); ctx.moveTo(ex - 2, ey); ctx.lineTo(ex + 2, ey); ctx.stroke(); }
    else if (kind === 'dizzy') { ctx.beginPath(); ctx.moveTo(ex - 1.8, ey - 1.8); ctx.lineTo(ex + 1.8, ey + 1.8); ctx.moveTo(ex + 1.8, ey - 1.8); ctx.lineTo(ex - 1.8, ey + 1.8); ctx.stroke(); }
    else { const w = kind === 'wide' ? 2.2 : 1.6; roundRect(ctx, ex - w, ey - w * 1.3 * p.eyeOpen, w * 2, w * 2.6 * p.eyeOpen, 0.8); ctx.fill(); }
  }
  ctx.restore();
  ctx.strokeStyle = C.led; ctx.lineWidth = 1.2;
  const my = hy + 7.6;
  ctx.beginPath();
  if (p.mouth === 'open' || p.mouth === 'laugh' || p.mouth === 'yawn' || p.mouth === 'talk') { roundRect(ctx, x - 2.4, my - 1, 4.8, 2.4, 0.8); ctx.stroke(); }
  else if (p.mouth === 'frown') { ctx.moveTo(x - 2.6, my + 1); ctx.quadraticCurveTo(x, my - 1.2, x + 2.6, my + 1); ctx.stroke(); }
  else if (p.mouth === 'flat' || p.mouth === 'o' || p.mouth === 'whistle') { ctx.moveTo(x - 2, my); ctx.lineTo(x + 2, my); ctx.stroke(); }
  else { ctx.moveTo(x - 3, my - 0.6); ctx.quadraticCurveTo(x, my + 2, x + 3, my - 0.6); ctx.stroke(); }
}

function drawEyes(ctx, x, y, p, gap = 4.6) {
  const kinds = p.eyes === 'wink' ? ['open', 'happy'] : [p.eyes, p.eyes];
  drawEye(ctx, x - gap, y, kinds[0], p);
  drawEye(ctx, x + gap, y, kinds[1], p);
}

function drawEye(ctx, x, y, kind, p) {
  ctx.strokeStyle = C.eyeLine;
  ctx.fillStyle = C.pupil;
  ctx.lineWidth = 1.4;
  const open = kind === 'sleepy' ? Math.min(p.eyeOpen, 0.45) : p.eyeOpen;
  if (kind === 'closed' || open < 0.18) {
    ctx.beginPath(); ctx.moveTo(x - 2.4, y); ctx.quadraticCurveTo(x, y + 1.6, x + 2.4, y); ctx.stroke();
    return;
  }
  if (kind === 'happy') {
    ctx.beginPath(); ctx.moveTo(x - 2.4, y + 1); ctx.quadraticCurveTo(x, y - 2.4, x + 2.4, y + 1); ctx.stroke();
    return;
  }
  if (kind === 'dizzy') {
    ctx.beginPath();
    for (let a = 0; a < Math.PI * 4; a += 0.4) {
      const rr = a * 0.2;
      const px = x + Math.cos(a + (p.fx.stars || 0)) * rr;
      const py = y + Math.sin(a + (p.fx.stars || 0)) * rr;
      a === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
    }
    ctx.stroke();
    return;
  }
  const wide = kind === 'wide';
  const px = x + p.pupil.x * 1.0;
  const py = y + p.pupil.y * 0.9;
  if (C.iris) {
    // Animal eyes: coloured iris, slit/round pupil, highlight.
    const rx = wide ? 2.9 : 2.4;
    const ry = (wide ? 3.4 : 2.9) * open;
    ctx.fillStyle = C.iris;
    ctx.beginPath(); ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = C.pupil;
    ctx.beginPath(); ctx.ellipse(px, py, CH.head === 'cat' ? 0.9 : 1.5, Math.min(ry, 2.4), 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.beginPath(); ctx.arc(px - 0.7, py - 1 * open, 0.6, 0, Math.PI * 2); ctx.fill();
    return;
  }
  const rx = wide ? 2.6 : 1.9;
  const ry = (wide ? 3.3 : 2.7) * open;
  if (wide) {
    ctx.fillStyle = C.white;
    ctx.beginPath(); ctx.ellipse(x, y, rx + 0.9, ry + 0.9, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.fillStyle = C.pupil;
  }
  ctx.beginPath(); ctx.ellipse(px, py, wide ? 1.5 : rx, wide ? 1.7 * open : ry, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = C.white;
  ctx.beginPath(); ctx.arc(px - 0.6, py - 0.9 * open, 0.65, 0, Math.PI * 2); ctx.fill();
  if (kind === 'sleepy') {
    ctx.fillStyle = C.skin;
    ctx.fillRect(x - 3, y - 4, 6, 3.6 - ry);
    ctx.beginPath(); ctx.moveTo(x - 2.6, y - ry + 0.3); ctx.lineTo(x + 2.6, y - ry + 0.3); ctx.stroke();
  }
}

function drawBrows(ctx, x, y, brows) {
  ctx.strokeStyle = C.brow;
  ctx.lineWidth = 1.3;
  const shapes = {
    neutral: [[-6.4, 0.2, -3, -0.4], [3, -0.4, 6.4, 0.2]],
    up: [[-6.4, -1.6, -3, -2.4], [3, -2.4, 6.4, -1.6]],
    angry: [[-6.4, -1, -3, 1], [3, 1, 6.4, -1]],
    sad: [[-6.4, 1, -3, -1], [3, -1, 6.4, 1]],
    raised: [[-6.4, 0.3, -3, 0.1], [3, -2.6, 6.4, -1.6]],
  };
  if (CH.head !== 'human' && brows === 'neutral') return; // animals only show brows when emoting
  for (const [x0, y0, x1, y1] of shapes[brows] || shapes.neutral) {
    ctx.beginPath(); ctx.moveTo(x + x0, y + y0); ctx.lineTo(x + x1, y + y1); ctx.stroke();
  }
}

function drawMouth(ctx, x, y, mouth, animal) {
  ctx.strokeStyle = C.outline;
  ctx.fillStyle = C.mouth;
  ctx.lineWidth = 1.2;
  const fillPath = (fn) => { ctx.beginPath(); fn(); ctx.closePath(); ctx.fill(); ctx.stroke(); };
  switch (mouth) {
    case 'grin':
      fillPath(() => { ctx.moveTo(x - 3.4, y - 0.8); ctx.quadraticCurveTo(x, y + 4.4, x + 3.4, y - 0.8); });
      ctx.fillStyle = C.white;
      ctx.fillRect(x - 2.6, y - 0.6, 5.2, 1.1);
      break;
    case 'laugh':
      fillPath(() => { ctx.moveTo(x - 3.8, y - 1.2); ctx.quadraticCurveTo(x, y + 6, x + 3.8, y - 1.2); });
      ctx.fillStyle = C.tongue;
      ctx.beginPath(); ctx.ellipse(x, y + 2.4, 1.8, 1, 0, 0, Math.PI * 2); ctx.fill();
      break;
    case 'open':
    case 'talk':
      fillPath(() => ctx.ellipse(x, y + 0.8, 2.2, mouth === 'talk' ? 1.4 : 2.2, 0, 0, Math.PI * 2));
      break;
    case 'yawn':
      fillPath(() => ctx.ellipse(x, y + 1.4, 2.4, 3.4, 0, 0, Math.PI * 2));
      break;
    case 'o':
      fillPath(() => ctx.arc(x, y + 0.5, 1.3, 0, Math.PI * 2));
      break;
    case 'whistle':
      fillPath(() => ctx.arc(x + 1.2, y + 0.4, 0.9, 0, Math.PI * 2));
      break;
    case 'flat':
      ctx.beginPath(); ctx.moveTo(x - 2.4, y); ctx.lineTo(x + 2.4, y); ctx.stroke();
      break;
    case 'frown':
      ctx.beginPath(); ctx.moveTo(x - 2.8, y + 1.4); ctx.quadraticCurveTo(x, y - 1.6, x + 2.8, y + 1.4); ctx.stroke();
      break;
    case 'tongue':
      ctx.beginPath(); ctx.moveTo(x - 3, y - 0.4); ctx.quadraticCurveTo(x, y + 2.4, x + 3, y - 0.4); ctx.stroke();
      ctx.fillStyle = C.tongue;
      ctx.beginPath(); ctx.ellipse(x + 1, y + 1.8, 1.4, 1.6, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      break;
    default: // smile -- a little "w" for the animals
      if (animal) {
        ctx.beginPath();
        ctx.moveTo(x - 2.8, y - 0.6); ctx.quadraticCurveTo(x - 1.4, y + 1.6, x, y - 0.2);
        ctx.quadraticCurveTo(x + 1.4, y + 1.6, x + 2.8, y - 0.6); ctx.stroke();
      } else {
        ctx.beginPath(); ctx.moveTo(x - 3, y - 0.4); ctx.quadraticCurveTo(x, y + 2.6, x + 3, y - 0.4); ctx.stroke();
      }
  }
}

function drawSnot(ctx, x, y, phase) {
  const r = 0.8 + 2.4 * (0.5 + 0.5 * Math.sin(phase));
  ctx.fillStyle = 'rgba(190,230,255,0.55)';
  ctx.strokeStyle = 'rgba(255,255,255,0.85)';
  ctx.lineWidth = 0.6;
  ctx.beginPath(); ctx.arc(x + r * 0.6, y + r * 0.4, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
}

function drawSweat(ctx, x, y, amount) {
  ctx.globalAlpha = Math.min(1, amount);
  ctx.fillStyle = C.sweat;
  ctx.strokeStyle = C.outline;
  ctx.lineWidth = 0.7;
  ctx.beginPath();
  ctx.moveTo(x, y - 3);
  ctx.quadraticCurveTo(x + 2.2, y + 0.5, x, y + 1.6);
  ctx.quadraticCurveTo(x - 2.2, y + 0.5, x, y - 3);
  ctx.fill(); ctx.stroke();
  ctx.globalAlpha = 1;
}

function drawHeadphonesBack(ctx, hx, hy, r) {
  ctx.strokeStyle = C.outline;
  ctx.lineWidth = 3.4;
  ctx.beginPath(); ctx.arc(hx, hy - 1, r + 1.6, Math.PI * 1.08, Math.PI * 1.92); ctx.stroke();
  ctx.strokeStyle = C.prop;
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(hx, hy - 1, r + 1.6, Math.PI * 1.08, Math.PI * 1.92); ctx.stroke();
}

function drawHeadphonesFront(ctx, hx, hy, r, t, mic) {
  for (const side of [-1, 1]) {
    const x = hx + side * (r - 0.5) + t * (side > 0 ? 0.2 : 0.4);
    ctx.fillStyle = C.outline;
    roundRect(ctx, x - 2.6, hy - 2.5, 5.2, 8, 2); ctx.fill();
    ctx.fillStyle = side > 0 ? '#c3a6ff' : C.prop;
    roundRect(ctx, x - 1.8, hy - 1.7, 3.6, 6.4, 1.5); ctx.fill();
  }
  if (mic) {
    ctx.strokeStyle = C.prop;
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(hx + r - 1, hy + 4); ctx.quadraticCurveTo(hx + r - 2, hy + 9, hx + t + 3, hy + 8.5); ctx.stroke();
    ctx.fillStyle = C.prop;
    ctx.beginPath(); ctx.arc(hx + t + 3, hy + 8.5, 1.3, 0, Math.PI * 2); ctx.fill();
  }
}

// --- props -----------------------------------------------------------------

function drawLaptop(ctx, cx, hipY, p) {
  const bx = cx + 5;
  const by = hipY - 1;
  ctx.fillStyle = C.outline;
  ctx.beginPath(); ctx.moveTo(bx - 2, by); ctx.lineTo(bx + 16, by); ctx.lineTo(bx + 14, by + 2.4); ctx.lineTo(bx - 4, by + 2.4); ctx.closePath(); ctx.fill();
  ctx.fillStyle = C.outline;
  roundRect(ctx, bx + 6, by - 13, 11, 13.5, 1.5); ctx.fill();
  ctx.fillStyle = C.propLight;
  roundRect(ctx, bx + 7, by - 12, 9, 11.5, 1); ctx.fill();
  ctx.fillStyle = '#9fd4ff';
  ctx.beginPath(); ctx.arc(bx + 11.5, by - 6.5, 1.4, 0, Math.PI * 2); ctx.fill();
  const glow = ctx.createRadialGradient(bx + 5, by - 7, 0, bx + 5, by - 7, 10);
  glow.addColorStop(0, `rgba(159,212,255,${0.25 + 0.1 * Math.sin((p.fx.typing || 0) * 3)})`);
  glow.addColorStop(1, 'rgba(159,212,255,0)');
  ctx.fillStyle = glow;
  ctx.fillRect(bx - 6, by - 17, 16, 18);
}

function drawPhone(ctx, hand) {
  ctx.save();
  ctx.translate(hand.x + 0.5, hand.y - 2.5);
  ctx.fillStyle = C.outline;
  roundRect(ctx, -2.4, -4, 4.8, 7.6, 1.2); ctx.fill();
  ctx.fillStyle = C.screen;
  roundRect(ctx, -1.6, -3.2, 3.2, 5.8, 0.6); ctx.fill();
  ctx.restore();
}

function drawMug(ctx, hand, steam) {
  ctx.save();
  ctx.translate(hand.x + 1.5, hand.y - 2.5);
  ctx.fillStyle = C.outline;
  roundRect(ctx, -2.8, -3.4, 5.6, 6.4, 1); ctx.fill();
  ctx.strokeStyle = C.outline;
  ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.arc(3, -0.4, 1.6, -Math.PI / 2, Math.PI / 2); ctx.stroke();
  ctx.fillStyle = C.mug;
  roundRect(ctx, -2, -2.6, 4, 4.8, 0.8); ctx.fill();
  ctx.fillStyle = C.coffee;
  ctx.fillRect(-2, -2.6, 4, 1);
  if (steam) {
    ctx.strokeStyle = 'rgba(255,255,255,0.7)';
    ctx.lineWidth = 0.7;
    for (const dx of [-1, 1]) {
      ctx.beginPath();
      ctx.moveTo(dx, -4);
      ctx.bezierCurveTo(dx + 1.5 * Math.sin(steam), -6, dx - 1.5 * Math.sin(steam), -7.5, dx, -9);
      ctx.stroke();
    }
  }
  ctx.restore();
}

// --- effects ---------------------------------------------------------------

function glyph(ctx, text, x, y, size, color, alpha = 1) {
  ctx.globalAlpha = alpha;
  ctx.font = `bold ${size}px -apple-system, "Helvetica Neue", sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineWidth = 2;
  ctx.strokeStyle = MAN_PALETTE.outline;
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
  ctx.globalAlpha = 1;
}

function star(ctx, x, y, r) {
  ctx.beginPath();
  for (let i = 0; i < 10; i++) {
    const a = (i * Math.PI) / 5 - Math.PI / 2;
    const rr = i % 2 ? r * 0.45 : r;
    ctx.lineTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr);
  }
  ctx.closePath();
  ctx.fillStyle = MAN_PALETTE.star;
  ctx.strokeStyle = MAN_PALETTE.outline;
  ctx.lineWidth = 0.7;
  ctx.fill(); ctx.stroke();
}

function heart(ctx, x, y, r) {
  ctx.beginPath();
  ctx.moveTo(x, y + r);
  ctx.bezierCurveTo(x - r * 1.6, y - r * 0.2, x - r * 0.6, y - r * 1.4, x, y - r * 0.4);
  ctx.bezierCurveTo(x + r * 0.6, y - r * 1.4, x + r * 1.6, y - r * 0.2, x, y + r);
  ctx.fillStyle = MAN_PALETTE.heart;
  ctx.strokeStyle = MAN_PALETTE.outline;
  ctx.lineWidth = 0.7;
  ctx.fill(); ctx.stroke();
}

function drawFx(ctx, s, top, p) {
  const f = p.fx;
  ctx.save();
  ctx.scale(s, s);
  if (f.zzz !== undefined) {
    for (let i = 0; i < 3; i++) {
      const ph = (f.zzz / 2.4 + i / 3) % 1;
      glyph(ctx, 'Z', top.x + 8 + ph * 10, top.y + 4 - ph * 16, 5 + ph * 4, MAN_PALETTE.fx, 1 - ph);
    }
  }
  if (f.stars !== undefined) {
    for (let i = 0; i < 3; i++) {
      const a = f.stars * 2.5 + (i * Math.PI * 2) / 3;
      star(ctx, top.x + Math.cos(a) * 10, top.y + 1 + Math.sin(a) * 2.6, 2.4);
    }
  }
  if (f.heart !== undefined) heart(ctx, top.x + 9, top.y - 2 - f.heart * 8, 2.8);
  if (f.exclaim) glyph(ctx, '!', top.x + 10, top.y - 2, 11, '#ffd54a', Math.min(1, f.exclaim));
  if (f.question) glyph(ctx, '?', top.x + 10, top.y - 2, 11, '#9fd4ff', Math.min(1, f.question));
  if (f.bulb) {
    const x = top.x + 11;
    const y = top.y - 3;
    ctx.globalAlpha = Math.min(1, f.bulb);
    ctx.fillStyle = 'rgba(255,230,120,0.35)';
    ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#ffe066';
    ctx.strokeStyle = MAN_PALETTE.outline;
    ctx.lineWidth = 0.8;
    ctx.beginPath(); ctx.arc(x, y - 0.5, 3.2, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.fillStyle = MAN_PALETTE.propLight;
    ctx.fillRect(x - 1.5, y + 2.4, 3, 2);
    ctx.globalAlpha = 1;
  }
  if (f.notes !== undefined) {
    for (let i = 0; i < 2; i++) {
      const ph = (f.notes / 2 + i / 2) % 1;
      glyph(ctx, i ? '♫' : '♪', top.x - 10 + i * 20 + Math.sin(ph * 6) * 2, top.y + 6 - ph * 14, 7, MAN_PALETTE.note, 1 - ph);
    }
  }
  if (f.sparkle !== undefined) {
    for (let i = 0; i < 4; i++) {
      const a = (i * Math.PI) / 2 + f.sparkle;
      star(ctx, top.x + Math.cos(a) * 16, top.y + 14 + Math.sin(a) * 12, 1.6 + Math.sin(f.sparkle * 3 + i));
    }
  }
  if (f.speed) {
    ctx.strokeStyle = 'rgba(255,255,255,0.7)';
    ctx.lineWidth = 1;
    for (let i = 0; i < 3; i++) {
      const y = top.y + 26 + i * 9;
      const x = top.x - p.facing * (16 + ((f.speed * 40 + i * 7) % 8));
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - p.facing * 9, y); ctx.stroke();
    }
  }
  ctx.restore();
}

if (typeof module !== 'undefined') {
  module.exports = { drawMan, MAN_PALETTE, PET_CHARACTERS };
}
