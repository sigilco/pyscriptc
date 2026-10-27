// Validate the assembled site tree. Three checks, run in parallel:
//
//   links       every internal link in the deploy tree must resolve
//   placeholders no {{...}} placeholder may survive in a built page
//   docs        every internal link in the docs sources must resolve
//               (docmd validate, over a copy with the placeholders expanded)
//
// The checks are independent, so they run concurrently and all of them run
// even if one fails: the report says everything that is wrong, not just the
// first thing.
//
//   pnpm --dir apps/web run validate
import { execFileSync } from "node:child_process";
import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "..", "..");

// The tree to validate. --deploy overrides it, which is what the tests use
// to point the checker at a planted tree.
const deployArgIndex = process.argv.indexOf("--deploy");
const deploy =
  deployArgIndex >= 0 && process.argv[deployArgIndex + 1]
    ? resolve(process.argv[deployArgIndex + 1])
    : join(webRoot, "deploy");

// The site map: a URL path prefix and the directory under deploy/ that
// serves it. The landing page is the root, the docs under /docs, the compat
// page under /compat.
const SITE_MAP: Array<[prefix: string, directory: string]> = [
  ["/docs/", "docs"],
  ["/compat/", "compat"],
];

interface CheckResult {
  name: string;
  problems: string[];
  summary: string;
}

async function main(): Promise<void> {
  if (!existsSync(deploy)) {
    console.error(`validate: no deploy tree at ${relative(repoRoot, deploy)}`);
    console.error("run: pnpm --dir apps/web run build");
    process.exit(2);
  }

  const checks: Array<[name: string, run: () => CheckResult]> = [
    ["links", checkLinks],
    ["placeholders", checkPlaceholders],
    ["docs", checkDocs],
  ];

  const settled = await Promise.allSettled(checks.map(([, run]) => run()));

  let failed = 0;
  const width = Math.max(...checks.map(([name]) => name.length));
  settled.forEach((outcome, index) => {
    const name = checks[index][0];
    if (outcome.status === "rejected") {
      failed += 1;
      console.error(`${name.padEnd(width)}  FAIL  ${String(outcome.reason)}`);
      return;
    }
    const result = outcome.value;
    if (result.problems.length > 0) {
      failed += 1;
      console.error(`${name.padEnd(width)}  FAIL  ${result.problems.length} problem(s)`);
      for (const problem of result.problems) console.error(`  - ${problem}`);
    } else {
      console.log(`${name.padEnd(width)}  ok    ${result.summary}`);
    }
  });

  if (failed > 0) {
    console.error(`validate: ${failed} check(s) failed`);
    process.exit(1);
  }
  console.log("validate: all checks passed");
}

// -- links --

function checkLinks(): CheckResult {
  const problems: string[] = [];
  const pages = listHtml(deploy);
  if (pages.length === 0) problems.push(`no HTML page found under ${deploy}`);
  for (const page of pages) {
    for (const link of collectLinks(readFileSync(page, "utf8"))) {
      if (link.startsWith("#")) continue;
      const problem = resolveLink(deploy, page, link);
      if (problem) problems.push(`${relative(deploy, page)}: ${problem}`);
    }
  }
  return { name: "links", problems, summary: `${pages.length} pages, no broken link` };
}

