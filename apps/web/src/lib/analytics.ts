// Privacy-respecting PostHog web analytics for the pyscriptc site.
//
// The posture lives in src/lib/analytics-config.ts (pure data, shared with
// the tests and mirrored by the docs plugin): PostHog EU, cookieless
// (persistence "memory"), no person profiles, no session recording, no
// surveys or heatmaps, DNT and GPC respected, no client IP stored, and
// autocapture limited to explicit `data-ph-event` elements.
//
// Without PUBLIC_POSTHOG_TOKEN at build time everything here is a no-op and
// the PostHog bundle is never loaded. The token is a PostHog project API key
// (public by design, like the CLI's), supplied at build time; it is never
// committed.
import { POSTHOG_HOST, consentBlocked, posthogOptions } from "./analytics-config";

export { POSTHOG_HOST, gpcEnabled, consentBlocked } from "./analytics-config";

type PostHog = typeof import("posthog-js").default;

const TOKEN: string = import.meta.env.PUBLIC_POSTHOG_TOKEN ?? "";
// The deployment origin, as a super property. Empty means "not configured":
// no hostname is ever hardcoded, so the site can move domains freely.
const SITE: string = import.meta.env.PUBLIC_SITE_URL ?? "";

let initialized = false;
// The PostHog client, set by init() once the bundle is loaded.
let client: PostHog | null = null;

export function enabled(): boolean {
  return Boolean(TOKEN);
}

/** Initialize PostHog once, lazily. A no-op without a token or under DNT/GPC. */
export async function init(): Promise<void> {
  if (initialized || !TOKEN || typeof window === "undefined") return;
  if (consentBlocked(navigator as unknown as { doNotTrack?: string; globalPrivacyControl?: unknown })) {
    return;
  }
  initialized = true;
  const { default: posthog } = await import("posthog-js");
  client = posthog;
  posthog.init(TOKEN, posthogOptions() as never);
  // Registered on every event, like super properties. `site` comes from
  // PUBLIC_SITE_URL so the deployment host is never hardcoded.
  if (SITE) posthog.register({ site: SITE });
}

/** Track one custom event. A no-op without a token or before init. */
export function track(event: string, properties?: Record<string, unknown>): void {
  if (!initialized || !client) return;
  try {
    client.capture(event, properties);
  } catch {
    // Analytics must never break the page.
  }
}

/**
 * Track scroll depth on the landing page: one event per quarter of the page
 * reached. Cheap, no layout thrash (reads on scroll, throttled by the
 * browser) and it answers "do people read past the hero".
 */
export function bindScrollDepth(thresholds: number[] = [25, 50, 75, 100]): void {
  if (!initialized || typeof window === "undefined") return;
  const seen = new Set<number>();
  const onScroll = () => {
    const doc = document.documentElement;
    const total = doc.scrollHeight - window.innerHeight;
    if (total <= 0) return;
    const percent = Math.min(100, Math.round((window.scrollY / total) * 100));
    for (const threshold of thresholds) {
      if (percent >= threshold && !seen.has(threshold)) {
        seen.add(threshold);
        track("scroll_depth", { percent: threshold });
      }
    }
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
}

/**
 * Wire declarative tracking: every element with `data-ph-event="<name>"`
 * captures `<name>` on click, with `data-ph-props` JSON (optional) and a
 * `label` property from `data-ph-label` (optional). Idempotent; safe to call
 * again after DOM changes.
 */
export function bindDataAttributes(root: ParentNode = document): void {
  if (!initialized) return;
  for (const el of Array.from(root.querySelectorAll<HTMLElement>("[data-ph-event]"))) {
    if (el.dataset.phBound === "1") continue;
    el.dataset.phBound = "1";
    el.addEventListener("click", () => {
      let props: Record<string, unknown> = {};
      const raw = el.dataset.phProps;
      if (raw) {
        try {
          props = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          props = {};
        }
      }
      if (el.dataset.phLabel) props.label = el.dataset.phLabel;
      track(el.dataset.phEvent as string, props);
    });
  }
}
