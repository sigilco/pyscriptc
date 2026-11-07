// docmd plugin: inject the PostHog web analytics loader on every docs page.
//
// Same posture as the Astro site (apps/web/src/lib/analytics.ts): PostHog EU,
// cookieless (persistence memory), no person profiles, no session recording,
// no surveys or heatmaps, DNT/GPC respected, no IP stored. The docs loader is
// a plain script (docmd pages are static HTML, not an Astro bundle), so it
// loads posthog-js from the jsDelivr CDN on an idle tick and duplicates the
// init options by hand. The two must stay in sync; see
// apps/web/src/lib/analytics.ts.
//
// Config comes from the build environment (never committed):
//   PUBLIC_POSTHOG_TOKEN  the PostHog project API key; empty means emit
//                         nothing at all (a complete no-op build)
//   PUBLIC_SITE_URL       the deployment origin, sent as a `site` super
//                         property so analytics survive a domain move; empty
//                         means no hostname is sent at all
//
// Pageviews are captured by posthog-js's capture_pageview (each docs page is
// a full HTML load even though the docmd UI is an SPA shell). Autocapture is
// off here; clicks on elements carrying data-ph-event are captured through
// one delegated listener instead, so nothing is ever captured from element
// text, inputs or untagged links.

export const plugin = {
  name: "analytics",
  version: "0.1.0",
  capabilities: ["head"],
};

export function generateScripts(config, options, target) {
  if (target === "body") return "";
  const token = process.env.PUBLIC_POSTHOG_TOKEN || "";
  if (!token) {
    // No token: emit nothing. The docs build is a complete no-op, exactly
    // like the Astro site without PUBLIC_POSTHOG_TOKEN.
    return "";
  }
  const site = process.env.PUBLIC_SITE_URL || "";
  // JSON-encoded once so any character in the env values stays valid HTML.
  const tokenJson = JSON.stringify(token);
  const siteJson = JSON.stringify(site);
  return `<script>window.PH_EU_CONFIG={token:${tokenJson},site:${siteJson}};</script>
<script>(function(){var s=function(){if(!window.PH_EU_CONFIG)return;
var gpc=navigator.globalPrivacyControl===true;
var dnt=navigator.doNotTrack==="1"||navigator.doNotTrack==="yes"||window.doNotTrack==="1";
if(gpc||dnt)return;
var sc=document.createElement("script");sc.async=true;sc.src="https://cdn.jsdelivr.net/npm/posthog-js@1.440.0/dist/array.no-external.min.js";
sc.onload=function(){
  if(!window.posthog)return;
  window.posthog.init(window.PH_EU_CONFIG.token,{
    api_host:"https://eu.i.posthog.com",
    persistence:"memory",
    capture_pageview:true,
    capture_pageleave:true,
    autocapture:false,
    person_profiles:"never",
    disable_session_recording:true,
    disable_surveys:true,
    capture_heatmaps:false,
    capture_dead_clicks:false,
    capture_exceptions:false,
    capture_performance:false,
    rageclick:false,
    respect_dnt:true,
    before_send:function(e){if(e&&e.properties)delete e.properties.$ip;return e;}
  });
  window.posthog.register({site:window.PH_EU_CONFIG.site});
  document.addEventListener("click",function(ev){
    var t=ev.target;var el=t&&t.closest?t.closest("[data-ph-event]"):null;
    if(!el||!window.posthog)return;
    var props={};
    if(el.getAttribute("data-ph-label"))props.label=el.getAttribute("data-ph-label");
    window.posthog.capture("docs_"+el.getAttribute("data-ph-event"),props);
  },true);
  // Install-tab switches on the getting-started page (docmd renders the tab
  // nav items without data attributes, so match on the container).
  document.addEventListener("click",function(ev){
    var t=ev.target;var tab=t&&t.closest?t.closest(".docmd-tabs-nav-item"):null;
    if(!tab||!window.posthog)return;
    var label=tab.textContent?tab.textContent.trim().toLowerCase().replace(/[^a-z0-9]+/g,"_"):"";
    window.posthog.capture("docs_install_tab",label?{tab:label}:{});
  },true);
  // Code-block copy buttons (the install command is a code block).
  document.addEventListener("click",function(ev){
    var t=ev.target;var btn=t&&t.closest?t.closest(".docmd-copy-btn"):null;
    if(!btn||!window.posthog)return;
    var pane=btn.closest(".docmd-tab-pane");
    window.posthog.capture("docs_install_copy",{source:pane?"docs_tabs":"docs_code"});
  },true);
  // Docs search usage.
  document.addEventListener("click",function(ev){
    var t=ev.target;
    var open=t&&t.closest?t.closest(".docmd-search-trigger"):null;
    if(open&&window.posthog)window.posthog.capture("docs_search_open",{});
  },true);
};
document.head.appendChild(sc);
};
if("requestIdleCallback" in window){requestIdleCallback(s);}else{setTimeout(s,1);}
})();</script>`;
}
