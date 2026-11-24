// Sync the canonical data tree (repo-root data/) and the shared site assets
// (docs/assets) into the Astro public dir, so the compat page ships its JSON,
// the receipts page ships its own JSON, the benchmark files are public, and
// every page finds the logo.
// Synced output is gitignored: data/ and docs/assets are the only committed
// copies.
//
// Run as part of `pnpm build` (prebuild), not a source step.
import { copyFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "..", "..");
const publicDir = join(webRoot, "public");

const COPIES: Array<[sourceDir: string, outDir: string]> = [
  ["data/compat", "compat/data"],
  ["data/benchmarks", "benchmarks/data"],
  ["docs/assets", "."],
];

// The receipts page reads its own copy of the receipt document, so the page
// and its raw data live under the same prefix. The source file is the same
// committed one the compat page's data directory carries.
const RECEIPTS_DATA = "receipts.json";

for (const [sourceDir, outDir] of COPIES) {
  const source = join(repoRoot, sourceDir);
  const out = join(publicDir, outDir);
  const filter = sourceDir === "docs/assets" ? imageOnly : jsonOnly;
  let files: string[];
  try {
    files = readdirSync(source).filter(filter);
  } catch (error) {
    throw new Error(
      `sync-data: cannot read ${sourceDir}: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  if (files.length === 0) {
    throw new Error(`sync-data: no matching files in ${sourceDir}`);
  }
  mkdirSync(out, { recursive: true });
  for (const name of files) {
    copyFileSync(join(source, name), join(out, name));
  }
  console.log(`sync-data: ${sourceDir} -> public/${outDir} (${files.length} files)`);
}

function jsonOnly(name: string): boolean {
  return name.endsWith(".json");
}

function imageOnly(name: string): boolean {
  return /\.(png|ico|svg|webp)$/.test(name);
}

// The receipts data, copied next to the receipts page so the page's raw
// data link resolves. The file is optional: a tree without a receipt run
// yet still builds, and the page reads the committed data/compat copy.
const receiptsSource = join(repoRoot, "data", "compat", RECEIPTS_DATA);
if (existsSync(receiptsSource)) {
  const receiptsOut = join(publicDir, "receipts", "data");
  mkdirSync(receiptsOut, { recursive: true });
  copyFileSync(receiptsSource, join(receiptsOut, RECEIPTS_DATA));
  console.log(`sync-data: data/compat/${RECEIPTS_DATA} -> public/receipts/data`);
}
