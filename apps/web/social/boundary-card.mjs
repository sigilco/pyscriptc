// Boundary card: a real compiler diagnostic (captured from pyscriptc) rendered
// like the terminal card. The capture lives in boundary-capture.txt next to this
// script; regenerate it with
//   uv run pyscriptc compile apps/web/social/boundary/fence.py --emit bin -o /tmp/fence_out
//   (from the repo root; redirect stderr here)
// Never paste output by hand: only a captured run is rendered.
import { execFile } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { createServer } from "node:http";

const run = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "out");
mkdirSync(out, { recursive: true });

const source = readFileSync(join(here, "boundary/fence.py"), "utf8");
const capture = readFileSync(join(here, "boundary-capture.txt"), "utf8").trimEnd();
if (!capture.includes("PS2002")) {
  console.error("boundary-capture.txt does not look like a real PS2002 capture");
  process.exit(2);
}

const css = `
:root{--bg:#07080a;--text:#f2f4f7;--muted:#7c8798;--sec:#9aa3b2;--accent:#4d80c4;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--text);font-family:var(--mono)}
#card{width:1080px;height:1080px;padding:72px;display:flex;flex-direction:column;justify-content:space-between}
h1{font:600 40px/1.2 var(--mono)} .sub{color:var(--sec);font-size:24px;margin-top:12px}
.term{margin-top:36px;background:#0e1013;border:1px solid rgba(255,255,255,.09);border-radius:12px;padding:0;font-size:24px;line-height:1.55;overflow:hidden}
.tab{padding:14px 22px;color:var(--muted);font-size:20px;border-bottom:1px solid rgba(255,255,255,.09)}
.body{padding:24px 26px;white-space:pre-wrap}
.src{color:var(--text)} .src .kw{color:var(--accent)}
.diag{color:var(--accent);margin-top:22px;display:block}
.caret{color:var(--muted)}
.why{margin-top:30px;color:var(--sec);font-size:25px;line-height:1.6}
.why b{color:var(--text);font-weight:600}
.foot{color:var(--muted);font-size:20px}`;

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const srcLines = source.trimEnd().split("\n").map((l, i) => {
  const n = String(i + 1).padStart(2, " ");
  const line = esc(l).replace(/^(from|import|def|return|print)\b/, '<span class="kw">$1</span>');
  return `${n}  ${line}`;
}).join("\n");

const card = `<div id="card"><div><h1>Typing names are not values</h1>
<div class="sub">the compiler refuses this at compile time, before anything runs</div>
<div class="term"><div class="tab">&gt;_ fence.py · CPython 3.14 target</div><div class="body"><span class="src">${srcLines}</span>
<span class="diag">${esc(capture)}</span></div></div>
<div class="why"><b>PS2002</b> is the fence between annotations and values. <b>Optional</b>
describes types for the lowering input; using it as a runtime value would need the typing
module at runtime, which is outside the typed subset.</div>
</div><div class="foot">pyscriptc · try it: pyscriptc explain PS2002</div></div>`;

const server = createServer((q, s) => { const f = join(out, q.url.slice(1)); if (!existsSync(f)) { s.writeHead(404); return s.end(); } s.writeHead(200, { "content-type": "text/html" }); s.end(readFileSync(f)); });
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;
const pw = (...a) => run("playwright-cli", ["-s=social", ...a], { maxBuffer: 8e6 });
const html = join(out, "boundary.html");
writeFileSync(html, `<!doctype html><meta charset=utf-8><style>${css}</style>${card}`);
await pw("open", `${base}/boundary.html`);
await pw("resize", "1080", "1080");
await pw("screenshot", "#card", `--filename=${join(out, "boundary.png")}`);
await pw("close");
if (!existsSync(join(out, "boundary.png"))) { console.error("render failed"); process.exit(2); }
console.log("wrote", join(out, "boundary.png"));
server.close();
