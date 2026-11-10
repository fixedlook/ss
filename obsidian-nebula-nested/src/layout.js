// Ring/nesting geometry. Pure math, no three.js: produces the position buffers the renderer uploads.
//
// Design, after two rounds of "that looks wrong" on real devices:
//
//   * Folders own *arcs*, not full circles, so hierarchy survives: children are allocated a slice of
//     their parent's bearing range, sized by subtree note count.
//   * A folder's ring is a HOOP floating at its own random bearing and elevation, on a shell whose
//     distance from the hub grows with nesting depth. Hoops do not pass through the hub — that was
//     the "atom" look, where every ring crossed at one point and read as petals.
//   * Each hoop gets an arbitrary 3D orientation (pitch/yaw/roll), and drifts.
//
// Depth reads as distance from the centre; bearing reads as which branch you are in.
import { ROOT } from "./model.js";

const TAU = Math.PI * 2;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

const rngFrom = (seed) => {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};
// FNV-1a followed by a murmur3 fmix32 finalizer. Without the finalizer, strings differing only in
// their last character ("Top0".."Top3") hash to almost the same value and every ring gets the same
// orientation — which was a shipped bug, so it stays fixed.
const hash = (s) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  h ^= h >>> 16; h = Math.imul(h, 2246822507);
  h ^= h >>> 13; h = Math.imul(h, 3266489909);
  h ^= h >>> 16;
  return h >>> 0;
};
const unit = (s) => hash(s) / 4294967296;

/**
 * Place a point that lives in a ring's own plane into world space.
 *
 * A ring is born in its local xz-plane centred on the origin, spun by `spin` (so it drifts), given a
 * random 3D orientation by three successive rotations, then translated to the hoop's own centre.
 * The translation is what stops every ring from being a great circle through the hub.
 */
export function tiltPoint(x, y, z, ring, out) {
  // 1. spin around the hoop's own axis (local y)
  const cs = Math.cos(ring.spin || 0), ss = Math.sin(ring.spin || 0);
  const sx = x * cs + z * ss, sz = -x * ss + z * cs;
  // 2. pitch about X
  const cx = Math.cos(ring.rotX), snx = Math.sin(ring.rotX);
  const y1 = y * cx - sz * snx, z1 = y * snx + sz * cx;
  // 3. yaw about Y
  const cy = Math.cos(ring.rotY), sny = Math.sin(ring.rotY);
  const x2 = sx * cy + z1 * sny, z2 = -sx * sny + z1 * cy;
  // 4. roll about Z
  const cz = Math.cos(ring.rotZ), snz = Math.sin(ring.rotZ);
  out[0] = x2 * cz - y1 * snz + ring.cx;
  out[1] = x2 * snz + y1 * cz + ring.cy;
  out[2] = z2 + ring.cz;
  return out;
}

/**
 * @param {ReturnType<import("./model.js").buildTree>} tree
 * @param {[string,string][]} links resolved [[fromPath, toPath], ...] (.md paths)
 * @param {object} opt {start, perLevel, coreRadius, jitter, offsetFrac, hoopScale, spacing}
 */
