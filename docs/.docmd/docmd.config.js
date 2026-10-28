// docmd site for pyscriptc. Source lives in docs/ (the user-facing pages);
// this config lives at docs/.docmd/ and every path in it is relative to that
// directory. docmd resolves src/out against the config file's directory and
// local plugin paths against the process working directory, so the build has
// to run with docs/.docmd as its cwd (see mise.toml docs:* tasks).
// Internal developer notes live in .agents/docs/ and are served by
// docmd.internal.config.js, not by this config.
//
// The docs are the /docs/ part of the single site at pyscriptc.pages.dev
// (the root is the landing page, /compat/ is the compat page). The base is
// /docs/ so every asset, link, canonical URL and the sitemap point into the
// /docs/ subtree; the site assembly (apps/web/scripts/assemble.mts) copies this
// build to deploy/docs.
//
// docmd's bundled `llms` plugin writes llms.txt and llms-full.txt for every
// page at build time (title, description and the `llms: false` and `noindex`
// frontmatter opt-outs are honored). apps/web/scripts/assemble.mts then appends the
// public data files, which are not pages, as a post-build step.
//
// The `placeholders` plugin substitutes {{PYSC_COMPAT_URL}} and
// {{PYSC_DOCS_URL}} in page sources. Each placeholder reads its environment
// variable and falls back to the default in plugins/placeholders.js (next to
// this config).
export default {
  title: "pyscriptc",
  description:
    "Developer and agent documentation for pyscriptc: compile annotated Python to native binaries. Island is the floor and native is the ceiling.",
  url: "https://pyscriptc.pages.dev/docs",
  src: "..",
  out: "../../dist/docs",
  base: "/docs/",
  engine: "js",
  plugins: {
    "./plugins/placeholders.js": {},
    "./plugins/og-image.js": {},
    // The ai plugin is a core plugin and loads on every build; `assistant:
    // false` makes generateScripts()/getAssets() emit nothing
    // (plugin-ai/dist/index.js checks `pluginOptions.assistant === false`).
    "ai": { "assistant": false },
  },
  logo: {
    light: "./assets/logo-256.png",
    dark: "./assets/logo-256.png",
    href: "/",
    alt: "pyscriptc",
    height: "28px",
  },
  favicon: "./assets/icon-64.png",
  layout: {
    spa: true,
    header: {
      enabled: true,
    },
    sidebar: {
      collapsible: true,
      defaultCollapsed: false,
    },
    optionsMenu: {
      position: "sidebar-top",
      components: {
        search: true,
        themeSwitch: true,
      },
    },
    footer: {
      style: "minimal",
      content: "Apache-2.0 license.",
      branding: false,
    },
  },
  theme: {
    name: "default",
    appearance: "system",
    codeHighlight: true,
    customCss: ["./theme.css"],
  },
  minify: true,
  autoTitleFromH1: true,
  copyCode: true,
  pageNavigation: true,
  navigation: [
    { title: "Home", path: "/", icon: "house" },
    {
      title: "Getting started",
      icon: "rocket",
      collapsible: true,
      children: [
        { title: "Getting started", path: "/getting-started/" },
      ],
    },
    {
      title: "Guide",
      icon: "book-open",
      collapsible: true,
      children: [
        { title: "The Python subset", path: "/guide/python-subset/" },
        { title: "Using libraries", path: "/guide/libraries/" },
        { title: "HTTP", path: "/guide/http/" },
        { title: "Debugging and logs", path: "/guide/debugging/" },
        { title: "Mobile apps", path: "/guide/mobile/" },
        { title: "Examples", path: "/examples/" },
      ],
    },
    {
      title: "Reference",
      icon: "list",
      collapsible: true,
      children: [
        { title: "CLI", path: "/reference/cli/" },
        { title: "Config", path: "/reference/config/" },
        { title: "Diagnostics", path: "/reference/diagnostics/" },
        { title: "Divergences from CPython", path: "/reference/divergences/" },
        { title: "Runtime pack and licenses", path: "/reference/runtime-pack/" },
        { title: "Stability and contracts", path: "/reference/stability/" },
      ],
    },
    {
      title: "Project",
      icon: "wrench",
      collapsible: true,
      children: [
        { title: "Supported CPython versions", path: "/project/versions/" },
        { title: "Telemetry", path: "/project/telemetry/" },
        { title: "Trying the beta", path: "/project/beta/" },
        { title: "Contributing", path: "/project/contributing/" },
      ],
    },
    { title: "GitHub", path: "https://github.com/sigilco/pyscriptc", icon: "github" },
  ],
};