function resolveLink(deployRoot: string, page: string, link: string): string | null {
  const scheme = schemeOf(link);
  if (scheme && scheme !== "http" && scheme !== "https" && scheme !== "mailto") return null;
  if (scheme === "http" || scheme === "https") return null;

  const path = pathOf(link);
  if (!path) return null;

  if (path.startsWith("/")) {
    if (path === "/") return exists(join(deployRoot, "index.html")) ? null : `broken link ${link}`;
    const mapped = SITE_MAP.find(([prefix]) => path.startsWith(prefix));
    if (!mapped) {
      let candidate = join(deployRoot, path.slice(1));
      if (isDir(candidate)) candidate = join(candidate, "index.html");
      return exists(candidate) ? null : `broken link ${link}`;
    }
    const [prefix, directory] = mapped;
    const rest = path.slice(prefix.length).replace(/^\//, "");
    let candidate = rest ? join(deployRoot, directory, rest) : join(deployRoot, directory);
    if (isDir(candidate)) candidate = join(candidate, "index.html");
    return exists(candidate) ? null : `broken link ${link}`;
  }

  let candidate = resolve(dirname(page), path);
  if (!candidate.startsWith(resolve(deployRoot))) return null;
  if (isDir(candidate)) candidate = join(candidate, "index.html");
  return exists(candidate) ? null : `broken link ${link}`;
}

// -- placeholders --

const PLACEHOLDERS = ["{{BASE}}", "{{PYSC_COMPAT_URL}}", "{{PYSC_DOCS_URL}}"];

function checkPlaceholders(): CheckResult {
  const problems: string[] = [];
  const pages = listHtml(deploy);
  for (const page of pages) {
    const text = readFileSync(page, "utf8");
    for (const placeholder of PLACEHOLDERS) {
      if (text.includes(placeholder)) {
        problems.push(`unexpanded placeholder ${placeholder} in ${relative(deploy, page)}`);
      }
    }
  }
  return {
    name: "placeholders",
    problems,
    summary: `${pages.length} pages, no placeholder left`,
  };
}

// -- docs --

// docmd's link validator reads the raw markdown and does not run the
// docmd plugins, so {{PYSC_COMPAT_URL}} would be reported as a broken
// relative link. The docs sources are copied to a temp directory, the
// placeholders are expanded the same way the site build does, and docmd's
// validate runs over the copy.
const DOC_PLACEHOLDERS: Array<[name: string, env: string, fallback: string]> = [
  ["PYSC_COMPAT_URL", "PYSC_COMPAT_URL", "https://pyscriptc.pages.dev/compat"],
  ["PYSC_DOCS_URL", "PYSC_DOCS_URL", "https://pyscriptc.pages.dev"],
];

function checkDocs(): CheckResult {
  const docsDir = join(repoRoot, "docs");
  const docmd = join(docsDir, ".docmd", "node_modules", ".bin", "docmd");
  if (!existsSync(docmd)) {
    return {
      name: "docs",
      problems: ["docmd is not installed: run `pnpm --dir docs/.docmd install`"],
      summary: "",
    };
  }
  const problems: string[] = [];
  try {
    // Same wrapper as `pnpm run docs:validate`: expand the placeholders over
    // a copy of the docs, then let docmd check every link.
    execFileSync(process.execPath, [join(here, "validate-docs.mts")], {
      cwd: webRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
  } catch (error) {
    const err = error as { stdout?: string; stderr?: string };
    const output = `${err.stdout ?? ""}${err.stderr ?? ""}`.trim();
    problems.push(output || "docmd validate failed");
  }
  return { name: "docs", problems, summary: "docmd links resolve" };
}

// -- shared helpers --

function collectLinks(html: string): string[] {
  const links: string[] = [];
  const pattern = /\b(?:href|src)\s*=\s*("([^"]*)"|'([^']*)')/gi;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(html)) !== null) {
    links.push(match[2] ?? match[3] ?? "");
  }
  return links;
}

function schemeOf(link: string): string | null {
  const match = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(link);
  return match ? match[1].toLowerCase() : null;
}

function pathOf(link: string): string {
  const withoutQuery = link.split("#")[0].split("?")[0];
  try {
    return decodeURIComponent(withoutQuery);
  } catch {
    return withoutQuery;
  }
}

function exists(path: string): boolean {
  try {
    statSync(path);
    return true;
  } catch {
    return false;
  }
}

function isDir(path: string): boolean {
  try {
    return statSync(path).isDirectory();
  } catch {
    return false;
  }
}

function listHtml(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string): void => {
    let entries: ReturnType<typeof readdirSync>;
    try {
      entries = readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries as unknown as Array<{
      name: string;
      isDirectory(): boolean;
    }>) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".html")) out.push(full);
    }
  };
  walk(root);
  return out;
}



await main();
