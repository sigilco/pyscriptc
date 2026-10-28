// docmd dev server for the internal developer notes (.agents/docs).
// Deliberately dev-only: internal notes are not built or published.
//
//   mise run docs:internal
//
// Serves the same docmd UI over .agents/docs with its own navigation.
// The `placeholders` plugin is shared with the public site. Paths are
// relative to docs/.docmd (this file's directory): src/out resolve against
// it, and the build runs with docs/.docmd as its cwd so local plugin
// paths resolve the same way.
export default {
  title: "pyscriptc dev notes",
  url: "",
  src: "../../.agents/docs",
  out: "../../dist/agents-docs",
  base: "/",
  engine: "js",
  plugins: {
    "./plugins/placeholders.js": {},
  },
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
      content: "Internal developer notes. Not published.",
      branding: false,
    },
  },
  theme: {
    name: "default",
    appearance: "system",
    codeHighlight: true,
  },
  minify: false,
  autoTitleFromH1: true,
  copyCode: true,
  pageNavigation: true,
  navigation: [
    { title: "Dev notes", path: "/", icon: "house" },
    { title: "Invariants", path: "/invariants/" },
    { title: "Debug mode", path: "/debug/" },
    { title: "Map", path: "/map/" },
    { title: "The loop", path: "/loop/" },
    { title: "Roadmap", path: "/roadmap/" },
    { title: "Benchmark audit", path: "/benchmarks-audit/" },
    {
      title: "Specs",
      icon: "file-text",
      collapsible: true,
      children: [
        { title: "E1 boundary", path: "/spec/e1-boundary/" },
        { title: "E1 entrypoint detector spec", path: "/spec/e1-entrypoint-detector-spec/" },
        { title: "E1 entrypoint detector results", path: "/spec/e1-entrypoint-detector-results/" },
        { title: "E2 exception model", path: "/spec/e2-exception-model/" },
        { title: "IR canonicity", path: "/spec/ir-canonicity/" },
        { title: "Fence repro probes", path: "/spec/fence-repro-probes/" },
        { title: "Import classifier", path: "/spec/import-classifier/" },
        { title: "Refused features (PS4xxx)", path: "/spec/refused-features/" },
        { title: "External oracles", path: "/spec/external-oracles/" },
        { title: "HTTP seam design", path: "/spec/http-seam-design/" },
        { title: "Wave C backlog", path: "/spec/wave-c-backlog/" },
      ],
    },
    {
      title: "Design notes",
      icon: "pencil",
      collapsible: true,
      children: [
        { title: "Checker bridge", path: "/design/checker-bridge/" },
        { title: "Coroutine lowering", path: "/design/coroutine-lowering/" },
        { title: "Backend contract", path: "/design/backend-contract/" },
      ],
    },
    {
      title: "FFI",
      icon: "plug",
      collapsible: true,
      children: [
        { title: "FFI manifest", path: "/ffi/ffi-manifest/" },
        { title: "ABI strategy", path: "/ffi/abi-strategy/" },
      ],
    },
    {
      title: "Benchmarks",
      icon: "activity",
      collapsible: true,
      children: [
        { title: "Compile-rate essence method", path: "/benchmarks/compile-rate-essence-method/" },
        { title: "Self-host baseline", path: "/benchmarks/self-host-baseline/" },
        { title: "Cross-compile assessment", path: "/benchmarks/cross-compile-assessment/" },
        { title: "E0 cost baseline", path: "/benchmarks/e0-cost-baseline/" },
      ],
    },
    {
      title: "Support data (generated)",
      icon: "database",
      collapsible: true,
      children: [
        { title: "Supported (generated)", path: "/support/supported/" },
        { title: "Top-100 libraries (generated)", path: "/support/compat-top100/" },
      ],
    },
    {
      title: "Release",
      icon: "tag",
      collapsible: true,
      children: [
        { title: "Release process", path: "/release/release-process/" },
        { title: "Version track", path: "/release/version-track/" },
        { title: "Python version window", path: "/release/python-version-window/" },
      ],
    },
  ],
};
