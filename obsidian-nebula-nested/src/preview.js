// Standalone browser preview (no Obsidian). Builds a demo vault shaped like the one this fork was
// written for, lays it out with the real code paths, and renders it with the real renderer.
import { Constellation, DEFAULTS } from "./constellation.js";
import { buildTree } from "./model.js";
import { layout, auditLayout } from "./layout.js";

const VAULT = [
  "Home.md", "MOC Trading.md",
  "01 OptionsFlow/Greeks/Delta.md", "01 OptionsFlow/Greeks/Gamma.md", "01 OptionsFlow/Greeks/Vanna.md",
  "01 OptionsFlow/Greeks/Charm.md", "01 OptionsFlow/Greeks/Greeks Cheat Sheet.md",
  "01 OptionsFlow/Dealer Positioning/GEX.md", "01 OptionsFlow/Dealer Positioning/Gamma Flip.md",
  "01 OptionsFlow/Dealer Positioning/Call Wall & Put Wall.md", "01 OptionsFlow/Dealer Positioning/0DTE Gamma.md",
  "01 OptionsFlow/Volatility/VIX.md", "01 OptionsFlow/Volatility/VIX Term Structure.md",
  "01 OptionsFlow/Volatility/Vol Strikes.md", "01 OptionsFlow/Volatility/Vanna Rally.md",
  "01 OptionsFlow/Flow Reading/Sweeps & Blocks.md", "01 OptionsFlow/Flow Reading/Flow vs Positioning.md",
  "02 Market Structure/Instruments/NQ vs ES vs NDX vs QQQ.md", "02 Market Structure/Instruments/Cash vs Future vs Option.md",
  "02 Market Structure/Sessions/RTH vs Overnight.md", "02 Market Structure/Sessions/Initial Balance.md",
  "02 Market Structure/Levels/ONH & ONL.md", "02 Market Structure/Levels/PDH & PDL.md",
  "03 Auction Theory/TPO Mechanics/30-Min Bracket.md", "03 Auction Theory/TPO Mechanics/TPO vs Volume Profile.md",
  "03 Auction Theory/Profile Basics/POC.md", "03 Auction Theory/Profile Basics/Value Area.md",
  "03 Auction Theory/Levels/HVL.md", "03 Auction Theory/Levels/Naked POC.md",
  "03 Auction Theory/Day Types/Trend Day.md", "03 Auction Theory/Day Types/Neutral Day.md",
  "04 Order Flow/Absorption & Exhaustion/Absorption — Definition.md",
  "04 Order Flow/Absorption & Exhaustion/Absorption at Value Edge.md",
  "04 Order Flow/Absorption & Exhaustion/Absorption in Footprint.md",
  "04 Order Flow/Absorption & Exhaustion/When Absorption Fails.md",
  "04 Order Flow/Footprint/Bid x Ask.md", "04 Order Flow/Footprint/Delta per Price.md",
  "04 Order Flow/Delta/Cumulative Delta.md", "04 Order Flow/Delta/Delta Divergence.md",
  "05 Regime/Positive Gamma Day.md", "05 Regime/Negative Gamma Day.md", "05 Regime/Flip Day.md",
  "05 Regime/Morning Routine.md", "05 Regime/Day Types ↔ Gamma Map.md",
  "06 Playbooks/Absorption Fade at TPO Level.md", "06 Playbooks/No-Trade Conditions.md",
  "07 Edge Tests/Absorption Fade by Regime.md", "08 Journal/2026-11.md",
  "99 Debunked/ICT Liquidity.md", "99 Debunked/FVG.md",
];

const LINKS = [
  ["01 OptionsFlow/Dealer Positioning/GEX.md", "01 OptionsFlow/Greeks/Gamma.md"],
  ["01 OptionsFlow/Dealer Positioning/Gamma Flip.md", "01 OptionsFlow/Greeks/Gamma.md"],
  ["01 OptionsFlow/Dealer Positioning/Gamma Flip.md", "05 Regime/Positive Gamma Day.md"],
  ["05 Regime/Positive Gamma Day.md", "06 Playbooks/Absorption Fade at TPO Level.md"],
  ["05 Regime/Negative Gamma Day.md", "04 Order Flow/Absorption & Exhaustion/When Absorption Fails.md"],
  ["04 Order Flow/Absorption & Exhaustion/Absorption — Definition.md", "03 Auction Theory/Levels/HVL.md"],
  ["03 Auction Theory/TPO Mechanics/TPO vs Volume Profile.md", "04 Order Flow/Footprint/Bid x Ask.md"],
  ["01 OptionsFlow/Volatility/VIX.md", "05 Regime/Morning Routine.md"],
  ["Home.md", "MOC Trading.md"],
];

const host = document.getElementById("host");
const tree = buildTree(VAULT, { maxDepth: 3, maxRings: 120, includeRootNotes: true });
const L = layout(tree, LINKS, { perLevel: 9, gap: 6, coreRadius: 26, jitter: 1.6 });

const problems = auditLayout(L);
if (problems.length) console.error("layout audit failed:", problems);
window.__nebula = { tree, L, problems, depths: L.depths };

new Constellation(host, { L, tree }, {
  settings: { ...DEFAULTS, beads: 300, labels: true },
  force: true,
  onOpen: (p) => console.log("open", p),
  onSettings: () => {},
  onRebuild: () => location.reload(),
  onFocus: () => document.body.classList.toggle("nebula-focus"),
});
