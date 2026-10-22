// docmd site for pyscriptc. Source lives in docs/ (the user-facing pages).
// Internal developer notes live in .agents/docs/ and are served by
// docmd.internal.config.js, not by this config.
//
// Cloudflare Pages runs `pnpm install && pnpm docs:build` and publishes
// `dist/docs`. The output directory lives outside the source tree on purpose:
// docmd would otherwise copy the build output into the next build.
//
// The `placeholders` plugin substitutes {{PYSC_COMPAT_URL}} and
// {{PYSC_DOCS_URL}} in page sources. Each placeholder reads its environment
// variable and falls back to the default in docmd-plugins/placeholders.js.
export default {
  title: "pyscriptc",
  url: "https://pyscriptc.pages.dev",
  src: "docs",
  out: "dist/docs",
  base: "/",
  engine: "js",
  plugins: {
    "./docmd-plugins/placeholders.js": {},
  },
  logo: {
    light: "/assets/logo-256.png",
    dark: "/assets/logo-256.png",
    href: "/",
    alt: "pyscriptc",
    height: "28px",
  },
  favicon: "/assets/icon-64.png",
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
        { title: "Quickstart", path: "/getting-started/" },
        { title: "Install and toolchain", path: "/getting-started/install/" },
      ],
    },
    {
      title: "Guide",
      icon: "book-open",
      collapsible: true,
      children: [
        { title: "The Python subset", path: "/guide/support-model/" },
        { title: "Debugging and logs", path: "/guide/debugging/" },
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
        { title: "Divergences from CPython", path: "/reference/divergences/" },
      ],
    },
    {
      title: "Project",
      icon: "wrench",
      collapsible: true,
      children: [
        { title: "Supported CPython versions", path: "/project/versions/" },
        { title: "Telemetry", path: "/project/telemetry/" },
        { title: "Privacy notice", path: "/project/privacy/" },
        { title: "Contributing", path: "/project/contributing/" },
      ],
    },
    { title: "GitHub", path: "https://github.com/sigilco/pyscriptc", icon: "github" },
  ],
};
