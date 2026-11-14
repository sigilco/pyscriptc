// The web seams: the assembled tree, the llms data section, the validator,
// and the workflows that carry all of this. These tests run in CI on every
// site PR (site.yml), which is the seam that cannot be forgotten; the data
// files themselves stay under pytest in tests/test_compat_site.py and
// friends.
//
//   pnpm --dir apps/web test
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";

const webRoot = resolve(dirname(new URL(import.meta.url).pathname), "..");
const repoRoot = resolve(webRoot, "..", "..");

// -- assembled tree (requires a build; skipped otherwise) --

const deploy = join(webRoot, "deploy");
const hasDeploy = existsSync(deploy);

test("deploy tree carries the three site parts", { skip: !hasDeploy }, () => {
  for (const rel of [
    "index.html",
    "_headers",
    "_redirects",
    "docs/index.html",
    "docs/llms.txt",
    "docs/llms-full.txt",
    "compat/index.html",
    "compat/data/compat.json",
    "compat/data/island.json",
    "compat/data/ladder.json",
    "compat/data/what_will_fail.json",
    "compat/data/support.json",
    "compare/index.html",
  ]) {
    assert.ok(existsSync(join(deploy, rel)), `missing ${rel}`);
  }
});

test("deploy redirect rules are present", { skip: !hasDeploy }, () => {
  const text = readFileSync(join(deploy, "_redirects"), "utf8");
  assert.ok(text.includes("/docs    /docs/    308"));
  assert.ok(text.includes("/compat  /compat/  308"));
  assert.ok(text.includes("/compare  /compare/  308"));
});

test("theme.css ships next to the docs index (docmd customCss gap)", { skip: !hasDeploy }, () => {
  assert.ok(existsSync(join(deploy, "docs", "theme.css")));
});

test("llms.txt lists every public data file exactly once", { skip: !hasDeploy }, () => {
  const text = readFileSync(join(deploy, "llms.txt"), "utf8");
  const names = readdirSync(join(webRoot, "public", "compat", "data")).filter((n) => n.endsWith(".json"));
  assert.ok(names.length > 0);
  for (const name of names) {
    const count = text.split(`## Public Data Files`).length - 1;
    assert.equal(count, 1, "data section duplicated");
    assert.ok(text.includes(`[${name}](`), `${name} missing from llms.txt`);
  }
});

test("no placeholder survives in a built page", { skip: !hasDeploy }, () => {
  const walk = (dir: string): string[] => {
    const out: string[] = [];
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) out.push(...walk(full));
      else if (entry.name.endsWith(".html")) out.push(full);
    }
    return out;
  };
  for (const page of walk(deploy)) {
    const text = readFileSync(page, "utf8");
    for (const placeholder of ["{{BASE}}", "{{PYSC_COMPAT_URL}}", "{{PYSC_DOCS_URL}}"]) {
      assert.ok(!text.includes(placeholder), `${placeholder} in ${page}`);
    }
  }
});

test("docs pages link the compat page through the placeholder", () => {
  const page = readFileSync(join(repoRoot, "docs", "guide", "python-subset.md"), "utf8");
  assert.ok(page.includes("{{PYSC_COMPAT_URL}}"));
  assert.ok(!page.includes("pyscriptc-compat.pages.dev"));
});

test("placeholder default is the single site", () => {
  const plugin = readFileSync(join(repoRoot, "docs", ".docmd", "plugins", "placeholders.js"), "utf8");
  assert.ok(plugin.includes("https://pyscriptc.pages.dev/compat"));
  assert.ok(!plugin.includes("pyscriptc-compat.pages.dev"));
});

// -- workflows: the seam that runs these very tests --

