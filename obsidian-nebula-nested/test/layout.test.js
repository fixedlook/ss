import { test } from "node:test";
import assert from "node:assert/strict";
import { buildTree, outline, ROOT } from "../src/model.js";
import { layout, auditLayout, ringPoints } from "../src/layout.js";

// A vault shaped like the one this was built for: numbered top folders, most of which are
// containers, some of which hold both notes and subfolders.
const VAULT = [
  "Home.md",
  "MOC Trading.md",
  "01 OptionsFlow/Greeks/Delta.md",
  "01 OptionsFlow/Greeks/Gamma.md",
  "01 OptionsFlow/Greeks/Vanna.md",
  "01 OptionsFlow/Greeks/Charm.md",
  "01 OptionsFlow/Dealer Positioning/GEX.md",
  "01 OptionsFlow/Dealer Positioning/Gamma Flip (Zero-Gamma).md",
  "01 OptionsFlow/Dealer Positioning/Call Wall & Put Wall.md",
  "01 OptionsFlow/Dealer Positioning/0DTE Gamma.md",
  "01 OptionsFlow/Volatility/VIX.md",
  "01 OptionsFlow/Volatility/Vanna Rally.md",
  "02 Market Structure/Instruments/NQ vs ES vs NDX vs QQQ.md",
  "03 Auction Theory/TPO Mechanics/30-Min Bracket.md",
  "03 Auction Theory/TPO Mechanics/TPO vs Volume Profile.md",
  "03 Auction Theory/Levels/HVL.md",
  "04 Order Flow/Absorption & Exhaustion/Absorption — Definition.md",
  "04 Order Flow/Absorption & Exhaustion/When Absorption Fails.md",
  "04 Order Flow/Footprint/Bid x Ask.md",
  "05 Regime/Positive Gamma Day.md",
  "05 Regime/Negative Gamma Day.md",
  "05 Regime/Day Types/Normal.md",
  "99 Debunked/ICT Liquidity.md",
];

test("every folder that holds notes gets a ring, pure containers are collapsed", () => {
  const t = buildTree(VAULT, { maxDepth: 3 });
  assert.ok(!t.nodes.has("01 OptionsFlow"), "container folder must not claim a ring");
  assert.ok(!t.nodes.has("04 Order Flow"), "container folder must not claim a ring");
  assert.ok(t.nodes.has("01 OptionsFlow/Greeks"));
  assert.ok(t.nodes.has("04 Order Flow/Absorption & Exhaustion"));
  assert.equal(t.nodes.get("01 OptionsFlow/Greeks").notes.length, 4);
});

test("children of a collapsed container are reparented to the hub, not orphaned", () => {
  const t = buildTree(VAULT, { maxDepth: 3 });
  assert.equal(t.nodes.get("01 OptionsFlow/Greeks").parent, ROOT);
  assert.equal(t.nodes.get("01 OptionsFlow/Greeks").level, 1);
  assert.equal(t.nodes.get("04 Order Flow/Footprint").parent, ROOT);
});

test("real nesting is preserved when the parent also holds notes", () => {
  const t = buildTree(VAULT, { maxDepth: 3 });
  const regime = t.nodes.get("05 Regime");
  const dayTypes = t.nodes.get("05 Regime/Day Types");
  assert.equal(regime.level, 1);
  assert.equal(dayTypes.level, 2);
  assert.equal(dayTypes.parent, "05 Regime");
  assert.equal(regime.descendants, 3);
});

test("maxDepth prunes deeper folders", () => {
  const t = buildTree(VAULT, { maxDepth: 1 });
  for (const f of t.order) assert.ok(!f.includes("/"), `unexpected deep ring ${f}`);
});

test("maxRings keeps the biggest and reports the rest", () => {
  const t = buildTree(VAULT, { maxDepth: 3, maxRings: 3 });
  assert.equal(t.order.length, 3);
  assert.ok(t.truncated > 0);
  const counts = t.order.map((f) => t.nodes.get(f).notes.length).sort((a, b) => b - a);
  assert.equal(counts[0], 4); // Greeks
});

