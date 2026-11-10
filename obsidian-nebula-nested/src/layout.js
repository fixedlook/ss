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
// FNV-1a followed by a murmur3 fmix32 finalizer. Without the finalizer, strings differing only in
// their last character ("Top0".."Top3") hash to almost the same value and every ring gets the same
// tilt — which is exactly the coplanar-rings bug this replaced.
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
 * A ring is built in its local xz-plane, then spun by `spin` around its own axis, then tilted by
 * `polar` away from the world's equatorial plane and rotated by `azim` around the vertical.
 * Equivalent to THREE.Euler order "YZX" with (polar, azim, spin).
 */
export function tiltPoint(x, y, z, ring, out) {
  // 1. spin around the ring's own axis (local y)
  const cs = Math.cos(ring.spin || 0), ss = Math.sin(ring.spin || 0);
  const sx = x * cs + z * ss, sz = -x * ss + z * cs;
  // 2. rotate around world Y (azimuth) — must use the *spun* sz, not the original z
  const ca = Math.cos(ring.azim), sa = Math.sin(ring.azim);
  const ax = sx * ca + sz * sa, az = -sx * sa + sz * ca;
  // 3. tilt away from the equatorial plane around world X (polar)
  const cp = Math.cos(ring.polar), sp = Math.sin(ring.polar);
  out[0] = ax;
  out[1] = y * cp - az * sp;
  out[2] = y * sp + az * cp;
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
  const tiltStep = opt.tiltStep == null ? 0.02 : opt.tiltStep;
  const minTilt = opt.minTilt == null ? 0.3 : opt.minTilt;
  const maxTilt = opt.maxTilt == null ? 1.5 : opt.maxTilt;

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

  // --- 3. rings: each ring gets its OWN random 3D orientation ------------------------------
  // The original approach (a shared tilt per level) made every ring coplanar, which reads as one
  // flat disc. Here each ring picks a random tilt away from the equatorial plane, so the nest looks
  // like a swarm of orbits. tiltStep is kept only as a tiny bias so deeper rings lean further out.
  const rings = [];
  const ringOf = new Map();
  folders.forEach((f, idx) => {
    const n = nodes.get(f);
    const lv = n.level;
    // random polar angle in [minTilt, maxTilt]; sqrt keeps it off the equator.
    // stir the hash so it decorrelates from radius/sibling index, then de-cluster neighbours.
    const u = unit("polar:" + f);
    // bias toward the middle of the band so rings lean steeply and never look coplanar
    const polar = minTilt + Math.sqrt(u) * (maxTilt - minTilt) + tiltStep * (lv - 1);
    // random spin around the vertical axis
    const azim = unit("azim:" + f) * TAU;
    const ring = {
      folder: f,
      name: n.name,
      radius: n.radius,
      level: lv,
      parent: n.parent,
      count: n.notes.length,
      descendants: n.descendants,
      arc: [n.wedge[0], n.wedge[1]],
      polar, azim,
      // slow rotation of the whole ring around its own normal
      orbitSpeed: (unit("spin:" + f) - 0.5) * 0.5,
      orbitPhase: unit("phase:" + f) * TAU,
      slot: -1,
    };
    // de-cluster: if this ring's tilt lands too close to a sibling's, walk it along a golden-angle
    // ladder until it is clear. Deterministic, and the ladder step (2.399 rad) never re-collides.
    const siblings = rings.filter((o) => o.parent === n.parent && o.level === lv);
    for (let guard = 0; guard < 12; guard++) {
      const clash = siblings.some((o) => Math.abs(o.polar - ring.polar) < 0.34);
      if (!clash) break;
      ring.polar = minTilt + (((ring.polar - minTilt) / (maxTilt - minTilt) + 0.618034) % 1) * (maxTilt - minTilt);
    }
    // keep the spin direction varied too, so neighbouring rings do not rotate in lockstep
    if (siblings.length && unit("dir:" + f) < 0.5) ring.orbitSpeed = -ring.orbitSpeed;

    rings.push(ring);
    ringOf.set(f, ring);
  });

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

  const localByRing = new Map();
  let slot = 1;
  for (const [path, folder] of noteList) {
    const ring = ringOf.get(folder);
    const R = rngFrom(hash(path));
    if (ring) {
      if (!localByRing.has(ring.folder)) localByRing.set(ring.folder, []);
      const localNotes = localByRing.get(ring.folder);
      const [a0, a1] = ring.arc;
      const th = a0 + R() * Math.max(1e-4, a1 - a0);
      // thin band around the arc, so each folder's stars read as one ellipse segment
      const band = clamp(0.7 + Math.sqrt(ring.count) * 0.3, 0.8, 2.6);
      const rr = ring.radius + (R() - 0.5) * band * 2;
      const y = (R() - 0.5) * band * 0.7;
      tiltPoint(Math.cos(th) * rr, y, Math.sin(th) * rr, ring, tmp);
      pos.set(tmp, slot * 3);
      localNotes.push([slot, rr, th, y]);
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
    tiltPoint(Math.cos(mid) * ring.radius, 0, Math.sin(mid) * ring.radius, ring, tmp);
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
    notes: noteList.map(([p]) => p), slotToNote, noteSlot, localByRing,
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
    tiltPoint(Math.cos(th) * ring.radius, 0, Math.sin(th) * ring.radius, ring, tmp);
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
    if (![r.radius, r.polar, r.azim].every(Number.isFinite)) problems.push(`ring ${r.folder} has a non-finite transform`);
    if (r.polar > Math.PI / 2 + 0.01) problems.push(`ring ${r.folder} is tilted past vertical (${r.polar.toFixed(2)})`);
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
  // no two rings may be geometrically identical (same radius AND same orientation)
  const seen = new Map();
  for (const r of rings) {
    const key = `${r.radius.toFixed(2)}|${r.polar.toFixed(3)}|${r.azim.toFixed(3)}`;
    if (seen.has(key)) problems.push(`rings ${seen.get(key)} and ${r.folder} are identical`);
    seen.set(key, r.folder);
  }
  // rings should not all be coplanar: that was the old bug, so assert some spread exists
  if (rings.length >= 4) {
    const polars = rings.map((r) => r.polar);
    const spread = Math.max(...polars) - Math.min(...polars);
    if (spread < 0.25) problems.push(`rings are nearly coplanar (polar spread ${spread.toFixed(3)})`);
  }
  for (let i = 0; i < L.pos.length; i++) {
    if (!Number.isFinite(L.pos[i])) { problems.push(`non-finite position at index ${i}`); break; }
  }
  return problems;
}
