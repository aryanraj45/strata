/* STRATA — twenty thousand points that keep changing their mind.
 *
 * The whole argument of the project is one image: the money symbol becoming a
 * person. So that is what the hero does. Every particle carries five positions
 * and the scroll decides which pair it is between:
 *
 *   0  ₿              the ledger. Public, permanent, and anonymous.
 *   1  a fingerprint  the same points, reformed. This is the claim.
 *   2  clusters       addresses resolved into entities
 *   3  a chain        a balance shedding a slice at every hop
 *   4  one core       the attributed lead
 *
 * The morph runs on the GPU: five position attributes and a single uniform
 * walking 0 -> 4, so nothing is recomputed on the main thread while you scroll.
 * The two flat shapes are sampled out of a 2D canvas at boot, which keeps the
 * glyph and the ridges honest without shipping a single asset.
 */

import * as THREE from './vendor/three.module.js';

const canvas = document.getElementById('world');
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

let renderer;
try {
  renderer = new THREE.WebGLRenderer({
    canvas, antialias: true, alpha: true, powerPreference: 'high-performance',
  });
} catch (err) {
  canvas.style.display = 'none';   // the page reads fine without any of this
  throw err;
}
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight, false);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(48, innerWidth / innerHeight, 1, 8000);

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const smooth = (t) => t * t * (3 - 2 * t);
const ramp = (v, a, b) => smooth(clamp01((v - a) / (b - a)));
const lerp = (a, b, t) => a + (b - a) * t;

const N = 20000;          // particles
const SPAN = 780;         // world size of the flat shapes

// --------------------------------------------------------------- sampling
// Draw a shape to a canvas, then scatter particles across the pixels it lit.
// Sampling the drawing rather than hand-placing points means the ₿ is a real
// glyph and the ridges are real curves, at whatever density we ask for.
function sampleShape(draw, count) {
  const S = 700;
  const c = document.createElement('canvas');
  c.width = c.height = S;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.clearRect(0, 0, S, S);
  g.fillStyle = '#fff';
  g.strokeStyle = '#fff';
  draw(g, S);

  const data = g.getImageData(0, 0, S, S).data;
  const lit = [];
  for (let y = 0; y < S; y++) {
    for (let x = 0; x < S; x++) {
      if (data[(y * S + x) * 4 + 3] > 90) lit.push(x, y);
    }
  }

  const out = new Float32Array(count * 3);
  const hits = lit.length / 2;
  for (let i = 0; i < count; i++) {
    const k = ((Math.random() * hits) | 0) * 2;
    // jitter inside the pixel so density reads smooth rather than gridded
    const x = (lit[k] + Math.random()) / S - 0.5;
    const y = (lit[k + 1] + Math.random()) / S - 0.5;
    out[i * 3] = x * SPAN;
    out[i * 3 + 1] = -y * SPAN;
    out[i * 3 + 2] = (Math.random() - 0.5) * 9;    // barely any depth: these read as flat plates
  }
  return out;
}

// ---- 0. the money -------------------------------------------------------
function drawBitcoin(g, S) {
  g.font = 'bold 470px "Times New Roman", Georgia, serif';
  g.textAlign = 'center';
  g.textBaseline = 'alphabetic';
  g.fillText('B', S / 2 + 6, S / 2 + 168);
  g.lineWidth = 30;
  g.lineCap = 'butt';
  for (const ox of [-66, 60]) {
    g.beginPath();
    g.moveTo(S / 2 + ox, S / 2 - 210);
    g.lineTo(S / 2 + ox, S / 2 + 210);
    g.stroke();
  }
}

// ---- 1. the person ------------------------------------------------------
// A whorl: nested closed curves, each one wobbled a little differently and
// drifting off-centre, which is what stops it reading as a target and starts it
// reading as a print.
function drawFingerprint(g, S) {
  const cx = S / 2, cy = S / 2 + 10;
  g.lineWidth = 4.2;
  for (let i = 0; i < 26; i++) {
    const r0 = 18 + i * 12.6;
    const ox = Math.sin(i * 0.17) * 11;
    const oy = Math.cos(i * 0.11) * 15 - i * 0.7;
    g.beginPath();
    for (let a = 0; a <= Math.PI * 2 + 0.02; a += 0.02) {
      const wob = Math.sin(a * 2 + i * 0.23) * 0.15
                + Math.sin(a * 3 - i * 0.12) * 0.07
                + Math.sin(a * 5 + i * 0.4) * 0.03;
      const r = r0 * (1 + wob);
      const x = cx + ox + Math.cos(a) * r * 0.80;
      const y = cy + oy + Math.sin(a) * r * 1.06;
      if (a === 0) g.moveTo(x, y); else g.lineTo(x, y);
    }
    g.stroke();
  }
  // ridge endings and bifurcations -- the minutiae an examiner actually marks
  for (let i = 0; i < 26; i++) {
    const a = Math.random() * Math.PI * 2;
    const r = 40 + Math.random() * 250;
    g.beginPath();
    g.arc(cx + Math.cos(a) * r * 0.8, cy + Math.sin(a) * r * 1.05, 5, 0, Math.PI * 2);
    g.fill();
  }
}

