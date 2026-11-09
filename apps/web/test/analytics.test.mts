// Build-emission tests for the site analytics: what reaches the deployed
// HTML with and without PUBLIC_POSTHOG_TOKEN. These run a real Astro build
// twice (tokenless and with a dummy token), which is slow, so they are in
// their own file and guarded:
//
//   pnpm --dir apps/web test:analytics
//
// The posture assertions (options, DNT/GPC, no committed tokens) live in
// analytics-privacy.test.mts, which runs with the default `pnpm test`.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "..", "..");

const dist = join(webRoot, "dist");
const astroDir = join(dist, "_astro");

function build(extraEnv: Record<string, string> = {}): void {
  execFileSync("pnpm", ["run", "build"], {
    cwd: webRoot,
    env: { ...process.env, ...extraEnv },
    stdio: "pipe",
  });
}

function chunkTexts(): Array<[string, string]> {
  if (!existsSync(astroDir)) return [];
  return readdirSync(astroDir)
    .filter((name) => name.endsWith(".js"))
    .map((name) => [name, readFileSync(join(astroDir, name), "utf8")]);
}

test("no-token build emits no PostHog code at all", () => {
  build({ PUBLIC_POSTHOG_TOKEN: "", PUBLIC_SITE_URL: "" });
  for (const page of ["index.html", "compat/index.html"]) {
    const html = readFileSync(join(dist, page), "utf8");
    assert.ok(!html.includes("posthog"), `${page} mentions posthog`);
    assert.ok(!html.includes("eu.i.posthog.com"), `${page} mentions the PostHog host`);
    assert.ok(!html.includes("phc_"), `${page} mentions a token literal`);
  }
  for (const [name, text] of chunkTexts()) {
    assert.ok(
      !text.includes("posthog-js") && !text.includes("eu.i.posthog.com"),
      `${name} carries PostHog code without a token`,
    );
  }
});

test("no-token docs build emits no analytics script", () => {
  const docmdDir = join(repoRoot, "docs", ".docmd");
  if (!existsSync(join(docmdDir, "node_modules"))) return; // docmd not installed
  execFileSync("pnpm", ["run", "docs:build"], {
    cwd: docmdDir,
    env: { ...process.env, PUBLIC_POSTHOG_TOKEN: "" },
    stdio: "pipe",
  });
  const html = readFileSync(join(repoRoot, "dist", "docs", "index.html"), "utf8");
  assert.ok(!html.includes("PH_EU_CONFIG"), "docs page carries the analytics config");
  assert.ok(!html.includes("posthog"), "docs page mentions posthog");
});

test("token docs build carries the loader and the privacy options", () => {
  const docmdDir = join(repoRoot, "docs", ".docmd");
  if (!existsSync(join(docmdDir, "node_modules"))) return; // docmd not installed
  execFileSync("pnpm", ["run", "docs:build"], {
    cwd: docmdDir,
    env: { ...process.env, PUBLIC_POSTHOG_TOKEN: "phc_test_dummy" },
    stdio: "pipe",
  });
  const html = readFileSync(join(repoRoot, "dist", "docs", "index.html"), "utf8");
  assert.ok(html.includes("PH_EU_CONFIG"), "docs page misses the analytics loader");
  for (const option of [
    'persistence:"memory"',
    'person_profiles:"never"',
    "disable_session_recording:true",
    "disable_surveys:true",
    "capture_heatmaps:false",
    "capture_dead_clicks:false",
    "capture_exceptions:false",
    "capture_performance:false",
    "rageclick:false",
    "respect_dnt:true",
    "autocapture:false",
  ]) {
    assert.ok(html.includes(option), `docs init option missing or wrong: ${option}`);
  }
});

test("token build loads the bundle and the privacy options are all set", () => {
  build({ PUBLIC_POSTHOG_TOKEN: "phc_test_dummy", PUBLIC_SITE_URL: "https://analytics.test" });
  const html = readFileSync(join(dist, "index.html"), "utf8");
  assert.ok(!html.includes("phc_test_dummy"), "token must not leak into HTML, only the bundle");
  const carrying = chunkTexts().filter(
    ([, text]) => text.includes("eu.i.posthog.com") || text.includes("phc_test_dummy"),
  );
  assert.ok(carrying.length > 0, "no emitted chunk carries the PostHog init");
  const text = carrying.map(([, t]) => t).join("\n");
  for (const option of [
    "persistence:`memory`",
    "person_profiles:`never`",
    "disable_session_recording:!0",
    "disable_surveys:!0",
    "capture_heatmaps:!1",
    "capture_dead_clicks:!1",
    "respect_dnt:!0",
    "capture_exceptions:!1",
    "capture_performance:!1",
  ]) {
    assert.ok(text.includes(option), `init option missing or wrong after bundling: ${option}`);
  }
});
