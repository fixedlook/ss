// Vault -> folder tree. No Obsidian imports so the logic can be unit-tested in plain node.

export const ROOT = "";

const trueDepth = (p) => (p === ROOT ? 0 : p.split("/").length);
const parentOf = (p) => {
  const i = p.lastIndexOf("/");
  return i < 0 ? ROOT : p.slice(0, i);
};
const baseOf = (p) => p.slice(p.lastIndexOf("/") + 1);

/**
 * Build a folder tree from vault paths.
 *
 * - Every folder that directly holds at least one note is a "ring" folder.
 * - Folders that only contain other folders ("container" folders) are collapsed: they shape the
 *   hierarchy but do not get a ring of their own. This is what stops an empty `01 OptionsFlow/`
 *   from claiming a ring while its populated children sit inside it.
 * - A ring folder whose parent was collapsed is reparented to its nearest surviving ancestor, so
 *   wedges always have somewhere to live.
 *
 * @param {string[]} notePaths   e.g. ["OptionsFlow/Greeks/Gamma.md", "Home.md"]
 * @param {object}   opts
 * @param {number}   opts.maxDepth  deepest *true* folder level to include (1 = top folders only)
 * @param {number}   opts.maxRings  keep at most this many rings, biggest first
 * @param {boolean}  opts.includeRootNotes  put loose root notes in a halo around the hub
 * @param {Map<string,number>} [opts.weights] optional size override per folder (e.g. backlink count)
 * @returns {{nodes:Map, order:string[], siblings:Map, loose:string[], truncated:number, dropped:string[]}}
 */
export function buildTree(notePaths, opts = {}) {
  const maxDepth = opts.maxDepth == null ? 3 : opts.maxDepth;
  const maxRings = opts.maxRings == null ? 120 : opts.maxRings;
  const includeRootNotes = !!opts.includeRootNotes;

  const byFolder = new Map(); // folder path -> note paths, direct children only
  const ensure = (folder) => { if (!byFolder.has(folder)) byFolder.set(folder, []); };
  ensure(ROOT);

  for (const p of notePaths) {
    if (!p || p.startsWith(".")) continue;
    const folder = parentOf(p);
    // register every ancestor so containers exist in the tree even when their own notes are absent
    let f = folder;
    while (f !== ROOT) { ensure(f); f = parentOf(f); }
    ensure(folder);
    byFolder.get(folder).push(p);
  }

  const countOf = (f) => (byFolder.get(f) || []).length;

  // candidates: real folders, deep enough, that directly hold notes
  const candidates = [...byFolder.keys()].filter(
    (f) => f !== ROOT && trueDepth(f) <= maxDepth && countOf(f) > 0
  );

  // biggest first, then alphabetical, so the ranking is stable across reloads
  candidates.sort((a, b) => countOf(b) - countOf(a) || a.localeCompare(b));
  const truncated = Math.max(0, candidates.length - maxRings);
  const kept = candidates.slice(0, maxRings);
  const keptSet = new Set(kept);

  // reparent in true-depth order, so a parent's visual level is always known first
  const nodes = new Map();
  for (const f of kept.slice().sort((a, b) => trueDepth(a) - trueDepth(b) || a.localeCompare(b))) {
    let parent = parentOf(f);
    while (parent !== ROOT && !keptSet.has(parent)) parent = parentOf(parent);
    const pNode = parent === ROOT ? null : nodes.get(parent);
    nodes.set(f, {
      path: f,
      name: baseOf(f),
      depth: trueDepth(f),          // real folder depth
      level: pNode ? pNode.level + 1 : 1, // nesting level after collapsing containers
      parent,
      notes: (byFolder.get(f) || []).slice().sort(),
      descendants: 0,               // filled below
      angle: 0,                     // filled by layout()
      wedge: [0, 0],
      radius: 0,
    });
  }

  // descendants: notes in this folder or any kept folder beneath it
  const order = kept.slice().sort((a, b) => nodes.get(a).level - nodes.get(b).level || a.localeCompare(b));
  for (const f of order) nodes.get(f).descendants = countOf(f);
  for (const f of order.slice().reverse()) {
    const n = nodes.get(f);
    if (n.parent !== ROOT) nodes.get(n.parent).descendants += n.descendants;
  }

  // siblings, ordered biggest-first (this ordering drives the angular wedges)
  const siblings = new Map();
  for (const f of order) {
    const p = nodes.get(f).parent;
    if (!siblings.has(p)) siblings.set(p, []);
    siblings.get(p).push(f);
  }
  for (const list of siblings.values()) {
    list.sort((a, b) => nodes.get(b).descendants - nodes.get(a).descendants || a.localeCompare(b));
  }

  const loose = includeRootNotes ? byFolder.get(ROOT).slice().sort() : [];
  const dropped = candidates.slice(maxRings);

  return { nodes, order, siblings, loose, truncated, dropped, byFolder };
}

/** A compact text outline of the tree (used by the export command). */
export function outline(tree) {
  const lines = [];
  const walk = (parent, indent) => {
    for (const f of tree.siblings.get(parent) || []) {
      const n = tree.nodes.get(f);
      const suffix = n.descendants > n.notes.length ? `${n.notes.length} direct, ${n.descendants} total` : `${n.notes.length}`;
      lines.push(`${"  ".repeat(indent)}${n.name}/  (${suffix})`);
      walk(f, indent + 1);
    }
  };
  walk(ROOT, 0);
  if (tree.loose.length) lines.push(`(root notes: ${tree.loose.length})`);
  return lines.join("\n");
}

/**
 * Vault -> scene input: the folder tree plus the links that survive pruning.
 *
 * @param {string[]} notePaths             all markdown paths in the vault
 * @param {Record<string,Record<string,string>>} resolvedLinks  Obsidian's metadataCache.resolvedLinks
 * @param {{maxDepth?:number,maxRings?:number,includeRootNotes?:boolean}} settings
 * @returns {{tree:object, links:[string,string][], inScene:Set<string>}}
 */
export function sceneFromVault(notePaths, resolvedLinks, settings = {}) {
  const clean = notePaths.filter((p) => p && !p.startsWith("."));
  const tree = buildTree(clean, settings);

  const inScene = new Set();
  for (const folder of tree.order) for (const p of tree.nodes.get(folder).notes) inScene.add(p);
  for (const p of tree.loose) inScene.add(p);

  const links = [];
  for (const from in resolvedLinks || {}) {
    if (!inScene.has(from)) continue;
    for (const to in resolvedLinks[from]) if (inScene.has(to)) links.push([from, to]);
  }
  return { tree, links, inScene };
}
