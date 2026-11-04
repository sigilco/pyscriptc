// Shared card theme and renderer. Tokens mirror src/styles/theme.css (obsidian canvas,
// one blue accent). Cards carry only the values; all explanation goes in the post text.
import { execFile } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { createServer } from "node:http";

const run = promisify(execFile);
export const here = dirname(fileURLToPath(import.meta.url));
export const out = join(here, "out");
mkdirSync(out, { recursive: true });

export const css = `
:root{--bg:#07080a;--text:#f2f4f7;--muted:#6b7482;--sec:#9aa3b2;--accent:#4d80c4;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;--sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;--surface:#0e1013;--hair:rgba(255,255,255,.09);--slate:#b4bec8;--accent-strong:#6c9bd8}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--text);font-family:var(--mono)}
#card{width:1080px;height:1080px;padding:64px}
.grid{display:grid;gap:20px;height:100%;align-content:space-between}
.cell{background:var(--surface);border:1px solid var(--hair);border-radius:14px;padding:22px 22px 20px}
.g3{grid-template-columns:repeat(3,1fr)} .g2{grid-template-columns:repeat(2,1fr);align-content:center;row-gap:24px}
.name{font:500 24px var(--sans);color:var(--slate)}
.bar{margin-top:10px;font-size:21px}
.bar .l{display:flex;justify-content:space-between}
.bar.a .l{color:var(--text)} .bar.a .l span:last-child{color:var(--accent-strong)} .bar.b .l{color:var(--muted)}
.dots{height:14px;margin-top:5px;background-image:radial-gradient(circle,currentColor 2.2px,transparent 2.8px);background-size:6px 8px}
.bar.a .dots{color:var(--accent)} .bar.b .dots{color:#3a404b;width:100%}
.big .name{font-size:32px} .big .bar{font-size:28px} .big .dots{height:20px;background-size:8px 11px}
.sect{border-bottom:1px solid rgba(255,255,255,.14);padding:56px 0}
.sect:last-child{border:0}
.lbl{font-family:var(--sans);color:var(--sec);font-size:26px;margin-bottom:22px}
.code{font-size:44px;line-height:1.35;white-space:pre} .code .kw{color:var(--accent)}
.hero{font-size:84px;font-weight:600;letter-spacing:-.02em}
.msg{font-size:34px;line-height:1.4} .msg .a{color:var(--accent)}
.term{font-size:33px;line-height:1.5;white-space:pre} .term .p{color:var(--accent)} .term .o{color:var(--sec)}
.stack{display:flex;flex-direction:column;justify-content:center;height:100%}`;

export const fmt = (ms) => (ms >= 100 ? Math.round(ms).toLocaleString("en-US") : ms.toFixed(ms < 10 ? 1 : 0)) + " ms";

// One benchmark cell: native bar scaled against its own CPython bar (the reference style).
export const cell = (name, native, py, a = "Native", b = "CPython") =>
  `<div class="cell"><div class="name">${name}</div>
<div class="bar a"><div class="l"><span>${a}</span><span>${fmt(native)}</span></div><div class="dots" style="width:${Math.max(4, (native / py) * 100).toFixed(1)}%"></div></div>
<div class="bar b"><div class="l"><span>${b}</span><span>${fmt(py)}</span></div><div class="dots"></div></div></div>`;

export async function render(cards) {
  const server = createServer((q, s) => { const f = join(out, q.url.slice(1)); if (!existsSync(f)) { s.writeHead(404); return s.end(); } s.writeHead(200, { "content-type": "text/html" }); s.end(readFileSync(f)); });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const pw = (...a) => run("playwright-cli", ["-s=social", ...a], { maxBuffer: 8e6 });
  for (const [name, body] of Object.entries(cards)) {
    writeFileSync(join(out, `${name}.html`), `<!doctype html><meta charset=utf-8><style>${css}</style><div id="card">${body}</div>`);
    await pw("open", `${base}/${name}.html`);
    await pw("resize", "1080", "1080");
    await pw("screenshot", "#card", `--filename=${join(out, name + ".png")}`);
    await pw("close");
    if (!existsSync(join(out, name + ".png"))) { console.error("render failed", name); process.exit(2); }
    console.log("wrote", name);
  }
  server.close();
}
