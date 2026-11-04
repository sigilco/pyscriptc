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

// Social run config: /tmp/social-bench-fresh.json takes priority (fresh run); the
// committed macOS reference is the fallback. Same schema.
const freshPath = "/tmp/social-bench-fresh.json";
const data = existsSync(freshPath)
  ? JSON.parse(readFileSync(freshPath, "utf8"))
  : JSON.parse(readFileSync(join(repo, "benchmarks/baseline/reference-macos-arm64.json"), "utf8"));
const SRC = existsSync(freshPath) ? "fresh run" : "reference run";

const run = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..", "..");
const out = join(here, "out");
mkdirSync(out, { recursive: true });

const rows = data.workloads
  .filter((w) => w.status === "ok" && w.native_median_ms > 0 && w.cpython_median_ms > 0)
  .map((w) => ({ name: w.name, native: w.native_median_ms, py: w.cpython_median_ms }))
  .sort((a, b) => a.native / a.py - b.native / b.py)
  .slice(0, 5);
const maxPy = Math.max(...rows.map((r) => r.py));
const fmt = (ms) => (ms >= 100 ? Math.round(ms).toLocaleString("en-US") : ms.toFixed(1)) + " ms";
const speed = (ratio) => (1 / ratio).toFixed(1) + "x";

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
.foot{color:var(--muted);font-size:20px}
.foot.note{color:var(--sec)}
.sp{margin-left:auto;color:var(--accent);font-size:20px}
.geo{display:flex;gap:12px;margin-top:18px;flex-wrap:wrap}
.chip{border:1px solid rgba(255,255,255,.09);border-radius:999px;padding:8px 18px;color:var(--sec);font-size:21px}
.chip b{color:var(--text);font-weight:600}
.trade{margin-top:26px;border-top:1px solid rgba(255,255,255,.09);padding-top:20px}
.trow{color:var(--sec);font-size:21px;line-height:1.5}
.trow b{color:var(--text);font-weight:600}
.trow.dim{color:var(--muted)}`;

const barW = (ms, ref) => Math.max(14, Math.round((ms / ref) * 600 / 7) * 7);
const bench = `<div id="card"><div><h1>native vs CPython ${data.cpython}</h1>
<div class="sub">median wall time, lower is better</div>
${rows.map((r) => `<div class="row"><div class="name">${r.name}</div>
<div class="bar"><span class="lab n">native</span><span class="dots nat" style="width:${barW(r.native, maxPy)}px"></span><span class="val n">${fmt(r.native)}</span></div>
<div class="bar"><span class="lab">CPython</span><span class="dots py" style="width:${barW(r.py, maxPy)}px"></span><span class="val">${fmt(r.py)}</span></div></div>`).join("")}
</div><div class="foot">pyscriptc · ${data.machine} · ${data.recorded_at.slice(0, 10)} · reference run, ${data.workloads.length} workloads</div></div>`;

// ---- win-spread: geomean per group + a curated realistic spread (not the top 5).
// The geomean comes from the kernel workloads (the tool's own headline geomean). It is
// startup-dominated, so the card says so.
const kernelRatio = data.geomean_time_ratio;
const groups = data.group_geomeans || {};
const CURATED = [
  "json-roundtrip", "re-scan", "url-parse", "sha256-digest",
  "pathlib-read", "os-files", "time-format", "struct-pack",
];
const spread = CURATED
  .map((n) => data.workloads.find((w) => w.name === n))
  .filter((w) => w && w.status === "ok")
  .map((w) => ({ name: w.name, native: w.native_median_ms, py: w.cpython_median_ms, ratio: w.time_ratio }));
const spreadMax = Math.max(...spread.map((r) => r.py));
const geomeans = Object.entries(groups)
  .filter(([g]) => g !== "island")
  .map(([g, v]) => ({ g, s: speed(v.geomean_time_ratio) }));
const spreadCard = `<div id="card"><div><h1>The spread, not the highlight reel</h1>
<div class="sub">geomean ${speed(kernelRatio)} CPython on the kernel group · CPython ${data.cpython}</div>
<div class="geo">${geomeans.map((x) => `<span class="chip"><b>${x.s}</b> ${x.g}</span>`).join("")}</div>
${spread.map((r) => `<div class="row"><div class="name">${r.name}</div>
<div class="bar"><span class="lab n">native</span><span class="dots nat" style="width:${barW(r.native, spreadMax)}px"></span><span class="val n">${fmt(r.native)}</span>
<span class="sp">${speed(r.ratio)}</span></div>
<div class="bar"><span class="lab">CPython</span><span class="dots py" style="width:${barW(r.py, spreadMax)}px"></span><span class="val">${fmt(r.py)}</span></div></div>`).join("")}
</div><div class="foot note">short scripts, process startup included · pyscriptc · macOS ${data.machine} · ${data.recorded_at.slice(0, 10)} · ${SRC}, ${data.workloads.length} workloads</div></div>`;

// ---- tradeoff: the island group (boxed CPython fallback) is no speed win. What you
// get instead: the binary is still standalone, but it links libpython, so the card
// only claims what the data shows: still one file, modestly slower, no win here.
const islandRows = data.workloads
  .filter((w) => w.group === "island" && w.status === "ok")
  .map((w) => ({ name: w.name, native: w.native_median_ms, py: w.cpython_median_ms, ratio: w.time_ratio }));
const boxed = islandRows.filter((r) => r.name !== "threads");
const boxedGeo = Math.exp(boxed.reduce((s, r) => s + Math.log(r.ratio), 0) / boxed.length);
const islBin = data.workloads.find((w) => w.group === "island" && w.binary_bytes > 0)?.binary_bytes;
const plainBin = data.workloads.find((w) => w.group !== "island" && w.binary_bytes > 0)?.binary_bytes;
const kb = (b) => (b / 1024).toFixed(0) + " KB";
const islMax = Math.max(...islandRows.map((r) => r.py));
const stdlibSpeed = groups.stdlib ? speed(groups.stdlib.geomean_time_ratio) : null;
const tradeCard = `<div id="card"><div><h1>Where we do not win</h1>
<div class="sub">boxed fallback: an import outside the closed table routes through an embedded CPython</div>
${islandRows.map((r) => `<div class="row"><div class="name">${r.name}</div>
<div class="bar"><span class="lab n">island</span><span class="dots nat" style="width:${barW(r.native, islMax)}px"></span><span class="val n">${fmt(r.native)}</span>
<span class="sp">${speed(r.ratio)}</span></div>
<div class="bar"><span class="lab">CPython</span><span class="dots py" style="width:${barW(r.py, islMax)}px"></span><span class="val">${fmt(r.py)}</span></div></div>`).join("")}
<div class="trade">
<div class="trow">the four boxed workloads land at <b>${speed(boxedGeo)} CPython</b>, that is no win</div>
${islBin && plainBin ? `<div class="trow">the boxed binary is <b>${kb(islBin)}</b> and links libpython, so it is not one standalone file</div>
<div class="trow">a fully lowered binary is <b>${kb(plainBin)}</b> and links only the system libc</div>` : ""}
${stdlibSpeed ? `<div class="trow">the win is elsewhere: stdlib group at <b>${stdlibSpeed} CPython</b>, see the spread card</div>` : ""}
<div class="trow dim">threads sits in this group but lowers natively, it is the exception</div>
</div>
</div><div class="foot">pyscriptc · macOS ${data.machine} · ${data.recorded_at.slice(0, 10)} · ${SRC}, ${data.workloads.length} workloads</div></div>`;

const cards = { bench, "win-spread": spreadCard, tradeoff: tradeCard };
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