test("layout passes its own audit and is genuinely nested", () => {
  const t = buildTree(VAULT, { maxDepth: 3 });
  const L = layout(t, [], {});
  assert.deepEqual(auditLayout(L), []);
  assert.ok(L.rings.length > 5);
  assert.equal(L.depths.levels, 2, "the fixture nests exactly two levels deep");
});

test("each nesting level sits outside its parent level", () => {
  const t = buildTree(VAULT, { maxDepth: 3 });
  const L = layout(t, [], {});
  for (const r of L.rings) {
    if (r.parent === ROOT) continue;
    const parent = L.ringOf.get(r.parent);
    assert.ok(r.radius > parent.radius, `${r.folder} (R ${r.radius}) must sit outside its parent ${r.parent} (R ${parent.radius})`);
  }
});

test("no two rings share a radius (that is what makes circles intersect)", () => {
  // two circles at equal radius in different planes always cross at exactly two points: the
  // "petals"/"atom" look. Radii must stay separated.
  const files = [];
  for (let d = 0; d < 6; d++) {
    files.push(`Top${d}/n.md`);
    for (let s = 0; s < 4; s++) files.push(`Top${d}/Sub${s}/x.md`);
  }
  const L = layout(buildTree(files, { maxDepth: 3 }), [], {});
  const sorted = L.rings.slice().sort((a, b) => a.radius - b.radius);
  for (let i = 1; i < sorted.length; i++) {
    assert.ok(sorted[i].radius - sorted[i - 1].radius > 1.5,
      `${sorted[i - 1].folder} and ${sorted[i].folder} are ${(sorted[i].radius - sorted[i - 1].radius).toFixed(2)} apart`);
  }
  assert.deepEqual(auditLayout(L), []);
});

test("sibling arcs tile their parent's circle without overlapping", () => {
  const t = buildTree(VAULT, { maxDepth: 3 });
  const L = layout(t, [], {});
  for (const [parent, kids] of t.siblings) {
    const rings = kids.map((f) => L.ringOf.get(f)).filter(Boolean).sort((a, b) => a.arc[0] - b.arc[1] || a.arc[0] - b.arc[0]);
    rings.sort((a, b) => a.arc[0] - b.arc[0]);
    for (let i = 1; i < rings.length; i++) {
      assert.ok(Math.abs(rings[i].arc[0] - rings[i - 1].arc[1]) < 1e-9, `gap or overlap under ${parent || "root"}`);
    }
  }
});

test("a child's arc sits inside its parent's arc", () => {
  const t = buildTree(["A/a.md", "A/B/b.md", "A/B/C/c.md", "D/d.md"], { maxDepth: 4 });
  const L = layout(t, [], {});
  for (const r of L.rings) {
    if (r.parent === ROOT) continue;
    const p = L.ringOf.get(r.parent);
    assert.ok(r.arc[0] >= p.arc[0] - 1e-9 && r.arc[1] <= p.arc[1] + 1e-9, `${r.folder} escapes ${r.parent}`);
  }
  assert.equal(L.depths.levels, 3);
  assert.deepEqual(auditLayout(L), []);
});

test("notes sit outside the hub and inside the world", () => {
  const t = buildTree(VAULT, { maxDepth: 3 });
  const L = layout(t, [], {});
  for (const ring of L.rings) {
    for (const notePath of t.nodes.get(ring.folder).notes) {
      const s = L.noteSlot.get(notePath);
      assert.ok(s, `no slot for ${notePath}`);
      const d = Math.hypot(L.pos[s * 3], L.pos[s * 3 + 1], L.pos[s * 3 + 2]);
      assert.ok(d > L.coreRadius, `${notePath} sits inside the hub`);
      assert.ok(d < L.far * 1.25, `${notePath} sits outside the world`);
    }
  }
});

