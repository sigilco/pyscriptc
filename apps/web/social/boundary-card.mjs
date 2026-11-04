// Boundary card: the real PS2002 diagnostic (boundary-capture.txt, captured from
// pyscriptc; regenerate with `uv run pyscriptc compile apps/web/social/boundary/fence.py --emit bin`).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { here, render } from "./lib.mjs";

const capture = readFileSync(join(here, "boundary-capture.txt"), "utf8");
const m = capture.match(/(PS\d+): (\w+) is a typing name and is not a value/);
if (!m) { console.error("boundary-capture.txt is not a real PS2002 capture"); process.exit(2); }
const [, code, name] = m;
await render({ boundary: `<div class="stack">
<div class="sect"><div class="lbl">Source</div><div class="code"><span class="kw">print</span>(${name})</div></div>
<div class="sect"><div class="lbl">Caught at compile time</div><div class="hero">${code}</div></div>
<div class="sect"><div class="lbl">Diagnostic</div><div class="msg"><span class="a">${name}</span> is a typing name and is not a value</div></div>
</div>` });