const shapeGlyph = sampleShape(drawBitcoin, N);
const shapePrint = sampleShape(drawFingerprint, N);

// ---- 2. clusters --------------------------------------------------------
const clusterOf = new Int16Array(N);
const shapeClusters = (() => {
  const out = new Float32Array(N * 3);
  const K = 18;
  const cs = [];
  for (let k = 0; k < K; k++) {
    cs.push([
      (Math.random() - 0.5) * 1150,
      (Math.random() - 0.5) * 620,
      (Math.random() - 0.5) * 520,
    ]);
  }
  const g1 = () => (Math.random() + Math.random() + Math.random() - 1.5) * 92;
  for (let i = 0; i < N; i++) {
    const k = (Math.random() * K) | 0;
    clusterOf[i] = k;
    const c = cs[k];
    out[i * 3] = c[0] + g1();
    out[i * 3 + 1] = c[1] + g1();
    out[i * 3 + 2] = c[2] + g1();
  }
  return out;
})();

// ---- 3. the peeling chain ----------------------------------------------
const shapeChain = (() => {
  const out = new Float32Array(N * 3);
  const HOPS = 8;
  for (let i = 0; i < N; i++) {
    const h = (Math.random() * HOPS) | 0;
    const r = 132 * Math.pow(0.86, h) * (0.86 + Math.random() * 0.16);
    const a = Math.random() * Math.PI * 2;
    out[i * 3] = -560 + h * 158 + Math.cos(a) * r;
    out[i * 3 + 1] = 200 - h * 50 + Math.sin(a) * r;
    out[i * 3 + 2] = (Math.random() - 0.5) * 70 - h * 34;
  }
  return out;
})();

// ---- 4. one lead --------------------------------------------------------
const shapeCore = (() => {
  const out = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {
    const u = Math.random() * 2 - 1;
    const a = Math.random() * Math.PI * 2;
    const r = 46 * Math.cbrt(Math.random());
    const s = Math.sqrt(1 - u * u);
    out[i * 3] = Math.cos(a) * s * r;
    out[i * 3 + 1] = Math.sin(a) * s * r;
    out[i * 3 + 2] = u * r;
  }
  return out;
})();

// ----------------------------------------------------------------- points

const geo = new THREE.BufferGeometry();
geo.setAttribute('position', new THREE.BufferAttribute(shapeGlyph.slice(), 3));
geo.setAttribute('s0', new THREE.BufferAttribute(shapeGlyph, 3));
geo.setAttribute('s1', new THREE.BufferAttribute(shapePrint, 3));
geo.setAttribute('s2', new THREE.BufferAttribute(shapeClusters, 3));
geo.setAttribute('s3', new THREE.BufferAttribute(shapeChain, 3));
geo.setAttribute('s4', new THREE.BufferAttribute(shapeCore, 3));

const seeds = new Float32Array(N);
const tints = new Float32Array(N);
for (let i = 0; i < N; i++) {
  seeds[i] = Math.random();
  tints[i] = Math.random();
}
geo.setAttribute('seed', new THREE.BufferAttribute(seeds, 1));
geo.setAttribute('tint', new THREE.BufferAttribute(tints, 1));
geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1800);

const uniforms = {
  uSeg: { value: 0 },        // 0..4, which pair of shapes we are between
  uTime: { value: 0 },
  uSize: { value: 1 },
  uOpacity: { value: 1 },
  uBurn: { value: 0 },       // the last beat: everything goes white-hot
  uMouse: { value: new THREE.Vector3(9e9, 9e9, 0) },   // in the rig's own space
  uPush: { value: 0 },       // how hard the cursor shoves the cloud
  uScanY: { value: 9e9 },    // the reader bar sweeping the print
  uScanK: { value: 0 },
};

