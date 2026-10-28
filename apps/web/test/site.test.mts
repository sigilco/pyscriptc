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
    "compat/data/ladder.json",
    "compat/data/what_will_fail.json",
    "compat/data/support.json",
  ]) {
    assert.ok(existsSync(join(deploy, rel)), `missing ${rel}`);
  }
});

test("deploy redirect rules are present", { skip: !hasDeploy }, () => {
  const text = readFileSync(join(deploy, "_redirects"), "utf8");
  assert.ok(text.includes("/docs    /docs/    308"));
  assert.ok(text.includes("/compat  /compat/  308"));
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
  const docsDir = join(repoRoot, "docs");
  const pages: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, entry.name);
      if (entry.isDirectory()) walk(p);
      else if (entry.name.endsWith(".md")) pages.push(p);
    }
  };
  walk(docsDir);
  const withPlaceholder = pages.filter((p) => readFileSync(p, "utf8").includes("{{PYSC_COMPAT_URL}}"));
  assert.ok(withPlaceholder.length > 0, "no docs page carries {{PYSC_COMPAT_URL}}");
  for (const p of pages) {
    const text = readFileSync(p, "utf8");
    assert.ok(!text.includes("pyscriptc-compat.pages.dev"), `hardcoded compat host in ${p}`);
  }
});

test("placeholder default is the single site", () => {
  const plugin = readFileSync(join(repoRoot, "docs", ".docmd", "plugins", "placeholders.js"), "utf8");
  assert.ok(plugin.includes("https://pyscriptc.pages.dev/compat"));
  assert.ok(!plugin.includes("pyscriptc-compat.pages.dev"));
});

// -- workflows: the seam that runs these very tests --

test("site workflow runs pnpm, not python", () => {
  const text = readFileSync(join(repoRoot, ".github", "workflows", "site.yml"), "utf8");
  assert.ok(text.includes("wrangler pages deploy"));
  assert.ok(text.includes("--project-name=pyscriptc"));
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