export function layout(tree, links = [], opt = {}) {
  const start = opt.start == null ? 0 : opt.start;
  const coreRadius = opt.coreRadius == null ? 26 : opt.coreRadius;
  const spacing = opt.spacing == null ? 1 : opt.spacing; // user-facing "ring spacing"
  const perLevel = (opt.perLevel == null ? 7.5 : opt.perLevel) * spacing;
  const hoopScale = opt.hoopScale == null ? 0.85 : opt.hoopScale; // hoop size vs shell distance
  const elevation = opt.elevation == null ? 1.35 : opt.elevation; // spread of hoop heights (rad)

  const nodes = tree.nodes;
  const folders = tree.order;
  const sizeOf = (f) => Math.max(1, nodes.get(f).descendants || nodes.get(f).notes.length || 1);

  // --- 1. bearing wedges: children tile their parent's bearing range ---------------------
  const process = (parent, wedgeStart, wedgeWidth) => {
    const kids = (tree.siblings.get(parent) || []).filter((f) => nodes.has(f));
    if (!kids.length) return;
    const total = kids.reduce((s, f) => s + sizeOf(f), 0);
    let cursor = wedgeStart;
    kids.forEach((f, i) => {
      const w = (sizeOf(f) / total) * wedgeWidth;
      const a0 = cursor, a1 = cursor + w;
      const n = nodes.get(f);
      n.angle = (a0 + a1) / 2;
      n.wedge = [a0, a1];
      n.level = parent === ROOT ? 1 : nodes.get(parent).level + 1;
      n.sibIdx = i;
      cursor = a1;
      process(f, a0, w);
    });
  };
  process(ROOT, start, TAU);

  // --- 2. hoops: own bearing, own elevation, own orientation, own centre ----------------
  const rings = [];
  const ringOf = new Map();
  for (const f of folders) {
    const n = nodes.get(f);
    const lv = n.level;

    // size of the hoop first: bigger folders get bigger hoops
    const hoopR = clamp(perLevel * hoopScale * Math.sqrt(sizeOf(f)) * 1.15, perLevel * 0.7, perLevel * 3.4);

    // then push its centre out far enough that the hoop clears the core. This is the invariant that
    // keeps rings off the hub: clearance = shellD - hoopR stays positive for every folder.
    // shell distance must be strictly increasing per level, so level n+1 never sinks inside level n
    const shellBase = coreRadius * 1.08 + (lv - 1) * (perLevel * 1.5) + perLevel * 0.9;
    const shellD = shellBase + hoopR * 0.5 + unit("sd:" + f) * perLevel * 0.35;

    // bearing: the middle of this folder's wedge, lightly jittered
    const bearing = n.angle + (unit("bj:" + f) - 0.5) * 0.22;

    // elevation: random per hoop, so they are scattered "all over the place" rather than in bands
    const el = (unit("el:" + f) - 0.5) * elevation + (lv % 2 ? 0.12 : -0.12);

    const cosEl = Math.cos(el);
    const ring = {
      folder: f,
      name: n.name,
      radius: hoopR,
      shellD,
      level: lv,
      parent: n.parent,
      count: n.notes.length,
      descendants: n.descendants,
      arc: [n.wedge[0], n.wedge[1]],
      bearing,
      elevation: el,
      // three independent rotations: a genuinely arbitrary plane, not a tilt off flat
      rotX: unit("rx:" + f) * Math.PI * 0.95,
      rotY: unit("ry:" + f) * TAU,
      rotZ: unit("rz:" + f) * Math.PI * 0.95,
      spin: 0,
      orbitSpeed: (unit("spin:" + f) - 0.5) * 0.5,
      orbitPhase: unit("phase:" + f) * TAU,
      cx: Math.cos(bearing) * cosEl * shellD,
      cy: Math.sin(el) * shellD * 0.62,
      cz: Math.sin(bearing) * cosEl * shellD,
      slot: -1,
    };

    // de-cluster: no two same-parent hoops may share an orientation or a height band
    const sibs = rings.filter((o) => o.parent === n.parent && o.level === lv);
    for (let guard = 0; guard < 10; guard++) {
      const clash = sibs.some((o) => Math.abs(o.cy - ring.cy) < shellD * 0.12
        && Math.abs(((o.rotX - ring.rotX + Math.PI * 3) % TAU) - Math.PI) < 0.35);
      if (!clash) break;
      ring.rotX = (ring.rotX + 0.618034 * Math.PI) % Math.PI;
      ring.cy += (unit("cy:" + f + guard) < 0.5 ? -1 : 1) * shellD * 0.16;
    }
    if (sibs.length && unit("dir:" + f) < 0.5) ring.orbitSpeed = -ring.orbitSpeed;

    rings.push(ring);
    ringOf.set(f, ring);
  }

  // --- 3. slots: 0 = core, then notes, then folder hubs --------------------------------
  const noteList = [];
  for (const f of folders) for (const p of nodes.get(f).notes) noteList.push([p, f]);
  for (const p of tree.loose || []) noteList.push([p, ROOT]);

  const N = noteList.length;
  const total = N + rings.length + 1;
  const pos = new Float32Array(total * 3);
  const aSize = new Float32Array(total);
  const phase = new Float32Array(total);
  const slotToNote = new Array(total).fill(null);
  const noteSlot = new Map();
  const localByRing = new Map();
  const tmp = [0, 0, 0];
  const scale = Math.min(2.4, Math.max(0.5, 3.0 / Math.pow(Math.max(N, 50) / 100, 0.3)));

  let slot = 1;
  for (const [path, folder] of noteList) {
    const ring = ringOf.get(folder);
    const R = rngFrom(hash(path));
    if (ring) {
      if (!localByRing.has(ring.folder)) localByRing.set(ring.folder, []);
      const localNotes = localByRing.get(ring.folder);
      const [a0, a1] = ring.arc;
      const th = a0 + R() * Math.max(1e-4, a1 - a0);
      // a thin band inside the hoop so stars read as sitting *on* it
      const band = clamp(0.35 + Math.sqrt(ring.count) * 0.16, 0.4, 1.6);
      const rr = ring.radius + (R() - 0.5) * band * 2;
      const y = (R() - 0.5) * band * 0.8;
      tiltPoint(Math.cos(th) * rr, y, Math.sin(th) * rr, ring, tmp);
      pos.set(tmp, slot * 3);
      localNotes.push([slot, rr, th, y]);
    } else {
      // loose root note: a slow halo directly around the hub
      const th = R() * TAU, rr = coreRadius * (1.2 + R() * 0.6);
      pos.set([Math.cos(th) * rr, (R() - 0.5) * 5, Math.sin(th) * rr], slot * 3);
    }
    aSize[slot] = 1.3 * scale;
    phase[slot] = R() * TAU;
    noteSlot.set(path, slot);
    slotToNote[slot] = { id: path, name: path.slice(path.lastIndexOf("/") + 1).replace(/\.md$/, ""), folder };
    slot++;
  }

  for (const ring of rings) {
    const s = slot++;
    const R = rngFrom(hash("hub:" + ring.folder));
    // the label anchor sits on the hoop, at the middle of the folder's arc
    const mid = (ring.arc[0] + ring.arc[1]) / 2;
    tiltPoint(Math.cos(mid) * ring.radius, 0, Math.sin(mid) * ring.radius, ring, tmp);
    pos.set(tmp, s * 3);
    aSize[s] = 3.4 * scale;
    phase[s] = R() * TAU;
    ring.slot = s;
  }

  // --- 4. edges + degree-driven node sizes ---------------------------------------------
  const edges = [];
  const deg = new Float32Array(total);
  for (const [a, b] of links) {
    const x = noteSlot.get(a), y = noteSlot.get(b);
    if (!x || !y || x === y) continue;
    edges.push([x, y]); deg[x]++; deg[y]++;
  }
  for (let s = 1; s <= N; s++) aSize[s] = (1.3 + Math.min(4.2, Math.sqrt(deg[s]) * 0.8)) * scale;

  const maxR = rings.reduce((m, r) => Math.max(m, Math.hypot(r.cx, r.cy, r.cz) + r.radius), coreRadius * 2);
  const far = Math.max(60, maxR * 1.15);

  return {
    pos, aSize, phase, total, edges, deg,
    notes: noteList.map(([p]) => p), slotToNote, noteSlot, localByRing,
    rings, ringOf, radius: new Map(folders.map((f) => [f, nodes.get(f).radius || ringOf.get(f).radius])),
    far, scale, coreRadius,
    depths: {
      levels: rings.reduce((m, r) => Math.max(m, r.level), 0),
      rings: rings.length,
      notes: N,
      loose: (tree.loose || []).length,
    },
  };
}

