// Ring/nesting geometry. Pure math, no three.js: produces the position buffers the renderer uploads.
//
// The thing that differs from the original Nebula: rings are not all children of the hub.
// Each folder's ring is a *child* orbit of its parent folder's ring, allocated an angular wedge of
// the parent's circle proportional to how many notes live beneath it. Depth reads as distance from
// the centre: every nesting level gets its own shell. Within a shell, sibling folders own
// contiguous arcs, so two branches never fight for the same space.
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
const hash = (s) => { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; };
const unit = (s) => hash(s) / 4294967296;

/** rotate a point by Euler (x, 0, z) in XYZ order — same as THREE.Euler(tiltX, 0, tiltZ, "XYZ") */
function tiltPoint(x, y, z, tiltX, tiltZ, out) {
  const cy = Math.cos(tiltX), sy = Math.sin(tiltX);
  const y1 = y * cy - z * sy, z1 = y * sy + z * cy;
  const cz = Math.cos(tiltZ), sz = Math.sin(tiltZ);
  out[0] = x * cz - y1 * sz; out[1] = x * sz + y1 * cz; out[2] = z1;
  return out;
}

/**
 * @param {ReturnType<import("./model.js").buildTree>} tree
 * @param {[string,string][]} links resolved [[fromPath, toPath], ...] (.md paths)
 * @param {object} opt  {start, perLevel, gap, coreRadius, jitter, tiltStep}
 */
export function layout(tree, links = [], opt = {}) {
  const start = opt.start == null ? 0 : opt.start;
  const perLevel = opt.perLevel == null ? 9 : opt.perLevel;
  const gap = opt.gap == null ? 6 : opt.gap;
  const coreRadius = opt.coreRadius == null ? 26 : opt.coreRadius;
  const jitter = opt.jitter == null ? 1.6 : opt.jitter;
  const tiltStep = opt.tiltStep == null ? 0.055 : opt.tiltStep;

  const nodes = tree.nodes;
  const folders = tree.order;
  const sizeOf = (f) => Math.max(1, nodes.get(f).descendants || nodes.get(f).notes.length || 1);

  // --- 1. shells: one radius per nesting level, growing with how crowded that level is ------
  const maxLevel = folders.reduce((m, f) => Math.max(m, nodes.get(f).level), 0);
  const ringsAtLevel = new Array(maxLevel + 1).fill(0);
  for (const f of folders) ringsAtLevel[nodes.get(f).level]++;

  const shell = new Array(maxLevel + 1).fill(coreRadius);
  for (let lv = 1; lv <= maxLevel; lv++) {
    // a shell with many arcs needs more circumference to stay readable
    const crowd = perLevel * 0.5 * Math.sqrt(Math.max(1, ringsAtLevel[lv]));
    shell[lv] = shell[lv - 1] + Math.max(perLevel, gap + 2) + crowd - perLevel * 0.5;
  }

  // --- 2. allocate wedges: children tile their parent's arc -------------------------------
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
      // spread siblings across the band by index (guaranteed distinct), plus a small hash nudge
      const frac = kids.length > 1 ? i / (kids.length - 1) - 0.5 : 0;
      n.radius = shell[n.level] + frac * jitter + (unit("r:" + f) - 0.5) * 0.25;
      n.sibIdx = i;
      cursor = a1;
      process(f, a0, w);
    });
  };
  process(ROOT, start, TAU);

  // --- 3. rings: per-level tilt (a shell reads as one tilted orbit) + a little variation ---
  const rings = [];
  const ringOf = new Map();
  for (const f of folders) {
    const n = nodes.get(f);
    const lv = n.level;
    const sign = unit("s:" + f) < 0.5 ? -1 : 1;
    const ring = {
      folder: f,
      name: n.name,
      radius: n.radius,
      level: lv,
      parent: n.parent,
      count: n.notes.length,
      descendants: n.descendants,
      arc: [n.wedge[0], n.wedge[1]],
      tiltX: sign * (0.15 + tiltStep * (lv - 1)) + (unit("tx:" + f) - 0.5) * 0.05,
      tiltZ: 0.1 * (lv - 1) + n.sibIdx * 0.03 + (unit("tz:" + f) - 0.5) * 0.12,
      slot: -1,
    };
    rings.push(ring);
    ringOf.set(f, ring);
  }

  // --- 4. slots: 0 = core, then notes, then folder hubs ---------------------------------
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
  const tmp = [0, 0, 0];
  const scale = Math.min(2.4, Math.max(0.5, 3.0 / Math.pow(Math.max(N, 50) / 100, 0.3)));

  let slot = 1;
  for (const [path, folder] of noteList) {
    const ring = ringOf.get(folder);
    const R = rngFrom(hash(path));
    if (ring) {
      const [a0, a1] = ring.arc;
      const th = a0 + R() * Math.max(1e-4, a1 - a0);
      // thin band around the arc, so each folder's stars read as one ellipse segment
      const band = clamp(0.7 + Math.sqrt(ring.count) * 0.3, 0.8, 2.6);
      const rr = ring.radius + (R() - 0.5) * band * 2;
      const y = (R() - 0.5) * band * 0.7;
      tiltPoint(Math.cos(th) * rr, y, Math.sin(th) * rr, ring.tiltX, ring.tiltZ, tmp);
      pos.set(tmp, slot * 3);
    } else {
      // loose root note: a slow halo directly around the hub
      const th = R() * TAU, rr = coreRadius * (1.3 + R() * 0.7);
      pos.set([Math.cos(th) * rr, (R() - 0.5) * 5, Math.sin(th) * rr], slot * 3);
    }
    aSize[slot] = 1.3 * scale; // resized after degrees are known
    phase[slot] = R() * TAU;
    noteSlot.set(path, slot);
    slotToNote[slot] = { id: path, name: path.slice(path.lastIndexOf("/") + 1).replace(/\.md$/, ""), folder };
    slot++;
  }

  for (const ring of rings) {
    const s = slot++;
    const R = rngFrom(hash("hub:" + ring.folder));
    const mid = (ring.arc[0] + ring.arc[1]) / 2;
    tiltPoint(Math.cos(mid) * ring.radius, 0, Math.sin(mid) * ring.radius, ring.tiltX, ring.tiltZ, tmp);
    pos.set(tmp, s * 3);
    aSize[s] = 4.0 * scale;
    phase[s] = R() * TAU;
    ring.slot = s;
  }

  // --- 5. edges + degree-driven node sizes ---------------------------------------------
  const edges = [];
  const deg = new Float32Array(total);
  for (const [a, b] of links) {
    const x = noteSlot.get(a), y = noteSlot.get(b);
    if (!x || !y || x === y) continue;
    edges.push([x, y]); deg[x]++; deg[y]++;
  }
  for (let s = 1; s <= N; s++) aSize[s] = (1.3 + Math.min(4.2, Math.sqrt(deg[s]) * 0.8)) * scale;

  const maxR = rings.reduce((m, r) => Math.max(m, r.radius), coreRadius);
  const far = Math.max(60, maxR * 1.25);

  return {
    pos, aSize, phase, total, edges, deg,
    notes: noteList.map(([p]) => p), slotToNote, noteSlot,
    rings, ringOf, radius: new Map(folders.map((f) => [f, nodes.get(f).radius])).set(ROOT, coreRadius),
    far, scale, coreRadius, shell, maxLevel,
    depths: {
      levels: maxLevel,
      rings: rings.length,
      notes: N,
      loose: (tree.loose || []).length,
    },
  };
}