test("layout is deterministic for the same input", () => {
  const a = layout(buildTree(VAULT, { maxDepth: 3 }), [], {});
  const b = layout(buildTree(VAULT, { maxDepth: 3 }), [], {});
  assert.deepEqual([...a.pos], [...b.pos]);
});

test("edges resolve against the slot table and drop broken links", () => {
  const t = buildTree(VAULT, { maxDepth: 3 });
  const L = layout(t, [
    ["01 OptionsFlow/Greeks/Gamma.md", "01 OptionsFlow/Dealer Positioning/GEX.md"],
    ["05 Regime/Positive Gamma Day.md", "04 Order Flow/Absorption & Exhaustion/When Absorption Fails.md"],
    ["does/not/exist.md", "05 Regime/Negative Gamma Day.md"],
  ], {});
  assert.equal(L.edges.length, 2, "unresolvable links must be dropped");
  for (const [x, y] of L.edges) assert.ok(x > 0 && y > 0);
  // degree drives node size: linked notes must be bigger than unlinked ones
  const linked = L.noteSlot.get("01 OptionsFlow/Greeks/Gamma.md");
  const lonely = L.noteSlot.get("01 OptionsFlow/Greeks/Charm.md");
  assert.ok(L.aSize[linked] > L.aSize[lonely]);
});

test("empty and root-only vaults do not crash", () => {
  const empty = layout(buildTree([], {}), [], {});
  assert.equal(empty.rings.length, 0);
  assert.deepEqual(auditLayout(empty), []);
  const rootOnly = layout(buildTree(["a.md", "b.md"], { includeRootNotes: true }), [], {});
  assert.equal(rootOnly.depths.loose, 2);
  assert.deepEqual(auditLayout(rootOnly), []);
});

test("a vault with only containers and no notes anywhere does not crash", () => {
  const L = layout(buildTree(["A/B/c.md"], { maxDepth: 1 }), [], {});
  assert.equal(L.rings.length, 0);
  assert.deepEqual(auditLayout(L), []);
});

test("ringPoints stays on its hoop, full circle and arc", () => {
  const t = buildTree(["A/x.md"], {});
  const L = layout(t, [], {});
  const ring = L.rings[0];
  const centre = [ring.cx, ring.cy, ring.cz];
  for (const pts of [ringPoints(ring, 64), ringPoints(ring, 48, ring.arc)]) {
    const seg = pts.length / 3;
    for (let i = 0; i < seg; i++) {
      const d = Math.hypot(pts[i * 3] - centre[0], pts[i * 3 + 1] - centre[1], pts[i * 3 + 2] - centre[2]);
      assert.ok(Math.abs(d - ring.radius) < 1e-4, `point ${i} is ${d} from its hoop centre, expected ${ring.radius}`);
    }
  }
});

test("outline renders a tree", () => {
  const t = buildTree(["A/x.md", "A/B/y.md", "A/B/z.md"], {});
  const text = outline(t);
  assert.match(text, /A\/\s+\(1 direct, 3 total\)/);
  assert.match(text, /^ {2}B\/\s+\(2\)/m);
});

test("scales to a large vault without exploding", () => {
  const files = [];
  // a note in every top folder as well, otherwise the top folders are pure containers and
  // (correctly) collapse into a single level
  for (let d = 0; d < 12; d++) {
    files.push(`F${d}/index.md`);
    for (let s = 0; s < 8; s++) for (let n = 0; n < 10; n++) files.push(`F${d}/S${s}/${n}.md`);
  }
  const t = buildTree(files, { maxDepth: 3, maxRings: 200 });
  const L = layout(t, [], {});
  assert.ok(L.rings.length > 20);
  assert.deepEqual(auditLayout(L), []);
  assert.equal(L.depths.levels, 2);
  assert.equal(L.depths.rings, 108);
});