/**
 * Points along a ring, already placed in world space.
 * @param {object} ring
 * @param {number} seg  number of segments
 * @param {[number,number]} [range] angular range; defaults to the full circle
 */
export function ringPoints(ring, seg = 200, range) {
  const out = new Float32Array(seg * 3);
  const tmp = [0, 0, 0];
  const a0 = range ? range[0] : 0;
  const span = range ? range[1] - range[0] : TAU;
  for (let s = 0; s < seg; s++) {
    const th = a0 + (s / seg) * span;
    tiltPoint(Math.cos(th) * ring.radius, 0, Math.sin(th) * ring.radius, ring, tmp);
    out[s * 3] = tmp[0]; out[s * 3 + 1] = tmp[1]; out[s * 3 + 2] = tmp[2];
  }
  return out;
}

/**
 * Belt-and-braces check used by the tests and a startup assert. Returns a list of problems.
 * The checks encode bugs that actually shipped: hoops through the hub, identical transforms,
 * a shell that does not sit outside its parent, non-finite transforms after a bad rotation.
 */
export function auditLayout(L, minGap = 1) {
  const problems = [];
  const { rings } = L;
  const TAU = Math.PI * 2;

  for (const r of rings) {
    if (![r.radius, r.rotX, r.rotY, r.rotZ, r.cx, r.cy, r.cz].every(Number.isFinite)) {
      problems.push(`ring ${r.folder} has a non-finite transform`);
      continue;
    }
    const centreDist = Math.hypot(r.cx, r.cy, r.cz);
    // nearest approach of the hoop to the hub; it must clear the core, or it reads as a great circle
    if (centreDist - r.radius < L.coreRadius * 0.55) {
      problems.push(`ring ${r.folder} passes through the core (${(centreDist - r.radius).toFixed(1)} vs core ${L.coreRadius})`);
    }
    if (!(r.arc[1] > r.arc[0])) problems.push(`ring ${r.folder} has an empty arc`);
  }

  // deeper shells must sit further out
  const byLevel = new Map();
  for (const r of rings) {
    if (!byLevel.has(r.level)) byLevel.set(r.level, []);
    byLevel.get(r.level).push(Math.hypot(r.cx, r.cy, r.cz) - r.radius);
  }
  const levels = [...byLevel.keys()].sort((a, b) => a - b);
  for (let i = 1; i < levels.length; i++) {
    const inner = Math.min(...byLevel.get(levels[i - 1]));
    const outer = Math.min(...byLevel.get(levels[i]));
    if (outer < inner - 1e-9) problems.push(`level ${levels[i]} sinks inside level ${levels[i - 1]}`);
  }

  // no two hoops may be identical
  const seen = new Map();
  for (const r of rings) {
    const key = [r.radius, r.rotX, r.rotY, r.rotZ, r.cx, r.cy, r.cz].map((v) => v.toFixed(2)).join("|");
    if (seen.has(key)) problems.push(`rings ${seen.get(key)} and ${r.folder} are identical`);
    seen.set(key, r.folder);
  }

  // orientations and heights must actually vary, or the scene reads as one flat disc again
  if (rings.length >= 4) {
    for (const axis of ["rotX", "rotZ"]) {
      const vals = rings.map((r) => r[axis]);
      if (Math.max(...vals) - Math.min(...vals) < 0.3) problems.push(`hoops barely vary in ${axis}`);
    }
    const heights = rings.map((r) => r.cy);
    if (Math.max(...heights) - Math.min(...heights) < 1) problems.push("every hoop sits at the same height");
  }

  for (let i = 0; i < L.pos.length; i++) {
    if (!Number.isFinite(L.pos[i])) { problems.push(`non-finite position at index ${i}`); break; }
  }
  return problems;
}
