// The PostHog configuration and the consent checks, as pure functions with
// no build-time env dependency, so the unit tests can assert the privacy
// posture without a browser, a network or a token.
//
// Posture (matches PRIVACY.md and docs/project/telemetry.md):
// - PostHog EU (eu.i.posthog.com), the same host the CLI telemetry uses.
// - Cookieless: persistence "memory", so nothing is written to localStorage
//   or cookies and every page view starts from a fresh anonymous id.
// - No person profiles (`person_profiles: "never"`), no session recording,
//   no surveys, no heatmaps, no dead-click, rage-click or exception capture.
// - Do Not Track and Global Privacy Control are treated as opt-out.
// - The client IP is dropped from every event; the authoritative control is
//   the PostHog project setting "Discard client IP data".
// - Autocapture is limited to explicit `data-ph-event` elements, so no
//   element text, attributes or inputs are captured beyond what we declare.
// - The site identifies itself with a `site` super property from
//   PUBLIC_SITE_URL, so no hostname is hardcoded and a domain move is free.

export const POSTHOG_HOST = "https://eu.i.posthog.com";

export interface PostHogOptions {
  api_host: string;
  persistence: string;
  capture_pageview: boolean;
  capture_pageleave: boolean;
  autocapture: { dom_event_allowlist: string[]; css_selector_allowlist: string[] };
  person_profiles: string;
  disable_session_recording: boolean;
  disable_surveys: boolean;
  capture_heatmaps: boolean;
  capture_dead_clicks: boolean;
  capture_exceptions: boolean;
  capture_performance: boolean;
  rageclick: boolean;
  respect_dnt: boolean;
  before_send: (event: { properties?: Record<string, unknown> }) => unknown;
}

/** The init options, in one place so both sites and the tests share them. */
export function posthogOptions(): PostHogOptions {
  return {
    api_host: POSTHOG_HOST,
    persistence: "memory",
    capture_pageview: true,
    capture_pageleave: true,
    autocapture: {
      dom_event_allowlist: ["click", "submit"],
      css_selector_allowlist: ["[data-ph-event]"],
    },
    person_profiles: "never",
    disable_session_recording: true,
    disable_surveys: true,
    capture_heatmaps: false,
    capture_dead_clicks: false,
    capture_exceptions: false,
    capture_performance: false,
    rageclick: false,
    respect_dnt: true,
    before_send: (event) => {
      if (event && event.properties) delete event.properties.$ip;
      return event;
    },
  };
}

/** Global Privacy Control, treated the same as Do Not Track. */
export function gpcEnabled(nav: { globalPrivacyControl?: unknown } | undefined): boolean {
  return Boolean(nav && nav.globalPrivacyControl);
}

/** True when the browser asked not to be tracked (DNT or GPC). */
export function consentBlocked(nav: { doNotTrack?: string; globalPrivacyControl?: unknown } | undefined): boolean {
  if (!nav) return false;
  const dnt = nav.doNotTrack;
  const dntOn = dnt === "1" || dnt === "yes";
  return dntOn || gpcEnabled(nav);
}
