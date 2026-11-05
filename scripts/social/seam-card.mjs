// Seam card (strategy 7): the compiler pipeline, typed Python to a single binary.
// Run from the repo root: node scripts/social/seam-card.mjs. Output in out/, gitignored.
//
// No numbers on this card: the pipeline is a diagram, not a measurement, so it
// carries no data source file. The stage list is the v0.8.0 shape, verified
// against the release tag, and the footer names the tag and commit.
import { here, render } from "./lib.mjs";
import { join } from "node:path";

// stage, what it is, tag (empty means it was already there)
const STAGES = [
  ["source", "typed Python, annotations required", ""],
  ["HIR", "typed nodes, one per construct", "new in 0.8.0"],
  ["MIR", "three-address, opt-in lane", "new in 0.8.0"],
  ["TIR", "LLVM IR, byte-identical out.ll", ""],
  ["binary", "one native executable", ""],
];

const rows = STAGES.map(([name, desc, tag]) => `<div class="row${tag ? " new" : ""}">
<div class="stage">${name}</div>
<div class="desc">${desc}</div>
${tag ? `<div class="tag">${tag}</div>` : `<div class="tag"></div>`}
</div>`).join(`<div class="arrow">↓</div>`);

await render({
  seam: `<div class="stack seam">
<div class="top">
<div class="brand">pyscriptc <span class="ver">v0.8.0</span></div>
<div class="pitch">the compiler core was rebuilt</div>
</div>
<div class="pipe">${rows}</div>
<div class="foot">same bytes out, new pipeline in</div>
</div>`,
});
