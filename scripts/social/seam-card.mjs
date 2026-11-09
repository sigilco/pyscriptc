// Seam card (strategy 7): the compiler pipeline, typed Python to a single binary.
// Run from the repo root: node scripts/social/seam-card.mjs. Output in out/, gitignored.
//
// The pipeline is a mermaid flowchart (theme base, tokens matching lib.mjs)
// instead of hand-laid rows: labelled edges between stage nodes survive the
// small preview sizes that hand-drawn arrows do not. Mermaid needs a DOM
// layout pass, so this script writes the card page with the mermaid bundle
// inline, starts a browser through the shared render() server, and screenshots
// #card after mermaid has replaced <pre class="mermaid"> with SVG.
//
// The bundle is loaded from jsDelivr at a pinned version. To make renders
// byte-reproducible offline, vendor it once:
//   curl -o scripts/social/vendor/mermaid.min.js \
//     https://cdn.jsdelivr.net/npm/mermaid@11.12.2/dist/mermaid.min.js
// When vendor/mermaid.min.js exists it is served locally and inlined, so no
// network is touched. The version pin stays in this file either way.
//
// No numbers on this card: the pipeline is a diagram, not a measurement, so it
// carries no data source file. The stage list is the v0.8.0 shape, verified
// against the release tag, and the footer names the tag and commit.
import { existsSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { here, css } from "./lib.mjs";
import { join } from "node:path";

const MERMAID_VERSION = "11.12.2";
const VENDOR = join(here, "vendor", "mermaid.min.js");

// stage id, node label (two lines: stage, detail), class (new marks the stages
// introduced in 0.8.0), edge label into the next stage. The vertical chain is
// the natural compiler shape; two-line nodes make it wide enough to fill a
// square card.
const FLOW = [
  ["src", "typed Python\nannotations required", "", "parse"],
  ["hir", "HIR · new in 0.8.0\ntyped nodes, one per construct", "new", "lower"],
  ["mir", "MIR · new in 0.8.0\nthree-address, opt-in lane", "new", "emit"],
  ["tir", "TIR\nLLVM IR, byte-identical out.ll", "", "zig cc"],
  ["bin", "binary\none native executable", "", ""],
];

const nodes = FLOW.map(([id, label]) => `    ${id}("${label}")`).join("\n");
const newIds = FLOW.filter(([, , c]) => c === "new").map(([id]) => id).join(",");
const classes = `\n    classDef new stroke:#6c9bd8,stroke-width:2.5px\n    class ${newIds} new`;
const edges = FLOW.slice(0, -1)
  .map(([id, , , lbl], i) => `    ${id} -- "${lbl}" --> ${FLOW[i + 1][0]}`)
  .join("\n");
const graph = `flowchart TD\n${nodes}\n${edges}\n${classes}`;

// Mermaid source: vendored file inlined (deterministic, no network) or CDN.
const mermaidScript = existsSync(VENDOR)
  ? `<script>${readFileSync(VENDOR, "utf8")}</script>`
  : `<script src="https://cdn.jsdelivr.net/npm/mermaid@${MERMAID_VERSION}/dist/mermaid.min.js"></script>`;

// Mermaid config as JSON, injected before mermaid.initialize runs. Tokens
// mirror lib.mjs: obsidian canvas, one blue accent, hairline borders.
const mermaidConfig = JSON.stringify({
  startOnLoad: true,
  // mermaid defaults to max-width:100% on the SVG, which shrinks the diagram
  // and its text to the container. Draw at natural size; the scale step below
  // fits it to the card instead.
  useMaxWidth: false,
  theme: "base",
  themeVariables: {
    background: "#07080a",
    primaryColor: "#0e1013",
    primaryTextColor: "#f2f4f7",
    primaryBorderColor: "#4d80c4",
    lineColor: "#4d80c4",
    textColor: "#b4bec8",
    edgeLabelBackground: "#07080a",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontSize: "34px",
  },
  flowchart: { curve: "linear", nodeSpacing: 100, rankSpacing: 100, padding: 16, wrappingWidth: 520, htmlLabels: false },
});

const head = `<!doctype html><meta charset="utf-8"><title>seam</title>
<style>${css}
#card{display:flex;flex-direction:column;justify-content:center;background:var(--bg)}
.hero{font-family:var(--sans);font-weight:700;font-size:52px;letter-spacing:-.02em;text-align:center;padding-bottom:28px}
.hero .ver{color:var(--accent-strong);font-weight:600}
#graph{flex:1;display:flex;align-items:center;justify-content:center;min-height:0;padding:36px 0}
#graph .mermaid{width:100%;height:100%;display:flex;align-items:center;justify-content:center}
</style>
${mermaidScript}
<script>
mermaid.initialize(${mermaidConfig});
// mermaid draws at its own natural size, which is narrower than the card.
// After render, scale the SVG uniformly so it fills the #graph box (contain).
window.addEventListener('load', () => {
  const timer = setInterval(() => {
    const svg = document.querySelector('#graph svg');
    if (!svg) return;
    clearInterval(timer);
    const box = document.querySelector('#graph').getBoundingClientRect();
    const nat = svg.getBoundingClientRect();
    const scale = Math.min(box.width / nat.width, box.height / nat.height);
    svg.style.transform = "scale(" + scale + ")";
    svg.style.transformOrigin = 'center';
    document.title = 'SVG-SCALED';
  }, 100);
  setTimeout(() => { if (document.title !== 'SVG-SCALED') document.title = 'MERMAID-FAILED'; }, 8000);
});
</script>`;

const body = `<div id="card">
<div class="hero">new compiler architecture</div>
<div id="graph"><pre class="mermaid">
${graph}
</pre></div>
</div>`;

// lib render() writes its own template, which would drop the mermaid head, so
// this card does its own render loop: same server/screenshot pattern, but the
// page is the full head+body written above and the screenshot waits for
// mermaid to have replaced the <pre> with an SVG (or fails loudly).
mkdirSync(join(here, "out"), { recursive: true });
const page = `<!doctype html><html><head><meta charset="utf-8">${head}</head><body>${body}</body></html>`;
writeFileSync(join(here, "out", "seam.html"), page);

const { execFile } = await import("node:child_process");
const { promisify } = await import("node:util");
const { createServer } = await import("node:http");
const run = promisify(execFile);

const server = createServer((q, s) => {
  s.writeHead(200, { "content-type": "text/html" });
  s.end(page);
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;
const pw = (...a) => run("playwright-cli", ["-s=social", ...a], { maxBuffer: 8e6 });

await pw("open", `${base}/seam.html`);
await pw("resize", "1080", "1080");
// Mermaid renders async; poll for the SVG then give fonts a beat to settle.
let svgSeen = false;
for (let i = 0; i < 40; i++) {
  const { stdout: out } = await pw("eval", "document.title === 'SVG-SCALED' ? 'SVG-OK' : (document.title === 'MERMAID-FAILED' ? 'SVG-FAIL' : 'WAIT')");
  if (out.includes("SVG-OK")) { svgSeen = true; break; }
  await new Promise((r) => setTimeout(r, 250));
}
if (!svgSeen) { console.error("mermaid never rendered or scaled an SVG"); process.exit(2); }
await pw("screenshot", "#card", `--filename=${join(here, "out", "seam.png")}`);
await pw("close");
server.close();
console.log("wrote seam");