test("site workflow runs pnpm, not python", () => {
  const text = readFileSync(join(repoRoot, ".github", "workflows", "site.yml"), "utf8");
  // Deploys belong to the Cloudflare dashboard git integration.
  assert.ok(!text.includes("wrangler"));
  assert.ok(!text.includes("CLOUDFLARE_API_TOKEN"));
  assert.ok(text.includes("pnpm"));
  assert.ok(!text.includes("setup-python"), "the site workflow must not need python");
  assert.ok(!text.includes("setup-uv"));
});

test("site workflow triggers only on web paths", () => {
  const text = readFileSync(join(repoRoot, ".github", "workflows", "site.yml"), "utf8");
  assert.ok(/paths:/s.test(text), "site.yml must have a paths: filter");
  // src/pyscriptc changes must never trigger the site workflow
  assert.ok(!text.includes("src/pyscriptc"));
});

test("nightly opens a PR and never pushes to master", () => {
  const workflow = readFileSync(join(repoRoot, ".github", "workflows", "nightly.yml"), "utf8");
  assert.ok(workflow.includes("gh pr create"));
  assert.ok(!workflow.includes("git push origin master"));
  const dataPaths = /git add .*data\/compat\/compat\.json/.test(workflow);
  assert.ok(dataPaths, "nightly stages the canonical data/compat path");
});

test("the nightly carries the R0 lane", () => {
  const workflow = readFileSync(join(repoRoot, ".github", "workflows", "nightly.yml"), "utf8");
  assert.ok(workflow.includes("benchmarks.real_scripts.measure"));
  assert.ok(workflow.includes("scripts/r0_lane.py"));
  assert.ok(workflow.includes("r0-lane:"));
  assert.ok(workflow.includes("nightly-failure"));
});

test("the compat page shows the R0 rate with its denominator", () => {
  const page = readFileSync(join(repoRoot, "apps", "web", "src", "pages", "compat", "index.astro"), "utf8");
  assert.ok(page.includes("data.ladder.r0"), "the page reads the r0 section");
  assert.ok(page.includes("r0.total"), "the page shows the corpus size");
  assert.ok(page.includes("r0.history"), "the page shows the trend");
});

test("the compare page claims only the reproducible baseline", () => {
  const page = readFileSync(join(repoRoot, "apps", "web", "src", "pages", "compare", "index.astro"), "utf8");
  assert.ok(page.includes("benchmarks/baseline/reference.json"), "the page reads the baseline file");
  assert.ok(page.includes("benchmarks.baseline.run --check"), "the page names the reproduce command");
  assert.ok(page.includes("13,798"), "the page states the conformance corpus size");
  assert.ok(!/blazing|faster than (codon|nuitka|pypy)|beats (codon|nuitka|pypy)/i.test(page), "no marketing claims");
});

test("the compare page states where pyscriptc loses", () => {
  const page = readFileSync(join(repoRoot, "apps", "web", "src", "pages", "compare", "index.astro"), "utf8");
  for (const tool of ["PyInstaller", "Nuitka", "Cython", "mypyc", "Codon", "PyPy"]) {
    assert.ok(page.includes(tool), `${tool} missing from the compare page`);
  }
  assert.ok(page.includes("Codon covers more of the"), "the Codon trade-off is stated");
});

test("the compare page leads with a scannable highlights strip", () => {
  const page = readFileSync(join(repoRoot, "apps", "web", "src", "pages", "compare", "index.astro"), "utf8");
  assert.ok(page.includes("class=\"highlights\""), "the highlights strip is on the page");
  assert.ok(page.includes("const highlights: Highlight[]"), "the strip is data-driven");
  // Four proof points, each with a value, a label and a note.
  const strip = page.slice(page.indexOf("const highlights"), page.indexOf("// One \"Why not X?\""));
  assert.equal((strip.match(/value:/g) ?? []).length, 4, "four highlight values");
  assert.equal((strip.match(/label:/g) ?? []).length, 4, "four highlight labels");
  assert.equal((strip.match(/note:/g) ?? []).length, 4, "four highlight notes");
  // The strip must come before the alternatives, so it is the first thing read.
  assert.ok(
    page.indexOf("class=\"highlights\"") < page.indexOf("Why not the alternatives?"),
    "the strip is above the alternatives",
  );
});

