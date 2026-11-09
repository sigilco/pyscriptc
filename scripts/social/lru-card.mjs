// LRU regression card (strategy `real-script`): the cachetools-style LRUCache
// from issue #1168, before and after the #1170 lowering fix, against CPython
// 3.14 on the same machine. All numbers come from the recorded fresh run in
// /tmp/lru-bench.json (medians, this VPS, 2026-11-09); the script fails loudly
// if that file is missing or its shape changed.
//
// The three bars share one log scale: the pre-fix run is about 26x the
// CPython reference, so a linear scale would render the two fast bars as
// invisible stubs. The scale is labelled on the card.
import { readFileSync } from "node:fs";
import { here, render } from "./lib.mjs";

const benchPath = process.argv[2] ?? "/tmp/lru-bench.json";
const bench = JSON.parse(readFileSync(benchPath, "utf8"));
const { pre_fix_native, post_fix_native, cpython_3_14 } = bench.results;
const { machine, date, pyscriptc_version, cpython_version } = bench;
const ms = (r) => r.median_s * 1000;
const x = (a, b) => (a / b).toFixed(1) + "x";
const num = (v) => Math.round(v).toLocaleString("en-US");

const when = date.slice(0, 10);
const meta = `pyscriptc ${pyscriptc_version.replace(/\s*\(.*\)/, "")} vs ${cpython_version} · ${machine.host} · ${when}`;

const rows = [
  ["pyscriptc before #1170", ms(pre_fix_native), "a"],
  ["pyscriptc after #1170", ms(post_fix_native), "a"],
  ["CPython 3.14", ms(cpython_3_14), "b"],
];
const width = (v) => Math.max(6, (Math.log1p(v) / Math.log1p(ms(pre_fix_native))) * 100).toFixed(1);

await render({ "lru-card": `
<div class="stack" style="padding:0 0 6px">
<div class="sect"><div class="lbl" style="font-size:23px;margin-bottom:12px">LRUCache eviction peek · issue #1168 repro · 100k ops</div>
<div class="code" style="font-size:33px"><span class="kw">oldest</span> = next(iter(self.data))</div></div>
<div class="sect" style="padding-top:0;padding-bottom:4px;border:0">
<div class="cell" style="padding:24px 26px">
${rows
  .map(
    ([name, v, kind]) => `<div class="bar ${kind}" style="font-size:24px;margin-top:${kind === "a" ? 0 : 16}px">
<div class="l"><span>${name}</span><span>${num(v)} ms</span></div>
<div class="dots" style="width:${width(v)}%"></div></div>`,
  )
  .join("\n")}
<div class="foot" style="padding-top:16px;font-size:22px">bars on a log scale · same machine, same source file</div>
</div>
<div class="msg" style="margin-top:18px;font-size:28px">I shipped a regression I could not see: this cache class ran <span class="a">${x(ms(pre_fix_native), ms(cpython_3_14))} slower</span> than CPython natively. After the fix it is <span class="a">${x(ms(post_fix_native), ms(cpython_3_14))}</span> of CPython, ${x(ms(pre_fix_native), ms(post_fix_native))} faster than before.</div>
<div class="foot" style="padding-top:18px;font-size:24px">${meta}</div>
</div>
</div>` });
