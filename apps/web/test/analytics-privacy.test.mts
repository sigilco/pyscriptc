// Privacy posture of the site analytics, from src/lib/analytics-config.ts
// (the pure, browser-free config the analytics module and the docs plugin
// mirror). No network, no browser, no token: everything here is pure data.
//
//   pnpm --dir apps/web test
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { POSTHOG_HOST, consentBlocked, gpcEnabled, posthogOptions } from "../src/lib/analytics-config.ts";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "..");
const repoRoot = resolve(webRoot, "..", "..");

test("posthog host is the EU host", () => {
  assert.equal(POSTHOG_HOST, "https://eu.i.posthog.com");
});

test("persistence is memory (cookieless, no storage)", () => {
  const options = posthogOptions();
  assert.equal(options.persistence, "memory");
});

test("no person profiles, no recording, no surveys, no heatmaps", () => {
  const options = posthogOptions();
  assert.equal(options.person_profiles, "never");
  assert.equal(options.disable_session_recording, true);
  assert.equal(options.disable_surveys, true);
  assert.equal(options.capture_heatmaps, false);
  assert.equal(options.capture_dead_clicks, false);
  assert.equal(options.capture_exceptions, false);
  assert.equal(options.capture_performance, false);
  assert.equal(options.rageclick, false);
});

test("DNT and GPC are respected as opt-out", () => {
  assert.equal(posthogOptions().respect_dnt, true);
  assert.equal(consentBlocked({ doNotTrack: "1" }), true);
  assert.equal(consentBlocked({ doNotTrack: "yes" }), true);
  assert.equal(consentBlocked({ globalPrivacyControl: true }), true);
  assert.equal(consentBlocked({ doNotTrack: "0", globalPrivacyControl: false }), false);
  assert.equal(consentBlocked(undefined), false);
  assert.equal(gpcEnabled(undefined), false);
});

test("the client IP is dropped from every event", () => {
  const event = { properties: { $ip: "203.0.113.9", other: 1 } };
  posthogOptions().before_send(event);
  assert.equal("$ip" in event.properties, false);
  assert.equal(event.properties.other, 1);
});

test("autocapture is limited to data-ph-event click/submit targets", () => {
  const options = posthogOptions();
  assert.deepEqual(options.autocapture.dom_event_allowlist, ["click", "submit"]);
  assert.deepEqual(options.autocapture.css_selector_allowlist, ["[data-ph-event]"]);
});

test("no real token or hostname is committed anywhere in the analytics sources", () => {
  const sources = [
    join(webRoot, "src", "lib", "analytics.ts"),
    join(webRoot, "src", "lib", "analytics-config.ts"),
    join(webRoot, "src", "components", "Analytics.astro"),
    join(repoRoot, "docs", ".docmd", "plugins", "analytics.js"),
  ];
  for (const path of sources) {
    const text = readFileSync(path, "utf8");
    assert.ok(!/phc_[A-Za-z0-9]{20,}/.test(text), `${path} contains a token literal`);
    // The only allowed URL mention is the PostHog EU host.
    const urls = text.match(/https:\/\/[a-z0-9.-]+/g) ?? [];
    for (const url of urls) {
      assert.ok(
        url === "https://eu.i.posthog.com" || url === "https://cdn.jsdelivr.net",
        `${path} hardcodes ${url}`,
      );
    }
  }
});

test("the docs plugin mirrors the Astro options", () => {
  const plugin = readFileSync(join(repoRoot, "docs", ".docmd", "plugins", "analytics.js"), "utf8");
  const options = posthogOptions();
  assert.ok(plugin.includes('"https://eu.i.posthog.com"'), "docs plugin must use the EU host");
  assert.ok(plugin.includes('persistence:"memory"'), "docs plugin must be cookieless");
  assert.ok(plugin.includes('person_profiles:"never"'), "docs plugin must disable person profiles");
  assert.ok(plugin.includes("autocapture:false"), "docs plugin must disable autocapture");
  for (const name of ["disable_session_recording", "disable_surveys", "capture_heatmaps", "capture_dead_clicks", "capture_exceptions", "capture_performance", "rageclick", "respect_dnt"]) {
    const expected = options[name as keyof typeof options] as boolean;
    assert.ok(plugin.includes(`${name}:${expected}`), `docs plugin option ${name} must be ${expected}`);
  }
});
