// Validate the docs sources: every internal link must resolve.
//
// docmd's link validator reads the raw markdown and does not run the docmd
// plugins, so `{{PYSC_COMPAT_URL}}` would be reported as a broken relative
// link. The docs sources are copied to a temp directory, the placeholders are
// expanded the same way the site build does, and docmd's validate runs over
// the copy.
//
// The placeholders and their defaults live in
// docs/.docmd/plugins/placeholders.js; the environment variable overrides the
// default in both.
//
//   pnpm --dir apps/web run docs:validate
import { execFileSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..", "..");
const docsDir = join(repoRoot, "docs");
const docmd = join(docsDir, ".docmd", "node_modules", ".bin", "docmd");

const PLACEHOLDERS: Array<[name: string, env: string, fallback: string]> = [
  ["PYSC_COMPAT_URL", "PYSC_COMPAT_URL", "https://pyscriptc.pages.dev/compat"],
  ["PYSC_DOCS_URL", "PYSC_DOCS_URL", "https://pyscriptc.pages.dev"],
];

if (!existsSync(docsDir)) {
  console.error(`docs directory not found: ${docsDir}`);
  process.exit(2);
}
if (!existsSync(docmd)) {
  console.error(
    "docmd not installed: run `pnpm --dir docs/.docmd install` (or `mise run setup`)",
  );
  process.exit(2);
}

const work = mkdtempSync(join(tmpdir(), "pyscriptc-docs-"));
try {
  cpTree(docsDir, join(work, "docs"), ["node_modules"]);
  symlinkSync(join(docsDir, ".docmd", "node_modules"), join(work, "docs", ".docmd", "node_modules"), "dir");
  // Some docs pages link into the internal notes (.agents/docs), one level
  // above the repo's docs/. Mirror it so docmd can resolve those links.
  const agentsTarget = join(docsDir, "..", ".agents");
  if (existsSync(agentsTarget)) {
    symlinkSync(agentsTarget, join(work, ".agents"), "dir");
  }
  for (const path of listFiles(join(work, "docs"), ".md")) {
    let text = readFileSync(path, "utf8");
    for (const [name, env, fallback] of PLACEHOLDERS) {
      text = text.split("{{" + name + "}}").join(process.env[env] || fallback);
    }
    writeFileSync(path, text, "utf8");
  }
  // The docmd config lives in docs/.docmd/ and resolves src relative to its
  // own directory, so the validator runs from there.
  execFileSync(docmd, ["validate"], {
    cwd: join(work, "docs", ".docmd"),
    stdio: "inherit",
  });
} finally {
  rmSync(work, { recursive: true, force: true });
}

function listFiles(root: string, suffix: string): string[] {
  const out: string[] = [];
  const walk = (dir: string): void => {
    let entries: Array<{ name: string; isDirectory(): boolean }>;
    try {
      entries = readdirSync(dir, { withFileTypes: true }) as unknown as Array<{
        name: string;
        isDirectory(): boolean;
      }>;
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(suffix)) out.push(full);
    }
  };
  walk(root);
  return out;
}

function cpTree(from: string, to: string, skip: string[] = []): void {
  mkdirSync(to, { recursive: true });
  for (const entry of readdirSync(from, { withFileTypes: true })) {
    if (skip.includes(entry.name)) continue;
    const src = join(from, entry.name);
    const dst = join(to, entry.name);
    if (entry.isDirectory()) cpTree(src, dst, skip);
    else copyFileSync(src, dst);
  }
}
