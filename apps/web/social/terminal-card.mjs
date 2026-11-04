// Terminal still: the final frame of the captured compile-and-run GIF, framed as a
// 1080x1080 card. The frame comes from the real asciinema capture (demo/session.cast);
// this script only frames it, it does not fabricate any terminal content.
import { execFile } from "node:child_process";
import { mkdirSync, copyFileSync, readdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { createServer } from "node:http";

const run = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "out");
mkdirSync(out, { recursive: true });

// The GIF is the source of truth for the still: extract its last coalesced frame.
const gif = join(out, "compile-and-run.gif");
if (!existsSync(gif)) { console.error("run the agg step first: compile-and-run.gif is missing"); process.exit(2); }
const tmp = join(out, "frames");
mkdirSync(tmp, { recursive: true });
await run("magick", [gif, "-coalesce", join(tmp, "frame-%03d.png")]);
const frames = readdirSync(tmp).filter((f) => f.endsWith(".png")).sort();
const last = join(tmp, frames[frames.length - 1]);
copyFileSync(last, join(out, "term-frame.png"));
const frameData = readFileSync(join(out, "term-frame.png")).toString("base64");

const css = `
:root{--bg:#07080a;--text:#f2f4f7;--muted:#7c8798;--sec:#9aa3b2;--accent:#4d80c4;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--text);font-family:var(--mono)}
#card{width:1080px;height:1080px;padding:72px;display:flex;flex-direction:column;justify-content:space-between}
h1{font:600 40px/1.2 var(--mono)} .sub{color:var(--sec);font-size:24px;margin-top:12px}
.shot{margin-top:36px;border:1px solid rgba(255,255,255,.09);border-radius:12px;overflow:hidden}
.shot img{display:block;width:100%}
.foot{color:var(--muted);font-size:20px}`;

const card = `<div id="card"><div><h1>Compile, build, run</h1>
<div class="sub">the same annotated Python, from interpreter to binary</div>
<div class="shot"><img src="data:image/png;base64,${frameData}"></div>
</div><div class="foot">pyscriptc · real captured terminal session · CPython 3.14 target</div></div>`;

const server = createServer((q, s) => { const f = join(out, q.url.slice(1)); if (!existsSync(f)) { s.writeHead(404); return s.end(); } s.writeHead(200, { "content-type": "text/html" }); s.end(readFileSync(f)); });
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;
const pw = (...a) => run("playwright-cli", ["-s=social", ...a], { maxBuffer: 8e6 });
const html = join(out, "terminal.html");
writeFileSync(html, `<!doctype html><meta charset=utf-8><style>${css}</style>${card}`);
await pw("open", `${base}/terminal.html`);
await pw("resize", "1080", "1080");
await pw("screenshot", "#card", `--filename=${join(out, "terminal.png")}`);
await pw("close");
if (!existsSync(join(out, "terminal.png"))) { console.error("render failed"); process.exit(2); }
console.log("wrote", join(out, "terminal.png"));
server.close();
