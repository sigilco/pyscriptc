// docmd site for pyscriptc. Source lives in site/ (docs/ holds internal
// design notes that are not part of the public site). Cloudflare Pages runs
// `pnpm install && pnpm docs:build` and publishes `docs/dist`.
export default {
  title: "pyscriptc",
  url: "https://pyscriptc.pages.dev",
  src: "site",
  out: "docs/dist",
  base: "/",
  engine: "js",
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
      title: "Guide",
      icon: "rocket",
      collapsible: true,
      children: [
        { title: "Quickstart", path: "/guide/getting-started/" },
        { title: "CLI", path: "/guide/cli/" },
        { title: "The Python subset", path: "/guide/language/" },
        { title: "Examples", path: "/guide/examples/" },
      ],
    },
    {
      title: "Operating",
      icon: "wrench",
      collapsible: true,
      children: [
        { title: "Debugging and logs", path: "/guide/debugging/" },
        { title: "Telemetry and privacy", path: "/guide/telemetry/" },
        { title: "Privacy notice", path: "/guide/privacy/" },
      ],
    },
    {
      title: "Reference",
      icon: "book-open",
      collapsible: true,
      children: [
        { title: "Version policy", path: "/guide/versions/" },
        { title: "Contributing", path: "/guide/contributing/" },
      ],
    },
    { title: "GitHub", path: "https://github.com/sigilco/pyscriptc", icon: "github" },
  ],
};