test("very deep nesting still lays out sanely", () => {
  // deep chains collapse to one level unless intermediate folders hold their own notes
  const collapsed = layout(buildTree(["a/b/c/d/e/f/note.md"], { maxDepth: 6 }), [], {});
  assert.equal(collapsed.depths.levels, 1);
  assert.deepEqual(auditLayout(collapsed), []);

  // with a note at every level, the nesting is real
  const deep = [];
  const parts = ["a", "a/b", "a/b/c", "a/b/c/d", "a/b/c/d/e", "a/b/c/d/e/f"];
  for (const p of parts) deep.push(`${p}/note.md`);
  const t = buildTree(deep, { maxDepth: 6 });
  const L = layout(t, [], {});
  assert.equal(L.depths.levels, 6);
  assert.deepEqual(auditLayout(L), []);
});

// ---- sceneFromVault: the mapping the plugin uses on every rebuild ----------------------
import { sceneFromVault } from "../src/model.js";

test("sceneFromVault drops links to pruned notes and keeps the rest", () => {
  const files = ["A/one.md", "A/two.md", "B/deep/x.md", "C/hidden.md"];
  const resolved = {
    "A/one.md": { "A/two.md": "A/two.md", "B/deep/x.md": "B/deep/x.md" },
    "A/two.md": { "B/deep/x.md": "B/deep/x.md" },
    "B/deep/x.md": { "A/one.md": "A/one.md" },
    "C/hidden.md": { "A/one.md": "A/one.md" },
  };
  // depth 1 -> "B/deep" (a real level-2 folder) is pruned, so x.md must be neither a node nor an edge
  const { tree, links, inScene } = sceneFromVault(files, resolved, { maxDepth: 1 });
  assert.ok(tree.nodes.has("A"));
  assert.ok(!tree.nodes.has("B/deep"));
  assert.ok(!inScene.has("B/deep/x.md"), "pruned note must not enter the scene");
  assert.deepEqual(links, [
    ["A/one.md", "A/two.md"],
    ["C/hidden.md", "A/one.md"],
  ]);
});

test("sceneFromVault includes root notes only when asked", () => {
  const files = ["Home.md", "A/x.md"];
  const withoutRoot = sceneFromVault(files, {}, {});
  assert.equal(withoutRoot.inScene.has("Home.md"), false);
  const withRoot = sceneFromVault(files, {}, { includeRootNotes: true });
  assert.equal(withRoot.inScene.has("Home.md"), true);
});

test("sceneFromVault survives a missing or empty resolvedLinks map", () => {
  const a = sceneFromVault(["A/x.md"], undefined, {});
  assert.deepEqual(a.links, []);
  const b = sceneFromVault([], {}, {});
  assert.equal(b.tree.order.length, 0);
  assert.deepEqual(layout(b.tree, b.links, {}).rings, []);
});

test("a link to a note in the same folder is kept and resolves to a real edge", () => {
  const files = ["A/one.md", "A/two.md"];
  const { tree, links } = sceneFromVault(files, { "A/one.md": { "A/two.md": "A/two.md" } }, {});
  const L = layout(tree, links, {});
  assert.equal(L.edges.length, 1);
  assert.equal(L.rings.length, 1);
});

// ---- regressions from real-device feedback --------------------------------------------

test("rings vary in both orientation and radius, and are anchored to the hub", () => {
  const files = [];
  for (let i = 0; i < 12; i++) files.push(`Top${i}/n.md`);
  const L = layout(buildTree(files, { maxDepth: 2 }), [], {});
  const spread = (vals) => Math.max(...vals) - Math.min(...vals);

  assert.ok(spread(L.rings.map((r) => r.rotX)) > 0.5, "pitch must vary");
  assert.ok(spread(L.rings.map((r) => r.rotY)) > 1, "yaw must be distinct");
  assert.ok(spread(L.rings.map((r) => r.radius)) > 20, "radii must vary enough to avoid intersections");
  // anchored: the innermost ring sits just outside the core, not off in space
  const inner = Math.min(...L.rings.map((r) => r.radius));
  assert.ok(inner < L.coreRadius * 2.2, `innermost ring (${inner.toFixed(1)}) must hug the core (${L.coreRadius})`);
  // pitch stays off the two degenerate angles
  for (const r of L.rings) assert.ok(r.rotX > 0.1 && r.rotX < Math.PI / 2, `pitch ${r.rotX} would render as a line or a perfect circle`);
  assert.deepEqual(auditLayout(L), []);
});

