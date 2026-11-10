import { Plugin, ItemView, Notice } from "obsidian";
import { Constellation, DEFAULTS } from "./constellation.js";
import { buildTree, outline, sceneFromVault } from "./model.js";
import { layout, auditLayout } from "./layout.js";

const VIEW = "nebula-nested";

class NebulaView extends ItemView {
  constructor(leaf, plugin) { super(leaf); this.plugin = plugin; }
  getViewType() { return VIEW; }
  getDisplayText() { return "Nebula"; }
  getIcon() { return "orbit"; }

  async onOpen() {
    this.addAction("git-fork", "Back to graph view", () => this.plugin.backToGraph());
    this.contentEl.empty();
    this.contentEl.addClass("nebula-host");
    this.build();
    this.scope.register([], "f", () => { this.plugin.toggleFocus(); return false; });
    this.scope.register([], "Escape", () => {
      if (document.body.classList.contains("nebula-focus")) this.plugin.toggleFocus();
      return false;
    });
    // Obsidian restores this tab at startup before links are resolved, so the first build can come
    // up with almost none. The "resolved" event misses that late pass, so poll: rebuild when the
    // vault shape has changed and then held still for one tick.
    this.registerInterval(window.setInterval(() => this.refresh(), 2000));
  }

  /** cheap fingerprint of the vault: note count + resolved link count */
  shape() {
    const rl = this.app.metadataCache.resolvedLinks;
    let n = 0;
    for (const a in rl) n += Object.keys(rl[a]).length;
    return `${this.app.vault.getMarkdownFiles().length}:${n}`;
  }

  refresh() {
    const s = this.shape();
    if (this.c && s !== this.built && s === this.seen) this.build();
    this.seen = s;
  }

  /** structure settings fire on every slider tick, so coalesce rebuilds */
  rebuildSoon() {
    window.clearTimeout(this._rb);
    this._rb = window.setTimeout(() => this.build(), 220);
  }

  settings() {
    const s = this.plugin.data.settings || {};
    return {
      ...s,
      maxDepth: s.maxDepth == null ? DEFAULTS.maxDepth : s.maxDepth,
      maxRings: s.maxRings == null ? DEFAULTS.maxRings : s.maxRings,
      showRootNotes: s.showRootNotes == null ? DEFAULTS.showRootNotes : s.showRootNotes,
      spacing: s.spacing == null ? DEFAULTS.spacing : s.spacing,
    };
  }

  build() {
    window.clearTimeout(this._rb);
    const s = this.settings();
    const app = this.app;
    this.built = this.shape();

    // keep the camera and the panel state where the user left them across a rebuild
    const camera = this.c ? this.c.cameraState() : null;
    const panelOpen = this.c ? this.c.panelOpen() : false;
    if (this.c) { this.c.destroy(); this.c = null; }

    // 1-3. folder tree + the links that survive pruning
    const notePaths = app.vault.getMarkdownFiles().filter((f) => !f.path.startsWith(".")).map((f) => f.path);
    const { tree, links } = sceneFromVault(notePaths, app.metadataCache.resolvedLinks, {
      maxDepth: s.maxDepth,
      maxRings: s.maxRings,
      includeRootNotes: !!s.showRootNotes,
    });

    // 4. geometry
    const L = layout(tree, links, {
      perLevel: 9 * s.spacing,
      gap: 6,
      coreRadius: 26,
      jitter: 1.6,
    });
    const problems = auditLayout(L);
    if (problems.length) console.warn("[Nebula Nested] layout audit:", problems);

    // 5. render
    this.c = new Constellation(this.contentEl, { L, tree }, {
      settings: s,
      camera: camera || undefined,
      panelOpen,
      onSettings: (next) => { this.plugin.data.settings = next; this.plugin.saveData(this.plugin.data); },
      onRebuild: () => this.rebuildSoon(),
      onFocus: () => this.plugin.toggleFocus(),
      onOpen: (path, e) => {
        const file = app.vault.getAbstractFileByPath(path);
        if (!file) return;
        const inTab = e && (e.metaKey || e.ctrlKey);
        app.workspace.getLeaf(inTab ? "tab" : "split", inTab ? undefined : "vertical").openFile(file);
      },
    });
  }

  async onClose() {
    window.clearTimeout(this._rb);
    if (this.c) this.c.destroy();
    this.c = null;
  }
}

export default class NebulaNested extends Plugin {
  async onload() {
    this.data = Object.assign({ settings: {} }, await this.loadData());
    this.registerView(VIEW, (leaf) => new NebulaView(leaf, this));

    const openView = async () => {
      const existing = this.app.workspace.getLeavesOfType(VIEW)[0];
      if (existing) { this.app.workspace.revealLeaf(existing); existing.view.build(); return; }
      const leaf = this.app.workspace.getLeaf("tab");
      await leaf.setViewState({ type: VIEW, active: true });
      this.app.workspace.revealLeaf(leaf);
    };
    const toggle = () => (this.app.workspace.getLeavesOfType(VIEW).length ? this.backToGraph() : openView());
    this.openView = openView;

    this.addRibbonIcon("orbit", "Nebula", toggle);
    this.addCommand({ id: "toggle", name: "Toggle view", callback: toggle });
    this.addCommand({ id: "open", name: "Open view", callback: openView });
    this.addCommand({ id: "back", name: "Close and open graph view", callback: () => this.backToGraph() });
    this.addCommand({ id: "refresh", name: "Refresh view", callback: () => this.app.workspace.getLeavesOfType(VIEW).forEach((l) => l.view.build()) });
    this.addCommand({
      id: "export-outline",
      name: "Copy folder outline to clipboard",
      callback: async () => {
        const s = (this.data.settings || {});
        const notePaths = this.app.vault.getMarkdownFiles().filter((f) => !f.path.startsWith(".")).map((f) => f.path);
        const tree = buildTree(notePaths, {
          maxDepth: s.maxDepth == null ? DEFAULTS.maxDepth : s.maxDepth,
          maxRings: s.maxRings == null ? DEFAULTS.maxRings : s.maxRings,
          includeRootNotes: true,
        });
        const text = outline(tree) + (tree.truncated ? `\n… ${tree.truncated} smaller rings hidden by "Max rings"` : "");
        try {
          await navigator.clipboard.writeText(text);
          new Notice(`Nebula: outline copied — ${tree.order.length} rings, ${notePaths.length} notes`);
        } catch (err) {
          console.warn("[Nebula Nested] clipboard unavailable", err);
          new Notice("Nebula: could not reach the clipboard — outline printed to the console instead");
          console.log(text);
        }
      },
    });
  }

  backToGraph() {
    if (document.body.classList.contains("nebula-focus")) this.toggleFocus();
    this.app.workspace.detachLeavesOfType(VIEW);
    const g = this.app.workspace.getLeavesOfType("graph")[0];
    if (g) this.app.workspace.revealLeaf(g);
    else this.app.commands.executeCommandById("graph:open");
  }

  toggleFocus() {
    const ws = this.app.workspace, on = !document.body.classList.contains("nebula-focus");
    document.body.classList.toggle("nebula-focus", on);
    if (on) { this._ls = !ws.leftSplit.collapsed; this._rs = !ws.rightSplit.collapsed; ws.leftSplit.collapse(); ws.rightSplit.collapse(); }
    else { if (this._ls) ws.leftSplit.expand(); if (this._rs) ws.rightSplit.expand(); }
  }

  // Leaves are left in place on unload (Obsidian guideline) so the tab returns after an update.
  onunload() { document.body.classList.remove("nebula-focus"); }
}