const mat = new THREE.ShaderMaterial({
  uniforms,
  transparent: true,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
  vertexShader: `
    attribute vec3 s0; attribute vec3 s1; attribute vec3 s2;
    attribute vec3 s3; attribute vec3 s4;
    attribute float seed; attribute float tint;
    uniform float uSeg; uniform float uTime; uniform float uSize;
    uniform vec3 uMouse; uniform float uPush;
    uniform float uScanY; uniform float uScanK;
    varying float vT; varying float vGlow; varying float vHot;

    void main() {
      // Sequential morph. Each stage is a clamped slice of uSeg, so a particle
      // is only ever interpolating between two shapes at a time.
      vec3 p = s0;
      float a = clamp(uSeg - 0.0, 0.0, 1.0);
      float b = clamp(uSeg - 1.0, 0.0, 1.0);
      float c = clamp(uSeg - 2.0, 0.0, 1.0);
      float d = clamp(uSeg - 3.0, 0.0, 1.0);
      p = mix(p, s1, a);
      p = mix(p, s2, b);
      p = mix(p, s3, c);
      p = mix(p, s4, d);

      // Mid-transition the cloud blooms outward, so the change reads as a
      // dissolve and re-form rather than points sliding along straight lines.
      float f = fract(uSeg);
      float bloom = sin(f * 3.14159) * step(0.001, uSeg) * (1.0 - step(3.999, uSeg));
      float ang = seed * 6.2831 + uTime * 0.25;
      p += vec3(cos(ang), sin(ang), sin(ang * 1.7)) * bloom * (70.0 + seed * 150.0);

      // a slow drift so a settled shape is never completely still
      p += vec3(
        sin(uTime * 0.35 + seed * 9.0),
        cos(uTime * 0.31 + seed * 7.0),
        sin(uTime * 0.27 + seed * 5.0)
      ) * 3.5;

      // A shockwave rides out from the centre on every change of shape, so the
      // transition lands as an event rather than a crossfade.
      float ringR = f * 980.0;
      float d0 = length(p.xy);
      float ring = exp(-pow((d0 - ringR) / 80.0, 2.0)) * bloom;
      p.z += ring * 110.0;

      // The cursor shoves the cloud aside. Falls off fast so it feels like a
      // hand in water rather than the whole shape lurching.
      vec2 toM = p.xy - uMouse.xy;
      float dM = length(toM) + 0.001;
      p.xy += (toM / dM) * uPush * exp(-(dM * dM) / (250.0 * 250.0)) * 150.0;

      // A reader bar sweeping the print, the way a biometric scanner does.
      float scan = exp(-pow((p.y - uScanY) / 46.0, 2.0)) * uScanK;

      vT = tint;
      vGlow = bloom;
      vHot = scan + ring * 0.85;

      vec4 mv = modelViewMatrix * vec4(p, 1.0);
      gl_PointSize = uSize * (2.6 + seed * 3.2) * (1.0 + vHot * 2.2) * (1150.0 / max(-mv.z, 1.0));
      gl_Position = projectionMatrix * mv;
    }
  `,
  fragmentShader: `
    uniform float uOpacity; uniform float uBurn;
    varying float vT; varying float vGlow; varying float vHot;
    void main() {
      float d = length(gl_PointCoord - 0.5);
      if (d > 0.5) discard;
      // a tight core plus a wide soft skirt, which is what reads as glow
      float alpha = smoothstep(0.5, 0.04, d) + smoothstep(0.5, 0.0, d) * 0.32;

      vec3 amber = vec3(0.878, 0.635, 0.290);
      vec3 cream = vec3(0.949, 0.937, 0.914);
      vec3 col = mix(amber, cream, vT * 0.75);
      col = mix(col, vec3(1.0, 0.86, 0.62), vGlow * 0.8);   // hot while in flight
      col = mix(col, vec3(1.0, 0.95, 0.85), uBurn);
      col = mix(col, vec3(1.0), clamp(vHot, 0.0, 1.0) * 0.92);   // under the reader bar

      gl_FragColor = vec4(col, alpha * uOpacity * (1.0 + vHot * 0.9));
    }
  `,
});

const rig = new THREE.Group();
scene.add(rig);
const cloud = new THREE.Points(geo, mat);
cloud.frustumCulled = false;
rig.add(cloud);

// ---------------------------------------------------------------- driving

