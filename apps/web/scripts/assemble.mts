// Assemble the single pyscriptc site into apps/web/deploy/ — the tree
// Cloudflare Pages publishes as https://pyscriptc.pages.dev:
//
//   deploy/index.html      the landing page      (apps/web dist, base /)
//   deploy/docs/           the user docs         (docmd output)
//   deploy/compat/         the compat page       (apps/web dist/compat, base /compat)
//   deploy/_headers        response headers      (apps/web/_headers)
//   deploy/_redirects      /docs and /compat 308 to their trailing-slash form
//
// Idempotent: deploy/ is wiped on every run. Every part must exist; there is
// no fallback build here — the parts are built before this script runs.
//
//   pnpm --dir apps/web run assemble
//
// Runs on node's native type stripping (node >= 22.18), so the code stays
// erasable-syntax only: no enums, no parameter properties.
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
// PYSC_WEB_ROOT overrides the web app root; the tests use it to point the
// assembler at a tree without build outputs.
const webRoot = process.env.PYSC_WEB_ROOT ? resolve(process.env.PYSC_WEB_ROOT) : resolve(here, "..");
const repoRoot = resolve(webRoot, "..", "..");
const docsOut = join(repoRoot, "dist", "docs");
const astroOut = join(webRoot, "dist");
const deploy = join(webRoot, "deploy");

const REQUIRED = [
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
];

function fail(message: string): never {
  console.error(`assemble: ${message}`);
  process.exit(2);
}

for (const dir of [astroOut, docsOut]) {
  if (!existsSync(dir)) fail(`missing build output: ${dir} (run the part builds first)`);
}

rmSync(deploy, { recursive: true, force: true });
mkdirSync(deploy, { recursive: true });

// The Astro build with base "/" produces the landing page at the deploy root
// and the compat page under dist/compat.
cpTree(astroOut, deploy);
cpTree(docsOut, join(deploy, "docs"));

// docmd emits `<link href="<assetBaseUrl>theme.css?v=...">` for every entry
// in theme.customCss but does not copy the file into its output, so every
// docs page would carry a broken stylesheet link. Ship it next to the docs
// index, which is where the relative URL resolves.
if (!existsSync(join(deploy, "docs", "theme.css"))) {
  const source = join(repoRoot, "docs", ".docmd", "theme.css");
  if (existsSync(source)) copyFileSync(source, join(deploy, "docs", "theme.css"));
}

// The llms files are docs artifacts but read at the deploy root.
for (const name of ["llms.txt", "llms-full.txt"]) {
  const from = join(deploy, "docs", name);
  if (existsSync(from)) copyFileSync(from, join(deploy, name));
}

copyFileSync(join(webRoot, "_headers"), join(deploy, "_headers"));
copyFileSync(join(webRoot, "_redirects"), join(deploy, "_redirects"));

// Append the Public Data Files section to the llms exports. docmd's llms
// plugin only lists pages; the data files are not pages. Idempotent: an
// earlier section is replaced, not duplicated.
appendLlmsDataSection();

const problems: string[] = [];
for (const rel of REQUIRED) {
  if (!existsSync(join(deploy, rel))) problems.push(`missing ${rel}`);
}

const redirects = readFileSync(join(deploy, "_redirects"), "utf8");
for (const rule of ["/docs    /docs/    308", "/compat  /compat/  308"]) {
  if (!redirects.includes(rule)) problems.push(`missing redirect rule ${rule}`);
}

// The compat page links its assets from the deploy root (absolute paths), so
// the landing assets cover it too.
for (const name of ["logo-256.png", "icon-64.png"]) {
  if (!existsSync(join(deploy, name))) problems.push(`missing site asset ${name}`);
}

// Only HTML pages are checked, and only for our own placeholders. Bundled
// JavaScript (KaTeX, mermaid) contains `{{` as literal syntax and
// llms-full.txt carries raw markdown, so those are skipped.
for (const path of listHtml(deploy)) {
  const text = readFileSync(path, "utf8");
  for (const placeholder of ["{{BASE}}", "{{PYSC_COMPAT_URL}}", "{{PYSC_DOCS_URL}}"]) {
    if (text.includes(placeholder)) {
      problems.push(`unexpanded placeholder ${placeholder} in ${relative(deploy, path)}`);
    }
  }
}

if (problems.length > 0) {
  for (const problem of problems) console.error(`site assembly: ${problem}`);
  process.exit(1);
}

console.log(`assembled ${countFiles(deploy)} files in apps/web/deploy`);

// -- helpers --

function appendLlmsDataSection(): void {
  const compatBase = "https://pyscriptc.pages.dev/compat";
  const descriptions: Record<string, [string, string]> = {
    "compat.json": [
      "Live compatibility metrics across builtins, standard library, and PyPI top-100 packages",
      "compat_schema.py (version 1)",
    ],
    "ladder.json": [
      "Adoption ladder scores across R1 (single-file), R2 (multi-module), and R4 (dependency lanes) rungs",
      "ladder JSON (version 1)",
    ],
    "what_will_fail.json": [
      "Known compiler gaps and fences measured on the R0 corpus grouped by root cause",
      "what_will_fail JSON (version 1)",
    ],
    "support.json": [
      "Evaluated support status for standard library and builtin functions from compile probes",
      "support JSON (version 1)",
    ],
  };

  const dataDir = join(repoRoot, "data", "compat");
  let names: string[];
  try {
    names = readdirSync(dataDir)
      .filter((name) => name.endsWith(".json"))
      .sort();
  } catch {
    fail(`cannot read ${dataDir}`);
  }
  const records = names.map((name) => {
    const fallback: [string, string] = [
      "Public compatibility data file",
      "JSON document (version 1)",
    ];
    const [desc, schema] = descriptions[name] ?? fallback;
    return { name, desc, schema, url: `${compatBase}/data/${name}` };
  });

  const shortSection =
    "## Public Data Files\n\n" +
    records.map((r) => `- [${r.name}](${r.url})\n  ${r.desc} (schema/format: ${r.schema}).`).join("\n") +
    "\n";
  const fullSection =
    "## Public Data Files Reference\n\n" +
    records
      .map(
        (r) =>
          `### [${r.name}](${r.url})\nDescription: ${r.desc}.\nSchema / Format: ${r.schema}.\nDirect link: ${r.url}\n\n---\n`,
      )
      .join("\n") +
    "\n";

  const targets: Array<[string, string, string]> = [
    ["llms.txt", "## Public Data Files", shortSection],
    ["llms-full.txt", "## Public Data Files Reference", fullSection],
  ];
  for (const [name, marker, section] of targets) {
    const path = join(deploy, name);
    if (!existsSync(path)) fail(`${name} not found in deploy: run the docmd build first`);
    const text = readFileSync(path, "utf8");
    const head = text.includes("\n" + marker) ? text.split("\n" + marker, 1)[0] : text;
    writeFileSync(path, head.trimEnd() + "\n\n" + section, "utf8");
  }
}

function cpTree(from: string, to: string): void {
  mkdirSync(to, { recursive: true });
  for (const entry of readdirSync(from, { withFileTypes: true })) {
    const src = join(from, entry.name);
    const dst = join(to, entry.name);
    if (entry.isDirectory()) cpTree(src, dst);
    else copyFileSync(src, dst);
  }
}

function listHtml(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".html")) out.push(full);
    }
  };
  walk(root);
  return out;
}

function countFiles(root: string): number {
  let count = 0;
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) walk(join(dir, entry.name));
      else count += 1;
    }
  };
  walk(root);
  return count;
}
