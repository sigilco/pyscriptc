export const en = {
  nav: {
    logoAlt: "pyscriptc",
    playground: "Playground",
    playgroundBadge: "Preview",
    docs: "Docs",
    compat: "Are We Native Yet?",
    github: "GitHub",
    installCommand: "uv tool install pyscriptc",
    copied: "Copied!",
    langLabel: "EN",
  },
  hero: {
    badge: "Python to Standalone Native Binary",
    titleLine1: "Write typed Python.",
    titleLine2: "Get native speed.",
    subtitle:
      "Compile standard annotated Python into ultra-fast, zero-dependency native executables. No CPython runtime to bundle, zero container bloat, instant startup.",
    docsCta: "Read the Docs",
    compatCta: "Are We Native Yet?",
    installLabel: "Get started in seconds:",
    installCommand: "uv tool install pyscriptc",
  },
  metrics: [
    {
      val: "24",
      unit: "x",
      label: "Faster Execution",
      desc: "Geometric mean speedup across compute kernels vs standard CPython.",
    },
    {
      val: "6",
      unit: "x",
      label: "Lower Peak Memory",
      desc: "Radically reduced RSS footprint. Pack more tasks onto leaner instances.",
    },
    {
      val: "0",
      unit: "ms",
      label: "Cold Start",
      desc: "Instant sub-millisecond execution for CLI tools, lambdas & serverless.",
    },
    {
      val: "100",
      unit: "%",
      label: "Standalone Binary",
      desc: "Single self-contained file. Zero dependencies, zero virtualenvs to ship.",
    },
  ],
  parity: {
    badge: "Zero Dialect Drift",
    title: "Write standard Python. Keep byte-for-byte parity.",
    subtitle:
      "No new language to learn and no rewrite in Rust or C++. pyscriptc turns standard typed Python into high-performance native machine code with zero guessing.",
    terminalFile: "sum_squares.py",
    terminalTag: "Python 3.12+ typed",
    terminalRun: "$ pyscriptc compile sum_squares.py -o sum_squares",
    terminalStep1: "Static type verification",
    terminalStep2: "Emitted standalone native binary ./sum_squares (184 KB)",
    terminalExec: "$ ./sum_squares",
    terminalPerf: "0.6ms native",
    terminalVs: "(vs 17.6ms CPython — 29x faster)",
    guaranteeTitle: "Predictable, Not Probabilistic",
    guaranteeTag: "Deterministic",
    guaranteeDesc:
      "Unlike JIT engines that deoptimize unpredictably when types fluctuate at runtime, pyscriptc statically resolves your types at compile time. What compiles runs with steady, guaranteed native latency.",
    semTitle: "Full Python Semantics",
    semDesc:
      "Arbitrary precision integers, IEEE-754 floats, and standard exception handling preserved.",
    diagTitle: "Clear Compile-Time Diagnostics",
    diagDesc:
      "Actionable diagnostic codes highlight untyped constructs before shipping to production.",
  },
  surfaces: {
    title: "Three surfaces, one unified compiler.",
    subtitle:
      "Produce the exact executable format needed across your application architecture.",
    items: [
      {
        num: "01",
        tag: "Standalone Binary",
        title: "Pure Native Binary",
        desc: "Single-file executable with zero runtime dependencies. Sub-millisecond cold start, 24x faster execution, and 6x lighter memory footprint for microservices and CLI tools.",
        meta1: { k: "Runtime:", v: "None (Zero CPython)" },
        meta2: { k: "Format:", v: "ELF / Mach-O / PE" },
      },
      {
        num: "02",
        tag: "C-ABI Export",
        title: "Native Shared Library",
        desc: "Export standard C ABI functions with header files (.h). Embed high-speed compiled Python routines seamlessly into Rust, Go, Swift, C++, or Node.js.",
        meta1: { k: "Export target:", v: ".so / .dylib / .dll" },
        meta2: { k: "Integration:", v: "Rust, Go, C++, Swift" },
      },
      {
        num: "03",
        tag: "Edge & Sandboxes",
        title: "Universal WASM / Edge",
        desc: "Compile typed Python directly to lightweight WebAssembly (wasm32-wasi). Run secure sandboxed Python in browsers and edge workers with tiny payloads.",
        meta1: { k: "Architecture:", v: "wasm32-wasi" },
        meta2: { k: "Deployment:", v: "Cloudflare, Fastly, Browsers" },
      },
    ],
  },
  crossCompile: {
    badge: "Universal Cross-Compilation",
    title: "Compile anywhere, deploy everywhere.",
    subtitle:
      "Target any cloud, server, or edge architecture with a single flag. No multi-gigabyte Docker build containers, no complex cross-toolchain setup headaches.",
    targets: [
      { arch: "x86_64-linux", note: "glibc / musl" },
      { arch: "aarch64-macos", note: "Apple Silicon" },
      { arch: "x86_64-windows", note: "MSVC / MinGW" },
      { arch: "wasm32-wasi", note: "Edge & Browsers", highlight: true },
    ],
  },
  cta: {
    title: "Ready to ship Python as native code?",
    subtitle:
      "Start compiling typed Python into standalone executables in under two minutes.",
    primaryBtn: "Get Started with pyscriptc",
    secondaryBtn: "Star on GitHub",
  },
  footer: {
    tagline:
      "Compile standard typed Python to standalone native binaries. Zero CPython at runtime.",
    copyright: "© 2026 pyscriptc contributors. Released under the Apache-2.0 License.",
    status: "All systems operational",
  },
  playgroundModal: {
    title: "Web Playground in Progress",
    desc: "A WebAssembly-powered in-browser IDE with pyscriptc pre-installed is currently being prepared. In the meantime, install locally in seconds using uv.",
    cmd: "uv tool install pyscriptc",
    close: "Got it",
  },
};

export type Translations = typeof en;
