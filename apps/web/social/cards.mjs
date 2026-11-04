// Social cards for pyscriptc, generated from real benchmark data.
//   node social/cards.mjs        (run from apps/web/)
// Output: apps/web/social/out/*.png (1080x1080), not committed.
// Tokens mirror src/styles/theme.css. Accent blue only; no teal or yellow.
import { execFile } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { createServer } from "node:http";

const run = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..", "..");
const out = join(here, "out");
mkdirSync(out, { recursive: true });

const data = JSON.parse(readFileSync(join(repo, "benchmarks/baseline/reference-macos-arm64.json"), "utf8"));
const rows = data.workloads
  .filter((w) => w.status === "ok" && w.native_median_ms > 0 && w.cpython_median_ms > 0)
  .map((w) => ({ name: w.name, native: w.native_median_ms, py: w.cpython_median_ms }))
  .sort((a, b) => a.native / a.py - b.native / b.py)
  .slice(0, 5);
const maxPy = Math.max(...rows.map((r) => r.py));
const fmt = (ms) => (ms >= 100 ? Math.round(ms).toLocaleString("en-US") : ms.toFixed(1)) + " ms";

const css = `
:root{--bg:#07080a;--text:#f2f4f7;--muted:#7c8798;--sec:#9aa3b2;--accent:#4d80c4;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--text);font-family:var(--mono)}
#card{width:1080px;height:1080px;padding:72px;display:flex;flex-direction:column;justify-content:space-between}
h1{font:600 40px/1.2 var(--mono)} .sub{color:var(--sec);font-size:24px;margin-top:12px}
.row{margin-top:34px} .name{font-size:26px} 
.bar{display:flex;align-items:center;gap:18px;margin-top:10px;font-size:22px}
.lab{width:110px;color:var(--muted)} .lab.n{color:var(--text)}
.dots{height:20px;background-image:radial-gradient(circle,currentColor 2.4px,transparent 3px);background-size:7px 10px}
.py{color:#4b5260} .nat{color:var(--accent)}
.val{color:var(--muted)} .val.n{color:var(--text)}
.foot{color:var(--muted);font-size:20px}`;

const barW = (ms) => Math.max(14, Math.round((ms / maxPy) * 600 / 7) * 7);
const bench = `<div id="card"><div><h1>native vs CPython ${data.cpython}</h1>
<div class="sub">median wall time, lower is better</div>
${rows.map((r) => `<div class="row"><div class="name">${r.name}</div>
<div class="bar"><span class="lab n">native</span><span class="dots nat" style="width:${barW(r.native)}px"></span><span class="val n">${fmt(r.native)}</span></div>
<div class="bar"><span class="lab">CPython</span><span class="dots py" style="width:${barW(r.py)}px"></span><span class="val">${fmt(r.py)}</span></div></div>`).join("")}
</div><div class="foot">pyscriptc · ${data.machine} · ${data.recorded_at.slice(0, 10)} · reference run, ${data.workloads.length} workloads</div></div>`;

const cards = { bench };
const server = createServer((q, s) => { const f = join(out, q.url.slice(1)); if (!existsSync(f)) { s.writeHead(404); return s.end(); } s.writeHead(200, { "content-type": "text/html" }); s.end(readFileSync(f)); });
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;
const pw = (...a) => run("playwright-cli", ["-s=social", ...a], { maxBuffer: 8e6 });
for (const [name, body] of Object.entries(cards)) {
  const html = join(out, `${name}.html`);
  writeFileSync(html, `<!doctype html><meta charset=utf-8><style>${css}</style>${body}`);
  await pw("open", `${base}/${name}.html`);
  await pw("resize", "1080", "1080");
  await pw("screenshot", "#card", `--filename=${join(out, name + ".png")}`);
  await pw("close");
  if (!existsSync(join(out, name + ".png"))) { console.error("render failed", name); process.exit(2); }
  console.log("wrote", join(out, name + ".png"));
}
server.close();
