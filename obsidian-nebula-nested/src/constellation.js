// Nebula Nested renderer. Forked from 7evyr/obsidian-nebula (MIT).
// A hub at the centre, one orbit ring per folder, sub-folders drawn as nested arcs on outer
// shells, every note a glowing node, real links as lines of light with beads running along them.
// Renderer only: no Obsidian imports.
import * as THREE from "three";
import { TrackballControls } from "three/examples/jsm/controls/TrackballControls.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { ringPoints, tiltPoint, cameraFit } from "./layout.js";

export const DEFAULTS = {
  // look
  color: "#2cf27f", glow: 0.38, glowSize: 0.3, nodeSize: 0.95, links: 1.15, rings: 1.0,
  beads: 420, beadSpeed: 1, comets: true, spin: 0.3, pulse: true, stars: true, labels: true,
  quality: "high", saveGpu: true,
  // structure (these change the layout, so the view rebuilds when they change)
  maxDepth: 3, maxRings: 120, showRootNotes: false, wedgeArcs: true, depthFade: true, spacing: 1,
  ringSpin: 0.55, coreStyle: "orb",
};

const SWATCHES = [["Green", "#2cf27f"], ["White", "#ffffff"], ["Ice", "#9fdcff"], ["Violet", "#b38cff"], ["Amber", "#ffc46b"], ["Rose", "#ff8fa3"]];
const QUALITY = { fast: 1, high: 1.75, max: 2.5 };
const MAX_BEADS = 800;
const ease = (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));

const POINT_VS = `
  attribute float aSize; attribute float aBoost; attribute float aPhase;
  uniform float uScale; uniform float uTime; uniform float uSizeMul; uniform float uPulse;
  varying float vBoost; varying float vFade;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    // two out-of-phase sines read as a twinkle instead of a breathing pulse
    float twinkle = sin(uTime * 1.1 + aPhase) * 0.5 + sin(uTime * 2.3 + aPhase * 1.7) * 0.5;
    float pulse = 1.0 + uPulse * (0.10 * twinkle + 0.06 * sin(uTime * 1.6 + aPhase));
    gl_PointSize = aSize * uSizeMul * pulse * (1.0 + aBoost * 0.9) * uScale / -mv.z;
    vBoost = aBoost; vFade = clamp(1.25 - (-mv.z) / 1200.0, 0.45, 1.0);
    gl_Position = projectionMatrix * mv;
  }`;
const POINT_FS = `
  uniform vec3 uColor; varying float vBoost; varying float vFade;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    if (d > 0.5) discard;
    float halo = pow(1.0 - d * 2.0, 1.7);
    float core = smoothstep(0.22, 0.0, d);
    float spike = 0.0;
    // a faint diffraction cross on the brighter nodes only (vBoost > 0), for a real star look
    if (vBoost > 0.05) {
      float ax = abs(gl_PointCoord.x - 0.5), ay = abs(gl_PointCoord.y - 0.5);
      spike = (smoothstep(0.02, 0.0, ax) + smoothstep(0.02, 0.0, ay)) * (1.0 - d * 2.0) * vBoost * 0.5;
    }
    vec3 col = uColor * (halo * 0.55) + vec3(1.0) * core * (0.3 + vBoost * 0.7) + vec3(1.0) * spike;
    gl_FragColor = vec4(col * vFade * (0.75 + vBoost * 0.8), (halo * 0.85 + spike) * vFade);
  }`;

export class Constellation {
  /**
   * @param {HTMLElement} el container
   * @param {{L:object, tree:object}} ctx  result of layout() and buildTree()
   * @param {{settings?:object, camera?:{pos:number[],target:number[]}, onOpen?:Function, onSettings?:Function, onRebuild?:Function, onFocus?:Function, force?:boolean}} opts
   */
  constructor(el, ctx, opts = {}) {
    this.el = el; this.opts = opts; this.L = ctx.L; this.tree = ctx.tree;
    this.s = Object.assign({}, DEFAULTS, opts.settings || {});
    this.reduced = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.t0 = performance.now(); this.running = true; this.hover = -1; this._last = 0; this.frames = 0; this.spinPause = 0;
    this.root = document.createElement("div"); this.root.className = "nebula-root"; this.root.style.setProperty("--nebula-color", this.s.color); el.appendChild(this.root);
    this._build(); this._ui(); this._events(); this.apply();
    this._loop = this._loop.bind(this); window.requestAnimationFrame(this._loop);
  }

  // ---------------------------------------------------------------- scene
  _build() {
    const L = this.L;
    const w = Math.max(this.el.clientWidth, 200), h = Math.max(this.el.clientHeight, 200);
    const r = (this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" }));
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, QUALITY[this.s.quality] || 1.75)); r.setSize(w, h); r.setClearColor(0x000000, 1);
    r.toneMapping = THREE.NoToneMapping; this.root.appendChild(r.domElement); r.domElement.className = "nebula-canvas";
    const scene = (this.scene = new THREE.Scene());
    const cam = (this.camera = new THREE.PerspectiveCamera(52, w / h, 0.5, 4000));
    const color = (this.color = new THREE.Color(this.s.color));