test("the hash finalizer de-clusters similar strings", () => {
  // FNV-1a alone maps "Top0".."Top3" to almost the same value; the fmix32 finalizer fixes it
  const files = ["Top0/n.md", "Top1/n.md", "Top2/n.md", "Top3/n.md"];
  const L = layout(buildTree(files, { maxDepth: 1 }), [], {});
  const rounded = L.rings.map((r) => r.rotZ.toFixed(2));
  assert.equal(new Set(rounded).size, 4, "four folders must produce four distinct orientations");
});

test("spinning a hoop preserves its radius and moves its points", () => {
  const L = layout(buildTree(["A/a.md", "A/b.md"], {}), [], {});
  const ring = L.rings[0];
  const before = ringPoints(ring, 16);
  const r0 = Math.hypot(before[0] - ring.cx, before[1] - ring.cy, before[2] - ring.cz);
  ring.spin = 0.8;
  const after = ringPoints(ring, 16);
  const r1 = Math.hypot(after[0] - ring.cx, after[1] - ring.cy, after[2] - ring.cz);
  assert.ok(Math.abs(r0 - r1) < 1e-5, "radius must survive the spin");
  let moved = 0;
  for (let i = 0; i < before.length; i += 3) {
    if (Math.hypot(before[i] - after[i], before[i + 1] - after[i + 1], before[i + 2] - after[i + 2]) > 1e-4) moved++;
  }
  assert.equal(moved, 16, "every point must actually move");
});

test("nodes ride their hoop, at the hoop's radius", () => {
  const L = layout(buildTree(["A/a.md", "A/b.md", "A/c.md"], {}), [], {});
  const ring = L.rings[0];
  const local = L.localByRing.get(ring.folder);
  assert.ok(local && local.length === 3);
  for (const [slot, r, , y] of local) {
    const d = Math.hypot(L.pos[slot * 3] - ring.cx, L.pos[slot * 3 + 1] - ring.cy, L.pos[slot * 3 + 2] - ring.cz);
    // stars sit on the hoop plus a small vertical lift, so the expected distance is hypot(r, y)
    assert.ok(Math.abs(d - Math.hypot(r, y)) < 1e-3, `node distance ${d} should be hypot(r,y)=${Math.hypot(r, y)}`);
    assert.ok(Math.abs(r - ring.radius) < 1.8, "node must stay within the hoop's band");
  }
});

test("audit catches two rings sharing a radius (the intersection bug)", () => {
  const L = layout(buildTree(["A/a.md", "B/b.md", "C/c.md", "D/d.md"], {}), [], {});
  L.rings[1].radius = L.rings[0].radius; // force an intersection
  const problems = auditLayout(L);
  assert.ok(problems.some((p) => /share a radius/.test(p)), `expected a radius complaint, got ${JSON.stringify(problems)}`);
});

test("audit catches a detached nest", () => {
  const L = layout(buildTree(["A/a.md", "B/b.md", "C/c.md", "D/d.md"], {}), [], {});
  for (const r of L.rings) r.radius *= 4; // float everything away from the hub
  const problems = auditLayout(L);
  assert.ok(problems.some((p) => /detached/.test(p)), `expected a detachment complaint, got ${JSON.stringify(problems)}`);
});

// ---- camera framing: the nest must fit the viewport at any aspect ratio ---------------
import { cameraFit } from "../src/layout.js";