/**
 * Points along a ring, already tilted into world space.
 * @param {object} ring
 * @param {number} seg  number of segments
 * @param {[number,number]} [range] angular range; defaults to the full circle
 */
export function ringPoints(ring, seg = 200, range) {
  const out = new Float32Array(seg * 3);
  const tmp = [0, 0, 0];
  const a0 = range ? range[0] : 0;
  const span = range ? range[1] - range[0] : TAU;
  const closed = !range;
  const n = closed ? seg : seg;
  for (let s = 0; s < n; s++) {
    const th = a0 + (s / n) * span;
    tiltPoint(Math.cos(th) * ring.radius, 0, Math.sin(th) * ring.radius, ring.tiltX, ring.tiltZ, tmp);
    out[s * 3] = tmp[0]; out[s * 3 + 1] = tmp[1]; out[s * 3 + 2] = tmp[2];
  }
  return out;
}

/** Belt-and-braces check used by the tests and a startup assert. Returns a list of problems. */
export function auditLayout(L, minGap = 1) {
  const problems = [];
  const { rings } = L;
  for (const r of rings) {
    if (r.radius <= L.coreRadius) problems.push(`ring ${r.folder} inside the hub (${r.radius.toFixed(1)} <= ${L.coreRadius})`);
    if (r.parent !== ROOT) {
      const p = L.ringOf.get(r.parent);
      if (p && r.radius - p.radius < minGap) problems.push(`ring ${r.folder} too close to its parent ${r.parent} (${r.radius.toFixed(1)} vs ${p.radius.toFixed(1)})`);
    }
    if (!Number.isFinite(r.radius) || !Number.isFinite(r.tiltX) || !Number.isFinite(r.tiltZ)) problems.push(`ring ${r.folder} has a non-finite transform`);
    if (!(r.arc[1] > r.arc[0])) problems.push(`ring ${r.folder} has an empty arc`);
  }
  // deeper rings must sit strictly outside shallower ones
  const byLevel = new Map();
  for (const r of rings) {
    if (!byLevel.has(r.level)) byLevel.set(r.level, []);
    byLevel.get(r.level).push(r.radius);
  }
  const levels = [...byLevel.keys()].sort((a, b) => a - b);
  for (let i = 1; i < levels.length; i++) {
    const inner = Math.max(...byLevel.get(levels[i - 1]));
    const outer = Math.min(...byLevel.get(levels[i]));
    if (outer <= inner) problems.push(`level ${levels[i]} (min ${outer.toFixed(1)}) does not sit outside level ${levels[i - 1]} (max ${inner.toFixed(1)})`);
  }
  // no two rings may be geometrically identical
  const seen = new Map();
  for (const r of rings) {
    const key = `${r.radius.toFixed(2)}|${r.tiltX.toFixed(3)}|${r.tiltZ.toFixed(3)}`;
    if (seen.has(key)) problems.push(`rings ${seen.get(key)} and ${r.folder} are identical`);
    seen.set(key, r.folder);
  }
  for (let i = 0; i < L.pos.length; i++) {
    if (!Number.isFinite(L.pos[i])) { problems.push(`non-finite position at index ${i}`); break; }
  }
  return problems;
}
