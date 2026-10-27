// Sync the canonical data tree (repo-root data/) and the shared site assets
// (docs/assets) into the Astro public dir, so the compat page ships its JSON,
// the benchmark files are public, and every page finds the logo.
// Synced output is gitignored: data/ and docs/assets are the only committed
// copies.
//
// Run as part of `pnpm build` (prebuild), not a source step.
import { copyFileSync, mkdirSync, readdirSync } from "node:fs";
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