/** project every ring point through a look-at-origin camera and return the worst NDC extent */
function projectionExtent(L, fovDeg, aspect, elev = 0.30) {
  const { distance: dist } = cameraFit(L.maxExtent, fovDeg, aspect);
  const cam = [0, Math.sin(elev) * dist, Math.cos(elev) * dist];
  const zAxis = [0 - cam[0], 0 - cam[1], 0 - cam[2]];
  const zn = Math.hypot(...zAxis);
  for (let i = 0; i < 3; i++) zAxis[i] /= zn;
  const up = [0, 1, 0];
  const xAxis = [up[1] * zAxis[2] - up[2] * zAxis[1], up[2] * zAxis[0] - up[0] * zAxis[2], up[0] * zAxis[1] - up[1] * zAxis[0]];
  const xn = Math.hypot(...xAxis);
  for (let i = 0; i < 3; i++) xAxis[i] /= xn;
  const yAxis = [
    zAxis[1] * xAxis[2] - zAxis[2] * xAxis[1],
    zAxis[2] * xAxis[0] - zAxis[0] * xAxis[2],
    zAxis[0] * xAxis[1] - zAxis[1] * xAxis[0],
  ];
  const vFov = (fovDeg * Math.PI) / 180;
  const hFov = 2 * Math.atan(Math.tan(vFov / 2) * aspect);
  let maxX = 0, maxY = 0;
  for (const ring of L.rings) {
    const pts = ringPoints(ring, 48);
    for (let i = 0; i < pts.length; i += 3) {
      const d = [pts[i] - cam[0], pts[i + 1] - cam[1], pts[i + 2] - cam[2]];
      const zc = -(d[0] * zAxis[0] + d[1] * zAxis[1] + d[2] * zAxis[2]);
      const xc = d[0] * xAxis[0] + d[1] * xAxis[1] + d[2] * xAxis[2];
      const yc = d[0] * yAxis[0] + d[1] * yAxis[1] + d[2] * yAxis[2];
      maxX = Math.max(maxX, Math.abs(xc / -zc / Math.tan(hFov / 2)));
      maxY = Math.max(maxY, Math.abs(yc / -zc / Math.tan(vFov / 2)));
    }
  }
  return { maxX, maxY, dist };
}

const FRAMING_VAULT = (() => {
  const files = [];
  for (let i = 0; i < 8; i++) files.push(`Top${i}/n.md`);
  for (let s = 0; s < 3; s++) for (let j = 0; j < 4; j++) files.push(`Top${s}/Sub${j}/x.md`);
  return files;
})();

test("the fitted camera keeps every ring inside the frame", () => {
  const L = layout(buildTree(FRAMING_VAULT, { maxDepth: 3 }), [], { coreRadius: 26 });
  for (const aspect of [0.7, 1.0, 1.6, 2.4, 3.2]) {
    const { maxX, maxY } = projectionExtent(L, 52, aspect);
    assert.ok(maxY < 0.98, `aspect ${aspect}: rings clip vertically (|ndcY| ${maxY.toFixed(3)})`);
    assert.ok(maxX < 0.98, `aspect ${aspect}: rings clip horizontally (|ndcX| ${maxX.toFixed(3)})`);
  }
});

test("the fitted camera is not wastefully far: the nest fills most of the frame", () => {
  const L = layout(buildTree(FRAMING_VAULT, { maxDepth: 3 }), [], { coreRadius: 26 });
  const { maxY } = projectionExtent(L, 52, 1.6);
  assert.ok(maxY > 0.6, `nest is too small in frame (|ndcY| ${maxY.toFixed(3)}); it was 1.0+ (clipped) before the fit`);
});

test("cameraFit responds to aspect ratio", () => {
  const wide = cameraFit(100, 52, 2.4);
  const tall = cameraFit(100, 52, 0.7);
  assert.ok(tall.distance > wide.distance, "a tall viewport needs a further camera");
  assert.ok(wide.distance > 0 && Number.isFinite(wide.distance));
  assert.equal(cameraFit(0, 52, 1.6).distance > 0, true, "zero extent must not produce NaN");
});