test("each alternative is framed as a why-not block with a choose-when line", () => {
  const page = readFileSync(join(repoRoot, "apps", "web", "src", "pages", "compare", "index.astro"), "utf8");
  assert.equal((page.match(/<h3>Why not \{rows\[/g) ?? []).length, 6, "six why-not headings, data-driven");
  assert.equal((page.match(/class="choose-when"/g) ?? []).length, 6, "six choose-when lines");
  assert.ok(page.includes("Choose {rows["), "the choose-when line is data-driven");
});

test("the compare page ends on a compact decision table of yes/no/partial", () => {
  const page = readFileSync(join(repoRoot, "apps", "web", "src", "pages", "compare", "index.astro"), "utf8");
  assert.ok(page.includes("const decision: Decision[]"), "the decision table is data-driven");
  for (const tool of ["pyscriptc", "PyInstaller / cx_Freeze", "Nuitka", "Cython", "mypyc", "Codon", "PyPy"]) {
    assert.ok(page.includes(`name: "${tool}"`), `the ${tool} row is missing from the decision table`);
  }
  assert.ok(page.includes("Standalone binary"), "the binary column is named");
  assert.ok(page.includes("No runtime dependency"), "the runtime column is named");
  assert.ok(page.includes("CPython-semantics oracle"), "the oracle column is named");
  assert.ok(page.includes("Reproducible benchmark"), "the benchmark column is named");
  // The decision table is the last table on the page, after the numbers and
  // the deploy story.
  assert.ok(
    page.indexOf("id=\"decision\"") > page.indexOf("id=\"measured\"") &&
      page.indexOf("id=\"decision\"") > page.indexOf("id=\"deploy\""),
    "the decision table comes last",
  );
});

test("the compare page keeps the benchmark numbers derived from the baseline file", () => {
  const page = readFileSync(join(repoRoot, "apps", "web", "src", "pages", "compare", "index.astro"), "utf8");
  // The kernel and stdlib figures are computed from the reference file at
  // build time, never hard-coded, so a re-recorded baseline moves the page.
  assert.ok(page.includes('const workloads = baseline.workloads'), "the page computes from the workloads");
  assert.ok(page.includes("const geomean ="), "the geomean is computed, not typed in");
  assert.ok(!/value: "\d+\.?\d*x"/.test(page), "no hard-coded speedup in the highlight strip");
  assert.ok(page.includes("REPRODUCE ="), "the reproduce command is a named constant");
});

// -- scripts keep working (the check that caught theme.css) --

test("assemble refuses to ship a tree without every part", () => {
  // A checkout without the build outputs must fail loudly instead of
  // shipping a partial site.
  const work = mkdtempSync(join(tmpdir(), "assemble-"));
  try {
    const result = spawnSync(process.execPath, [join(webRoot, "scripts", "assemble.mts")], {
      cwd: work,
      encoding: "utf8",
      env: { ...process.env, PYSC_WEB_ROOT: work },
    });
    assert.ok(result.status !== 0, "assembly without build outputs must fail");
    assert.ok(/missing build output/.test(result.stderr));
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
});

test("validator reports a broken link on a planted tree", () => {
  const work = mkdtempSync(join(tmpdir(), "validate-"));
  try {
    writeFileSync(join(work, "index.html"), '<a href="/docs/missing.html">x</a>', "utf8");
    const result = spawnSync(
      process.execPath,
      [join(webRoot, "scripts", "validate.mts"), "--deploy", work],
      { cwd: webRoot, encoding: "utf8" },
    );
    assert.ok(result.status !== 0, "validator must fail on a broken link");
    assert.ok(/broken link/.test(result.stderr));
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
});
