// Benchmark cards from a fresh run (/tmp/social-bench-fresh.json, or the committed
// reference). Run from the repo root: node scripts/social/cards.mjs. Output in out/, gitignored.
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { here, cell, render } from "./lib.mjs";

const fresh = "/tmp/social-bench-fresh.json";
const ref = join(here, "..", "..", "benchmarks/baseline/reference-macos-arm64.json");
const data = JSON.parse(readFileSync(existsSync(fresh) ? fresh : ref, "utf8"));
const w = (n) => { const x = data.workloads.find((v) => v.name === n && v.status === "ok"); if (!x) throw new Error("missing workload " + n); return x; };

// Most used stdlib surface, label -> workload. Chosen for how common the module is, not for the ratio.
const STDLIB = [
  ["json", "json-roundtrip"], ["re", "re-scan"], ["urllib.parse", "url-parse"],
  ["base64", "b64-roundtrip"], ["hashlib sha256", "sha256-digest"], ["pathlib read", "pathlib-read"],
  ["os files", "os-files"], ["time format", "time-format"], ["datetime add", "date-add"],
  ["struct", "struct-pack"], ["os.path join", "path-join"], ["fnmatch", "fnmatch-filter"],
  ["io.StringIO", "stringio-loop"], ["collections.deque", "deque-ring"], ["functools.reduce", "reduce-sum"],
];
const grid = `<div class="grid g3">${STDLIB.map(([l, n]) => cell(l, w(n).native_median_ms, w(n).cpython_median_ms)).join("")}</div>`;

// Boxed fallback: the honest no-win cases.
const ISLAND = [["socket", "socket-host"], ["textwrap", "cleandoc"], ["logging", "log-info"], ["print echo", "echo-out"]];
const trade = `<div class="grid g2 big">${ISLAND.map(([l, n]) => cell(l, w(n).native_median_ms, w(n).cpython_median_ms, "Island")).join("")}</div>`;

await render({ stdlib: grid, tradeoff: trade });
