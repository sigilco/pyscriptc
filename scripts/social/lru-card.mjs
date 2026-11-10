// LRU regression card (strategy `real-script`): the cachetools-style LRUCache
// from issue #1168, before and after the #1170 lowering fix, against CPython
// 3.14 on the same machine. All numbers come from the recorded fresh run in
// /tmp/lru-bench.json (medians, this VPS, 2026-11-09); the script fails loudly
// if that file is missing or its shape changed.
//
// The three bars share one log scale: the pre-fix run is about 26x the
// CPython reference, so a linear scale would render the two fast bars as
// invisible stubs. Cards carry no header, footer or prose — the machine,
// version, date and scale note go in the post copy.
import { readFileSync } from "node:fs";
import { here, render } from "./lib.mjs";

const benchPath = process.argv[2] ?? "/tmp/lru-bench.json";
const bench = JSON.parse(readFileSync(benchPath, "utf8"));
const { pre_fix_native, post_fix_native, cpython_3_14 } = bench.results;
const ms = (r) => r.median_s * 1000;
const num = (v) => Math.round(v).toLocaleString("en-US");

const rows = [
  ["pyscriptc before #1170", ms(pre_fix_native), "a"],
  ["pyscriptc after #1170", ms(post_fix_native), "a"],
  ["CPython 3.14", ms(cpython_3_14), "b"],
];
const width = (v) => Math.max(6, (Math.log1p(v) / Math.log1p(ms(pre_fix_native))) * 100).toFixed(1);

await render({ "lru-card": `
<div class="stack" style="padding:0 0 8px">
<div class="sect" style="padding:6px 0 44px;border:0"><div class="code" style="font-size:36px"><span class="kw">oldest</span> = next(iter(self.data))</div></div>
<div class="sect" style="padding:0;border:0">
<div class="cell" style="padding:48px 52px">
${rows
  .map(
    ([name, v, kind], i) => `<div class="bar ${kind}" style="font-size:26px;margin-top:${i === 0 ? 0 : 40}px">
<div class="l"><span>${name}</span><span>${num(v)} ms</span></div>
<div class="dots" style="width:${width(v)}%"></div></div>`,
  )
  .join("\n")}
</div>
</div>
</div>` });