const mouse = { x: 0, y: 0, tx: 0, ty: 0 };
let pointerSeen = false;    // until the cursor actually moves it is nowhere,
                            // not at the origin punching a hole in the shape
addEventListener('pointermove', (e) => {
  pointerSeen = true;
  mouse.tx = (e.clientX / innerWidth - 0.5) * 2;
  mouse.ty = (e.clientY / innerHeight - 0.5) * 2;
}, { passive: true });

let p = 0, targetP = 0;
function readScroll() {
  const max = document.documentElement.scrollHeight - innerHeight;
  targetP = max > 0 ? clamp01(scrollY / max) : 0;
}
addEventListener('scroll', readScroll, { passive: true });
addEventListener('resize', () => {
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  readScroll();
}, { passive: true });
readScroll();
p = targetP;

const clock = new THREE.Clock();
const _mw = new THREE.Vector3();
const _inv = new THREE.Matrix4();

function update(t) {
  // Four transitions, each owning its own stretch of the page. The first one
  // is the one that matters: the money becoming the person.
  // Each shape needs a hold, not just a hand-off. The fingerprint is the whole
  // pitch, so it gets the longest one -- roughly a fifth of the page -- rather
  // than the six percent it had when the ramps ran back to back.
  const seg = ramp(p, 0.05, 0.15)      // ₿        -> fingerprint
            + ramp(p, 0.36, 0.50)      // print    -> clusters   (holds 0.15-0.36)
            + ramp(p, 0.60, 0.74)      // clusters -> chain
            + ramp(p, 0.84, 0.97);     // chain    -> one core

  uniforms.uSeg.value = seg;
  uniforms.uTime.value = t;
  uniforms.uBurn.value = ramp(p, 0.86, 0.99);
  // Backed off while the page below is carrying real text, full strength at the
  // hero and at the very end where the copy is sparse.
  uniforms.uOpacity.value = lerp(1.0, 0.5, ramp(p, 0.44, 0.60))
                          + ramp(p, 0.86, 0.98) * 0.45;
  uniforms.uSize.value = lerp(1.0, 1.55, ramp(p, 0.84, 0.98));

  // The reader bar only runs while the print is up -- a scanner sweeping a
  // shape that is not a fingerprint would be nonsense.
  const printHold = ramp(p, 0.14, 0.19) * (1 - ramp(p, 0.34, 0.40));
  uniforms.uScanK.value = printHold * 1.35;
  // one pass every four seconds, bottom to top, with a pause between
  const sweep = (t % 4.0) / 2.2;
  uniforms.uScanY.value = sweep <= 1 ? lerp(-430, 430, sweep) : 9e9;

  // The flat shapes want to be square-on; the 3D ones want a little attitude.
  const flat = 1 - ramp(p, 0.42, 0.60);
  rig.rotation.y = lerp(0.0, 0.55, ramp(p, 0.42, 0.85)) * (1 - flat * 0.7)
                 + Math.sin(t * 0.12) * 0.05 * flat
                 + mouse.x * 0.22;
  rig.rotation.x = lerp(0.0, 0.34, ramp(p, 0.46, 0.75)) - mouse.y * 0.12;
  rig.position.y = lerp(-150, 20, ramp(p, 0.10, 0.55));

  camera.position.set(
    mouse.x * 70,
    -mouse.y * 55 + 25,
    lerp(1080, 1420, ramp(p, 0.28, 0.8)),
  );
  camera.lookAt(0, 0, 0);

  // The cursor pushes the cloud around. The particles live in the rig's local
  // space, so the pointer has to be carried back through the rig's transform
  // or the dent lands somewhere other than under the mouse.
  const halfH = Math.abs(camera.position.z) * Math.tan((48 * Math.PI / 180) / 2);
  _mw.set(mouse.tx * halfH * camera.aspect, -mouse.ty * halfH, 0);
  rig.updateMatrixWorld();
  _inv.copy(rig.matrixWorld).invert();
  uniforms.uMouse.value.copy(_mw.applyMatrix4(_inv));
  uniforms.uPush.value = pointerSeen ? 0.75 : 0.0;
}

function frame() {
  p += (targetP - p) * 0.075;
  mouse.x += (mouse.tx - mouse.x) * 0.05;
  mouse.y += (mouse.ty - mouse.y) * 0.05;
  update(clock.getElapsedTime());
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

if (reduced) {
  p = targetP;
  update(0);
  renderer.render(scene, camera);
} else {
  frame();
}