    // ---- nodes -------------------------------------------------------------
    const pos = L.pos, size = L.aSize, phase = L.phase, boost = new Float32Array(L.total);
    size[0] = 0; // core is a sprite, not a node
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    geo.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
    this.boostAttr = new THREE.BufferAttribute(boost, 1); geo.setAttribute("aBoost", this.boostAttr);
    this.uniforms = { uColor: { value: color }, uScale: { value: h * 0.62 }, uTime: { value: 0 }, uSizeMul: { value: 1 }, uPulse: { value: 1 } };
    this.points = new THREE.Points(geo, new THREE.ShaderMaterial({
      vertexShader: POINT_VS, fragmentShader: POINT_FS, uniforms: this.uniforms,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    }));
    this.points.frustumCulled = false; scene.add(this.points);
    this.raycaster = new THREE.Raycaster(); this.raycaster.params.Points.threshold = 1.1;

    // ---- rings: full circles on the innermost shell, arcs for nested folders ----
    const wedge = !!this.s.wedgeArcs;
    this.ringLines = []; this.ringPts = []; this.ringBase = []; this.ringLocal = []; this.ringArc = [];
    this.nodeLocal = L.rings.map((r) => L.localByRing.get(r.folder) || null);
    const maxR = L.far / 1.25;
    for (const ring of L.rings) {
      const closed = ring.level === 1 || !wedge;
      const seg = closed ? 240 : Math.max(24, Math.min(240, Math.round((ring.arc[1] - ring.arc[0]) / (Math.PI * 2) * 320)));
      if (closed) this.ringArc.push(null); else this.ringArc.push([ring.arc[0], ring.arc[1], seg]);
      const pts = closed ? ringPoints(ring, seg) : ringPoints(ring, seg, ring.arc);
      const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(pts, 3));
      const depth = L.depths.levels > 1 ? (ring.level - 1) / Math.max(1, L.depths.levels - 1) : 0;
      const fade = this.s.depthFade ? 1 - 0.55 * depth : 1;
      const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.26 * fade, blending: THREE.AdditiveBlending, depthWrite: false });
      const line = closed ? new THREE.LineLoop(g, mat) : new THREE.Line(g, mat);
      scene.add(line);
      this.ringLines.push(line);
      this.ringPts.push(pts);
      this.ringLocal.push(ring); // rebuild the world-space points when the ring spins
      this.ringBase.push(0.26 * fade);
    }

    // ---- links -------------------------------------------------------------
    this.adj = new Map();
    const edges = L.edges.slice(0, 9000);
    for (const [x, y] of edges) {
      if (!this.adj.has(x)) this.adj.set(x, []);
      if (!this.adj.has(y)) this.adj.set(y, []);
      this.adj.get(x).push(y); this.adj.get(y).push(x);
    }
    this.edges = edges;
    const lp = new Float32Array(edges.length * 6);
    edges.forEach(([x, y], i) => { lp.set(pos.subarray(x * 3, x * 3 + 3), i * 6); lp.set(pos.subarray(y * 3, y * 3 + 3), i * 6 + 3); });
    const lg = new THREE.BufferGeometry(); lg.setAttribute("position", new THREE.BufferAttribute(lp, 3));
    const lc = (this.linkFade = new Float32Array(lp.length / 3));
    for (let i = 0; i < lc.length; i += 2) {
      const dx = lp[i * 3] - lp[i * 3 + 3], dy = lp[i * 3 + 1] - lp[i * 3 + 4], dz = lp[i * 3 + 2] - lp[i * 3 + 5];
      const f = Math.min(1, Math.max(0.1, 1.3 - Math.sqrt(dx * dx + dy * dy + dz * dz) / (maxR * 0.5)));
      lc[i] = lc[i + 1] = f;
    }
    lg.setAttribute("color", new THREE.BufferAttribute(new Float32Array(lp.length), 3));
    this.linkGeo = lg; this._linkColors();
    this.linkMat = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.1, blending: THREE.AdditiveBlending, depthWrite: false });
    scene.add((this.links = new THREE.LineSegments(lg, this.linkMat)));

    // ---- hover neighbourhood lines ----------------------------------------
    this.hoverGeo = new THREE.BufferGeometry();
    this.hoverGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(600 * 6), 3));
    this.hoverGeo.setDrawRange(0, 0);
    this.hoverLines = new THREE.LineSegments(this.hoverGeo, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending, depthWrite: false }));
    this.hoverLines.frustumCulled = false; scene.add(this.hoverLines);

    // ---- beads along links -------------------------------------------------
    this.beads = [];
    const R2 = this._rng(3);
    for (let i = 0; i < MAX_BEADS; i++) this.beads.push({ e: edges.length ? edges[Math.floor(R2() * edges.length)] : [0, 0], t: R2(), s: 0.12 + R2() * 0.22 });
    const bg = new THREE.BufferGeometry(); bg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(MAX_BEADS * 3), 3));
    this.beadMat = new THREE.PointsMaterial({ map: this._dotTex(), color: 0xffffff, size: 2.4, sizeAttenuation: true, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false });
    this.beadPts = new THREE.Points(bg, this.beadMat); this.beadPts.frustumCulled = false; scene.add(this.beadPts);

    // ---- core --------------------------------------------------------------
    this.core = this._coreSprite(); this.core.scale.setScalar(this.coreSize); scene.add(this.core);
    this.coreSize = Math.max(9, L.coreRadius * 1.0);

    // ---- comets: one per ring, capped so huge vaults stay calm ------------
    this.comets = []; const TL = 44;
    const cometRings = L.rings.slice().sort((a, b) => b.descendants - a.descendants).slice(0, 18).map((r) => L.rings.indexOf(r));
    for (const i of cometRings) {
      const pts = this.ringPts[i];
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(TL * 3), 3));
      g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(TL * 3), 3));
      const line = new THREE.Line(g, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
      line.frustumCulled = false; scene.add(line);
      const R = this._rng(100 + i);
      this.comets.push({ line, lp: pts, ph: R(), sp: (0.02 + R() * 0.04) * (R() < 0.5 ? -1 : 1), TL, level: L.rings[i].level, ringIdx: i });
      (this.cometRing = this.cometRing || new Map()).set(this.comets[this.comets.length - 1], i);
    }

    // ---- halos round the core --------------------------------------------
    this.halos = [0.95, 1.35, 1.8].map((k, i) => {
      const seg = 128, a = new Float32Array(seg * 3);
      for (let s = 0; s < seg; s++) { const th = (s / seg) * Math.PI * 2; a.set([Math.cos(th), Math.sin(th), 0], s * 3); }
      const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(a, 3));
      const l = new THREE.LineLoop(g, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.55 - i * 0.12, blending: THREE.AdditiveBlending, depthWrite: false }));
      l.userData = { k, ax: new THREE.Vector3(Math.sin(i * 2.1), Math.cos(i * 1.3), Math.sin(i * 0.7 + 1)).normalize(), sp: 0.5 + i * 0.35 };
      l.rotation.set(i * 0.9, i * 1.7, 0); scene.add(l); return l;
    });

    // ---- background dust ---------------------------------------------------
    const dust = new Float32Array(1800 * 3), R3 = this._rng(11);
    for (let i = 0; i < 1800; i++) {
      const rr = maxR * (0.7 + R3() * 2.6), a = R3() * 6.283, b = Math.acos(2 * R3() - 1);
      dust.set([rr * Math.sin(b) * Math.cos(a), rr * Math.cos(b), rr * Math.sin(b) * Math.sin(a)], i * 3);
    }
    const dg = new THREE.BufferGeometry(); dg.setAttribute("position", new THREE.BufferAttribute(dust, 3));
    this.dust = new THREE.Points(dg, new THREE.PointsMaterial({ map: this._dotTex(), color: 0x9a9aa4, size: 0.9, sizeAttenuation: true, transparent: true, opacity: 0.35, depthWrite: false }));
    scene.add(this.dust);

    // ---- post: bloom -------------------------------------------------------
    const comp = (this.composer = new EffectComposer(r));
    comp.addPass(new RenderPass(scene, cam));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(w, h), 0.5, 0.4, 0.4); comp.addPass(this.bloom);
    comp.addPass(new OutputPass()); comp.setSize(w, h);

    // ---- camera + trackball ------------------------------------------------
    const far = (this.far = L.far);
    this.sceneExtent = L.maxExtent || L.far;
    cam.far = far * 16; cam.updateProjectionMatrix();
    const c = (this.controls = new TrackballControls(cam, r.domElement));
    Object.assign(c, { rotateSpeed: 2.6, zoomSpeed: 1.1, panSpeed: 0.7, staticMoving: false, dynamicDampingFactor: 0.1, minDistance: 6, maxDistance: far * 7 });
    // Fit the camera to the scene's measured extent. cameraFit is pure and unit-tested, because
    // guessing a multiple of `far` is what cropped the outer rings.
    const { distance: dist } = cameraFit(L.maxExtent || far, cam.fov, cam.aspect);
    this.fitDistance = dist;
    // a gentle elevation so rings read as ellipses rather than circles
    const elev = 0.30;
    this.home = new THREE.Vector3(0, Math.sin(elev) * dist, Math.cos(elev) * dist);
    c.target.set(0, 0, 0);
    if (this.opts.camera && this.opts.camera.pos) {
      // coming back from a rebuild: keep the user where they were
      cam.position.fromArray(this.opts.camera.pos); c.target.fromArray(this.opts.camera.target || [0, 0, 0]);
      this.flying = false;
    } else {
      cam.position.copy(this.reduced ? this.home : this.home.clone().multiplyScalar(2.6));
      this.flyStart = performance.now(); this.flying = !this.reduced;
    }
    this._lastMaxR = maxR;
  }

  _rng(seed) {
    let a = seed >>> 0;
    return () => {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  _dotTex() {
    const c = document.createElement("canvas"); c.width = c.height = 64; const g = c.getContext("2d");
    const r = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    r.addColorStop(0, "rgba(255,255,255,1)"); r.addColorStop(0.25, "rgba(255,255,255,.8)"); r.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = r; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c);
  }

  _linkColors() {
    const a = this.linkGeo.attributes.color.array, c = this.color;
    for (let i = 0; i < this.linkFade.length; i++) { const f = this.linkFade[i]; a[i * 3] = c.r * f; a[i * 3 + 1] = c.g * f; a[i * 3 + 2] = c.b * f; }
    this.linkGeo.attributes.color.needsUpdate = true;
  }

  // a glowing orb: radial falloff, a soft rim, no polygon edges to betray the sprite
  _coreSprite(style) {
    const c = document.createElement("canvas"); c.width = c.height = 256; const g = c.getContext("2d");
    const rgb = this.color, rgba = (a) => `rgba(${Math.round(rgb.r * 255)},${Math.round(rgb.g * 255)},${Math.round(rgb.b * 255)},${a})`;
    const kind = style || this.s.coreStyle || "orb";
    g.clearRect(0, 0, 256, 256);

    if (kind === "dot") {
      // small hard star
      const r0 = g.createRadialGradient(128, 128, 0, 128, 128, 70);
      r0.addColorStop(0, rgba(1)); r0.addColorStop(0.35, rgba(0.85)); r0.addColorStop(1, rgba(0));
      g.fillStyle = r0; g.beginPath(); g.arc(128, 128, 70, 0, Math.PI * 2); g.fill();
    } else if (kind === "none") {
      // nothing
    } else {
      // orb: bright centre, smooth falloff, faint concentric rim
      const r0 = g.createRadialGradient(128, 128, 0, 128, 128, 118);
      r0.addColorStop(0, "rgba(255,255,255,1)");
      r0.addColorStop(0.18, rgba(0.95));
      r0.addColorStop(0.55, rgba(0.42));
      r0.addColorStop(0.82, rgba(0.1));
      r0.addColorStop(1, rgba(0));
      g.fillStyle = r0; g.beginPath(); g.arc(128, 128, 118, 0, Math.PI * 2); g.fill();
      // a thin rim makes it read as a sphere rather than a blob
      g.strokeStyle = "rgba(255,255,255,.22)"; g.lineWidth = 2;
      g.beginPath(); g.arc(128, 128, 74, 0, Math.PI * 2); g.stroke();
    }
    const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, opacity: 0.95, depthTest: false }));
    sp.renderOrder = 10; return sp;
  }

  // ---------------------------------------------------------------- UI
  _ui() {
    const mk = (cls, parent = this.root, tag = "div") => { const e = document.createElement(tag); e.className = cls; parent.appendChild(e); return e; };
    this.labelLayer = mk("nebula-labels");
    // label the big folders always, nested ones by name. Cap the DOM churn on huge vaults.
    const labelled = this.L.rings.slice().sort((a, b) => b.descendants - a.descendants).slice(0, 80);
    this.hubLabels = labelled.map((ring) => {
      const e = mk("nebula-label", this.labelLayer);
      e.classList.add(`nebula-label-l${Math.min(ring.level, 3)}`);
      mk("", e, "b").textContent = ring.name;
      mk("", e, "span").textContent = ring.count === ring.descendants ? `${ring.count} note${ring.count === 1 ? "" : "s"}` : `${ring.descendants} notes · ${ring.count} here`;
      e.title = `${ring.folder}\nlevel ${ring.level}`;
      e.addEventListener("click", () => this.focusSlot(ring.slot));
      return { el: e, ring };
    });
    this.tip = mk("nebula-tip"); this.tip.classList.add("is-hidden");
    const d = this.L.depths;
    mk("nebula-hud").textContent = `${d.notes.toLocaleString()} notes · ${d.rings} rings · ${d.levels} level${d.levels === 1 ? "" : "s"} · ${this.edges.length.toLocaleString()} links`;
    this.fpsEl = mk("nebula-fps");
    mk("nebula-hint").textContent = "Drag to spin · right-drag to move · scroll to zoom · click a star to open it · click a name to fly there";
    const bar = mk("nebula-bar");
    const b1 = mk("nebula-btn", bar, "button"); b1.textContent = "Reset view"; b1.addEventListener("click", () => this.resetView());
    const b2 = mk("nebula-btn", bar, "button"); b2.textContent = "Focus"; b2.addEventListener("click", () => this.opts.onFocus && this.opts.onFocus());
    const b3 = mk("nebula-btn", bar, "button"); b3.textContent = "Customize"; b3.addEventListener("click", () => this.panel.classList.toggle("is-open"));
    this._panel(mk);
    if (this.opts.panelOpen) this.panel.classList.add("is-open");
  }

  _panel(mk) {
    const p = (this.panel = mk("nebula-panel"));
    mk("nebula-ph", p).textContent = "Customize";
    const row = (label) => { const r = mk("nebula-row", p); mk("nebula-rl", r).textContent = label; return r; };

    // colour: swatches, a visible "custom" chip, and a hex box so any colour can be typed
    const cr = row("Color"), sw = mk("nebula-sw", cr);
    SWATCHES.forEach(([name, hex]) => {
      const b = mk("nebula-dot", sw, "button"); b.title = name; b.style.background = hex;
      b.addEventListener("click", () => { this.set({ color: hex }); if (this.hexInput) this.hexInput.value = hex; });
    });
    const custom = mk("nebula-custom", cr, "button");
    custom.title = "Pick any colour";
    custom.append(mk("nebula-custom-chip", custom, "span"), mk("nebula-custom-label", custom, "span"));
    custom.querySelector(".nebula-custom-label").textContent = "Custom";
    const pick = mk("nebula-pick", custom, "input"); pick.type = "color"; pick.value = this.s.color;
    const hex = mk("nebula-hex", p, "input");
    hex.type = "text"; hex.spellcheck = false; hex.placeholder = "#2cf27f";
    const hexRow = mk("nebula-row", p);
    mk("nebula-rl", hexRow).textContent = "Hex";
    hexRow.appendChild(hex);
    const chip = () => {
      if (!custom.firstChild) return;
      custom.firstChild.style.background = this.s.color;
      custom.style.borderColor = this.s.color + "66";
    };
    const applyHex = () => {
      const v = hex.value.trim().replace(/^#?/, "#");
      if (/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(v)) {
        const full = v.length === 4 ? "#" + v.slice(1).split("").map((ch) => ch + ch).join("") : v;
        this.set({ color: full }); pick.value = full; hex.value = full; chip();
      } else hex.value = this.s.color;
    };
    pick.addEventListener("input", () => { this.set({ color: pick.value }); hex.value = pick.value; chip(); });
    hex.addEventListener("change", applyHex);
    hex.addEventListener("keydown", (e) => { if (e.key === "Enter") applyHex(); });
    this.picker = pick; this.hexInput = hex; this._chip = chip;
    hex.value = this.s.color; chip();

    const slider = (label, key, min, max, step, after) => {
      const r = row(label), inp = mk("nebula-range", r, "input"), out = mk("nebula-val", r, "span");
      Object.assign(inp, { type: "range", min, max, step, value: this.s[key] });
      const show = () => (out.textContent = step >= 1 ? String(Math.round(this.s[key])) : (+this.s[key]).toFixed(2));
      inp.addEventListener("input", () => { this.set({ [key]: +inp.value }); show(); if (after) after(); });
      show();
      (this._inputs = this._inputs || []).push(() => { inp.value = this.s[key]; show(); });
    };
    const toggle = (label, key, after) => {
      const r = row(label), b = mk("nebula-tog", r, "button");
      const show = () => { b.classList.toggle("is-on", !!this.s[key]); b.textContent = this.s[key] ? "On" : "Off"; };
      b.addEventListener("click", () => { this.set({ [key]: !this.s[key] }); show(); if (after) after(); });
      show(); (this._inputs = this._inputs || []).push(show);
    };

    mk("nebula-sub", p).textContent = "Structure";
    slider("Nesting depth", "maxDepth", 1, 6, 1, () => this.opts.onRebuild && this.opts.onRebuild());
    slider("Max rings", "maxRings", 6, 300, 1, () => this.opts.onRebuild && this.opts.onRebuild());
    slider("Ring spacing", "spacing", 0.5, 2.0, 0.05, () => this.opts.onRebuild && this.opts.onRebuild());
    toggle("Sub-ring arcs", "wedgeArcs", () => this.opts.onRebuild && this.opts.onRebuild());
    toggle("Depth fade", "depthFade", () => this.opts.onRebuild && this.opts.onRebuild());
    toggle("Root notes", "showRootNotes", () => this.opts.onRebuild && this.opts.onRebuild());

    mk("nebula-sub", p).textContent = "Look";
    slider("Glow", "glow", 0, 2, 0.05);
    slider("Glow size", "glowSize", 0, 1, 0.05);
    slider("Node size", "nodeSize", 0.3, 3, 0.05);
    slider("Links", "links", 0, 3, 0.05);
    slider("Rings", "rings", 0, 3, 0.05);
    slider("Light beads", "beads", 0, MAX_BEADS, 10);
    slider("Bead speed", "beadSpeed", 0.1, 4, 0.1);
    slider("Spin (scene)", "spin", 0, 2, 0.05);
    slider("Orbit spin", "ringSpin", 0, 3, 0.05);
    toggle("Comets", "comets"); toggle("Pulse", "pulse"); toggle("Stars", "stars");
    toggle("Folder names", "labels"); toggle("Save GPU in background", "saveGpu");

    const corer = row("Core"), coreSeg = mk("nebula-seg", corer);
    [["orb", "Orb"], ["dot", "Star"], ["none", "None"]].forEach(([v, label]) => {
      const b = mk("nebula-segb", coreSeg, "button"); b.textContent = label;
      b.addEventListener("click", () => {
        this.set({ coreStyle: v });
        coreSeg.querySelectorAll("button").forEach((x) => x.classList.toggle("is-on", x === b));
      });
      if (this.s.coreStyle === v) b.classList.add("is-on");
    });

    const qr = row("Quality"), seg = mk("nebula-seg", qr);
    this._qBtns = Object.keys(QUALITY).map((q) => {
      const b = mk("nebula-segb", seg, "button"); b.textContent = q[0].toUpperCase() + q.slice(1);
      b.addEventListener("click", () => this.set({ quality: q })); return [q, b];
    });

    const reset = mk("nebula-btn nebula-reset", p, "button"); reset.textContent = "Reset to default";
    reset.addEventListener("click", () => {
      this.set(Object.assign({}, DEFAULTS)); this.picker.value = this.s.color;
      (this._inputs || []).forEach((f) => f()); this.opts.onRebuild && this.opts.onRebuild();
    });
  }

  // Structure keys change the geometry, and the geometry is rebuilt by constructing a new
  // Constellation. If the save is still debounced when that happens, the new instance loads stale
  // settings and the change appears to "not save" (and the panel snaps shut). So persist those
  // synchronously; only cosmetic tweaks are debounced.
  static STRUCTURE = ["maxDepth", "maxRings", "showRootNotes", "wedgeArcs", "depthFade", "spacing", "coreStyle"];

  set(patch) {
    Object.assign(this.s, patch); this.apply(patch);
    const structural = Object.keys(patch).some((k) => Constellation.STRUCTURE.includes(k));
    window.clearTimeout(this._saveT);
    const save = () => this.opts.onSettings && this.opts.onSettings(Object.assign({}, this.s));
    if (structural) save();
    else this._saveT = window.setTimeout(save, 300);
  }

  /** panel visibility, so a rebuild can put the user back where they were */
  panelOpen() { return this.panel && this.panel.classList.contains("is-open"); }

  apply(patch) {
    const s = this.s;
    if (!patch || "color" in patch) {
      this.color.set(s.color);
      this.ringLines.forEach((r) => r.material.color.copy(this.color));
      this.halos.forEach((l) => l.material.color.copy(this.color));
      this._linkColors();
      const old = this.core; this.core = this._coreSprite(); this.core.scale.setScalar(this.coreSize); this.scene.add(this.core);
      if (old) { this.scene.remove(old); old.material.map.dispose(); old.material.dispose(); }
      this.root.style.setProperty("--nebula-color", s.color);
      if (this.picker) this.picker.value = s.color;
      if (this.hexInput) this.hexInput.value = s.color;
      if (this._chip) this._chip();
    }
    if (patch && "coreStyle" in patch) {
      const old = this.core; this.core = this._coreSprite(); this.core.scale.setScalar(this.coreSize); this.scene.add(this.core);
      if (old) { this.scene.remove(old); old.material.map.dispose(); old.material.dispose(); }
    }
    this.bloom.strength = s.glow; this.bloom.radius = s.glowSize;
    this.uniforms.uSizeMul.value = s.nodeSize;
    this.uniforms.uPulse.value = s.pulse && !this.reduced ? 1 : 0;
    this.linkMat.opacity = 0.1 * s.links; this.links.visible = s.links > 0;
    this.ringLines.forEach((r, i) => { r.material.opacity = (this.ringBase[i] || 0.2) * s.rings; r.visible = s.rings > 0; });
    this.beadPts.geometry.setDrawRange(0, Math.min(MAX_BEADS, Math.round(s.beads))); this.beadPts.visible = s.beads > 0;
    this.dust.visible = !!s.stars;
    this.comets.forEach((c) => (c.line.visible = !!s.comets && s.rings > 0));
    this.labelLayer.style.display = s.labels ? "" : "none";
    if (!patch || "quality" in patch) { this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, QUALITY[s.quality] || 1.75)); this.resize(); }
    if (this._qBtns) this._qBtns.forEach(([q, b]) => b.classList.toggle("is-on", q === s.quality));
  }

  cameraState() {
    return { pos: this.camera.position.toArray(), target: this.controls.target.toArray() };
  }

  resetView() {
    this._fly = { t0: performance.now(), from: this.controls.target.clone(), to: new THREE.Vector3(), cf: this.camera.position.clone(), ct: this.home.clone() };
    this.camera.up.set(0, 1, 0);
  }

  _events() {
    const cv = this.renderer.domElement;
    this._mv = (e) => { const r = cv.getBoundingClientRect(); this.mouse = { x: ((e.clientX - r.left) / r.width) * 2 - 1, y: -((e.clientY - r.top) / r.height) * 2 + 1 }; this._dirty = true; };
    this._lv = () => { this.mouse = null; this._dirty = true; };
    this._dn = (e) => { this._down = [e.clientX, e.clientY]; this.spinPause = performance.now() + 6000; this._fly = null; this.flying = false; };
    this._up = (e) => {
      const d = this._down; this._down = null;
      if (!d || e.button !== 0 || Math.hypot(e.clientX - d[0], e.clientY - d[1]) > 5) return;
      if (this.hover > 0 && this.hover <= this.L.notes.length && this.opts.onOpen) {
        const note = this.L.slotToNote[this.hover];
        if (note) this.opts.onOpen(note.id, e);
      }
    };
    this._wh = () => { this.spinPause = performance.now() + 3000; this._fly = null; this.flying = false; };
    cv.addEventListener("pointermove", this._mv); cv.addEventListener("pointerleave", this._lv);
    cv.addEventListener("pointerdown", this._dn); cv.addEventListener("pointerup", this._up);
    cv.addEventListener("wheel", this._wh, { passive: true });
    cv.addEventListener("contextmenu", (e) => e.preventDefault());
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(this.el);
  }

  resize() {
    const w = Math.max(this.el.clientWidth, 100), h = Math.max(this.el.clientHeight, 100);
    this.renderer.setSize(w, h); this.composer.setSize(w, h);
    this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    this.uniforms.uScale.value = h * 0.62;
    // refit the default view when the aspect changes, unless the user has moved the camera
    if (this.fitDistance && !this._fly && !this._down) {
      const { distance: d } = cameraFit(this.L.maxExtent || this.far, this.camera.fov, this.camera.aspect);
      this.fitDistance = d;
      const elev = 0.30;
      this.home.set(0, Math.sin(elev) * d, Math.cos(elev) * d);
    }
    this.controls.handleResize && this.controls.handleResize();
  }

  focusSlot(slot) {
    if (slot == null || slot < 0) return;
    const p = new THREE.Vector3().fromArray(this.L.pos, slot * 3);
    const dir = this.camera.position.clone().sub(this.controls.target).normalize().multiplyScalar(Math.max(10, this.L.coreRadius * 0.9));
    this._fly = { t0: performance.now(), from: this.controls.target.clone(), to: p, cf: this.camera.position.clone(), ct: p.clone().add(dir) };
    this.spinPause = performance.now() + 5000;
  }

  _pick() {
    if (!this.mouse) return -1;
    this.raycaster.setFromCamera(this.mouse, this.camera);
    const hits = this.raycaster.intersectObject(this.points);
    let best = -1, bd = 1e9;
    for (const h of hits) { if (h.index === 0) continue; if (h.distanceToRay < bd) { bd = h.distanceToRay; best = h.index; } }
    return best;
  }

  _setHover(i) {
    const b = this.boostAttr.array, old = this.hover;
    if (old >= 0) { b[old] = 0; (this.adj.get(old) || []).forEach((n) => (b[n] = 0)); }
    this.hover = i;
    if (i >= 0) {
      b[i] = 1;
      const nb = this.adj.get(i) || [];
      nb.forEach((n) => (b[n] = 0.55));
      const lp = this.hoverGeo.attributes.position.array; let k = 0;
      for (const n of nb) {
        if (k >= 600) break;
        lp.set(this.L.pos.subarray(i * 3, i * 3 + 3), k * 6);
        lp.set(this.L.pos.subarray(n * 3, n * 3 + 3), k * 6 + 3); k++;
      }
      this.hoverGeo.attributes.position.needsUpdate = true; this.hoverGeo.setDrawRange(0, k * 2);
    } else this.hoverGeo.setDrawRange(0, 0);
    this.boostAttr.needsUpdate = true;
  }

  _project(slot, out) { out.fromArray(this.L.pos, slot * 3).project(this.camera); return out; }

  _loop(now) {
    if (!this.running) return;
    window.requestAnimationFrame(this._loop);
    if (document.hidden || !this.el.isConnected || this.el.clientWidth < 10 || (this.el.offsetParent === null && !this.opts.force)) return;
    if (this.s.saveGpu && !document.hasFocus() && now - this._last < 66) return;
    const dt = Math.min(100, now - (this._last || now)); this._last = now; this.frames++;
    if ((this._fpsT = (this._fpsT || now)) && now - this._fpsT > 500) {
      this.fpsEl.textContent = Math.round((this.frames - (this._fpsF || 0)) * 1000 / (now - this._fpsT)) + " fps";
      this._fpsT = now; this._fpsF = this.frames;
    }
    const t = (now - this.t0) / 1000; this.uniforms.uTime.value = this.reduced ? 0 : t;
    const cam = this.camera, c = this.controls;
    if (this.flying) {
      const k = (now - this.flyStart) / 2600;
      cam.position.lerpVectors(this.home.clone().multiplyScalar(1.9), this.home, ease(k));
      if (k >= 1) this.flying = false;
    }
    if (this._fly) {
      const k = ease((now - this._fly.t0) / 900);
      c.target.lerpVectors(this._fly.from, this._fly.to, k);
      cam.position.lerpVectors(this._fly.cf, this._fly.ct, k);
      if (k >= 1) this._fly = null;
    }
    if (!this.reduced && this.s.spin > 0 && now > this.spinPause && !this._down) {
      const q = (this._q = this._q || new THREE.Quaternion()), off = (this._off = this._off || new THREE.Vector3());
      q.setFromAxisAngle(cam.up, this.s.spin * 0.12 * (dt / 1000));
      off.copy(cam.position).sub(c.target).applyQuaternion(q); cam.position.copy(c.target).add(off);
    }
    c.update();
    this.core.scale.setScalar(this.coreSize * (1 + (this.s.pulse && !this.reduced ? Math.sin(t * 1.3) * 0.04 : 0)));
    if (this._dirty || this.hover >= 0) { const p = this._pick(); if (p !== this.hover) this._setHover(p); this._dirty = false; }

    const w = this.el.clientWidth, h = this.el.clientHeight;
    const tmp = (this._tmp = this._tmp || new THREE.Vector3()), hv = (this._hv = this._hv || new THREE.Vector3());

    // tooltip
    if (this.hover > 0 && this.hover <= this.L.notes.length && this.mouse) {
      const n = this.L.slotToNote[this.hover];
      this._project(this.hover, tmp); this.tip.classList.remove("is-hidden");
      this.tip.style.transform = `translate(${(tmp.x * 0.5 + 0.5) * w + 14}px,${(-tmp.y * 0.5 + 0.5) * h - 10}px)`;
      if (this.tip._n !== this.hover) {
        this.tip.replaceChildren();
        const b = document.createElement("b"), sp = document.createElement("span");
        b.textContent = n.name; sp.textContent = n.folder || "root";
        this.tip.append(b, sp); this.tip._n = this.hover;
      }
    } else this.tip.classList.add("is-hidden");

    // folder labels
    if (this.s.labels) {
      const dist = cam.position.distanceTo(c.target);
      const zf = Math.min(1, Math.max(0, (this.far * 3.2 - dist) / (this.far * 1.6)));
      for (const { el, ring } of this.hubLabels) {
        this._project(ring.slot, tmp);
        const cd = cam.position.distanceTo(hv.fromArray(this.L.pos, ring.slot * 3));
        const near = cd < this.far * 1.2;
        if (tmp.z > 1 || tmp.z < -1 || !near || zf <= 0.02) { el.classList.add("is-off"); continue; }
        const byLevel = ring.level === 1 ? 1 : ring.level === 2 ? 0.85 : 0.7;
        el.style.opacity = Math.min(1, byLevel * Math.min(1, (this.far * 1.2 - cd) / (this.far * 0.35))) * zf;
        el.classList.remove("is-off");
        el.style.transform = `translate(${(tmp.x * 0.5 + 0.5) * w}px,${(-tmp.y * 0.5 + 0.5) * h}px) translate(-50%,-155%) scale(${ring.level === 1 ? 1 : 0.82})`;
      }
    }

    // rings drift: each ring spins around its own axis at its own speed and direction.
    // Nodes are children of their ring, so they have to be re-placed with it.
    if (!this.reduced && this.s.ringSpin > 0) {
      for (let i = 0; i < this.ringLocal.length; i++) {
        const ring = this.ringLocal[i];
        const speed = (ring.orbitSpeed || 0) * this.s.ringSpin;
        if (!speed) continue;
        ring.spin = (ring.spin || 0) + speed * dt / 1000;
        const arc = this.ringArc[i];
        const pts = arc ? ringPoints(ring, arc[2], [arc[0], arc[1]]) : ringPoints(ring, this.ringPts[i].length / 3);
        const attr = this.ringLines[i].geometry.attributes.position;
        attr.array.set(pts); attr.needsUpdate = true;
        this.ringPts[i] = pts;
        // re-place this ring's stars (they are stored with their local ring coordinates)
        const local = this.nodeLocal[i];
        if (local) {
          const P = this.L.pos, out = [0, 0, 0];
          for (const [slot, r, th, y] of local) {
            tiltPoint(Math.cos(th) * r, y, Math.sin(th) * r, ring, out);
            P[slot * 3] = out[0]; P[slot * 3 + 1] = out[1]; P[slot * 3 + 2] = out[2];
          }
          this.points.geometry.attributes.position.needsUpdate = true;
        }
      }
      // comets ride the rings, so re-point them at the fresh geometry
      for (const cm of this.comets) {
        const idx = this.cometRing.get(cm);
        if (idx != null) cm.lp = this.ringPts[idx];
      }
    }

    // beads
    const nb = Math.min(MAX_BEADS, Math.round(this.s.beads));
    if (!this.reduced && nb > 0 && this.edges.length) {
      const bp = this.beadPts.geometry.attributes.position.array, P = this.L.pos, sp = this.s.beadSpeed * dt / 1000;
      for (let i = 0; i < nb; i++) {
        const b = this.beads[i];
        b.t += b.s * sp;
        if (b.t > 1) { b.t = 0; b.e = this.edges[(Math.random() * this.edges.length) | 0]; }
        const a = b.e[0] * 3, e2 = b.e[1] * 3, k = b.t;
        bp[i * 3] = P[a] + (P[e2] - P[a]) * k;
        bp[i * 3 + 1] = P[a + 1] + (P[e2 + 1] - P[a + 1]) * k;
        bp[i * 3 + 2] = P[a + 2] + (P[e2 + 2] - P[a + 2]) * k;
      }
      this.beadPts.geometry.attributes.position.needsUpdate = true;
    }

    if (!this.reduced) {
      const k = this.coreSize * 0.5;
      this.halos.forEach((l) => { l.scale.setScalar(k * l.userData.k); l.rotateOnAxis(l.userData.ax, l.userData.sp * dt / 1000); });
      if (this.s.comets && this.s.rings > 0) for (const cm of this.comets) {
        cm.ph = (cm.ph + cm.sp * dt / 1000 + 1) % 1;
        const seg = cm.lp.length / 3, head = Math.floor(cm.ph * seg), dir = cm.sp > 0 ? 1 : -1;
        const P = cm.line.geometry.attributes.position.array, C = cm.line.geometry.attributes.color.array;
        const col = this.color, br = Math.min(2, 0.6 + this.s.rings * 0.7) * (cm.level === 1 ? 1 : 0.65);
        for (let j = 0; j < cm.TL; j++) {
          const idx = ((head - j * dir) % seg + seg) % seg;
          P[j * 3] = cm.lp[idx * 3]; P[j * 3 + 1] = cm.lp[idx * 3 + 1]; P[j * 3 + 2] = cm.lp[idx * 3 + 2];
          const f = Math.pow(1 - j / cm.TL, 2) * br, wht = j < 2 ? 0.5 : 0;
          C[j * 3] = Math.min(1, col.r * f + wht); C[j * 3 + 1] = Math.min(1, col.g * f + wht); C[j * 3 + 2] = Math.min(1, col.b * f + wht);
        }
        cm.line.geometry.attributes.position.needsUpdate = true;
        cm.line.geometry.attributes.color.needsUpdate = true;
      }
    } else this.halos.forEach((l) => l.scale.setScalar(this.coreSize * 0.5 * l.userData.k));

    this.composer.render();
  }

  destroy() {
    this.running = false;
    window.clearTimeout(this._saveT);
    this.ro && this.ro.disconnect();
    this.controls.dispose();
    this.scene.traverse((o) => {
      o.geometry && o.geometry.dispose();
      if (o.material) { o.material.map && o.material.map.dispose(); o.material.dispose(); }
    });
    this.composer.dispose && this.composer.dispose();
    this.renderer.dispose();
    this.root.remove();
  }
}
