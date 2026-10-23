import React, { useState, useEffect } from "react";
import { HeroMeshGrid } from "./HeroMeshGrid";
import { LumioCarousel } from "./LumioCarousel";
import { IsometricSurfaceStack } from "./IsometricSurfaceStack";

export type VariationId = "v1-kyoto" | "v2-tokyo" | "v3-muji";

export interface VariationsStudioProps {
  initialVariation?: VariationId;
  initialTheme?: "dark" | "light";
}

export const VariationsStudio: React.FC<VariationsStudioProps> = ({
  initialVariation = "v1-kyoto",
  initialTheme = "dark",
}) => {
  const [variation, setVariation] = useState<VariationId>(initialVariation);
  const [theme, setTheme] = useState<"dark" | "light">(initialTheme);

  // Sync with URL query parameter ?v=1 | 2 | 3 if present in browser
  useEffect(() => {
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      const v = params.get("v");
      if (v === "1") setVariation("v1-kyoto");
      else if (v === "2") setVariation("v2-tokyo");
      else if (v === "3") setVariation("v3-muji");

      const t = params.get("theme");
      if (t === "light") setTheme("light");
      else if (t === "dark") setTheme("dark");
    }
  }, []);

  const switchVariation = (v: VariationId) => {
    setVariation(v);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.set("v", v === "v1-kyoto" ? "1" : v === "v2-tokyo" ? "2" : "3");
      window.history.replaceState({}, "", url.toString());
    }
  };

  const switchTheme = (t: "dark" | "light") => {
    setTheme(t);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.set("theme", t);
      window.history.replaceState({}, "", url.toString());
    }
  };

  const isDark = theme === "dark";

  return (
    <div
      className={`w-full min-h-screen transition-colors duration-500 font-sans antialiased ${
        isDark
          ? variation === "v1-kyoto"
            ? "bg-[#0c0d12] text-slate-100"
            : variation === "v2-tokyo"
            ? "bg-[#07080c] text-white"
            : "bg-[#101114] text-slate-200"
          : variation === "v1-kyoto"
          ? "bg-[#f7f7f5] text-[#111111]"
          : variation === "v2-tokyo"
          ? "bg-[#f1f3f7] text-[#0a0b0e]"
          : "bg-[#fbf9f5] text-[#1c1b1b]"
      }`}
    >
      {/* =========================================================================
          TOP FLOATING STUDIO CONTROLLER TOOLBAR (Always present for evaluation)
          ========================================================================= */}
      <div className="sticky top-0 z-50 w-full backdrop-blur-xl bg-black/80 border-b border-white/10 px-4 py-3 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs font-bold text-cyan-400 uppercase tracking-widest">
            Design Studio
          </span>
          <span className="text-slate-600">/</span>
          <div className="flex items-center gap-1.5 bg-white/5 p-1 rounded-xl border border-white/10">
            <button
              onClick={() => switchVariation("v1-kyoto")}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                variation === "v1-kyoto"
                  ? "bg-cyan-400 text-black font-bold shadow-md scale-105"
                  : "text-slate-300 hover:text-white"
              }`}
            >
              V1: Kyoto Minimal
            </button>
            <button
              onClick={() => switchVariation("v2-tokyo")}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                variation === "v2-tokyo"
                  ? "bg-cyan-400 text-black font-bold shadow-md scale-105"
                  : "text-slate-300 hover:text-white"
              }`}
            >
              V2: Tokyo Monolith
            </button>
            <button
              onClick={() => switchVariation("v3-muji")}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                variation === "v3-muji"
                  ? "bg-cyan-400 text-black font-bold shadow-md scale-105"
                  : "text-slate-300 hover:text-white"
              }`}
            >
              V3: MUJI Restraint
            </button>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 bg-white/5 p-1 rounded-xl border border-white/10">
            <button
              onClick={() => switchTheme("dark")}
              className={`px-3 py-1 rounded-lg text-xs font-mono transition-all ${
                isDark ? "bg-white/20 text-white font-bold" : "text-slate-400 hover:text-white"
              }`}
            >
              🌙 Dark
            </button>
            <button
              onClick={() => switchTheme("light")}
              className={`px-3 py-1 rounded-lg text-xs font-mono transition-all ${
                !isDark ? "bg-white/20 text-white font-bold" : "text-slate-400 hover:text-white"
              }`}
            >
              ☀️ Light
            </button>
          </div>
        </div>
      </div>

      {/* Variation Description Ribbon */}
      <div
        className={`w-full py-2 px-4 text-center font-mono text-xs border-b ${
          variation === "v1-kyoto"
            ? "bg-sky-950/30 border-sky-500/20 text-sky-300"
            : variation === "v2-tokyo"
            ? "bg-cyan-950/40 border-cyan-500/30 text-cyan-300"
            : "bg-amber-950/20 border-amber-500/20 text-amber-200"
        }`}
      >
        {variation === "v1-kyoto" && (
          <span>
            <strong>VARIATION 1: KYOTO PAPER & OBSIDIAN</strong> — Centered architectural editorial, warm neutrals, Lumio vertical timeline, and centered isometric stack.
          </span>
        )}
        {variation === "v2-tokyo" && (
          <span>
            <strong>VARIATION 2: TOKYO MONOLITH / CYBER-PRECISION</strong> — Asymmetrical 2-column hero split with live compiler workbench, technical monospace metadata, and blueprint grid.
          </span>
        )}
        {variation === "v3-muji" && (
          <span>
            <strong>VARIATION 3: MUJI STUDIO / PURE RESTRAINT</strong> — Ultra-minimalism inspired by MUJI & Notion. Extreme whitespace rhythm, zero decorative borders, pure typographic contrast.
          </span>
        )}
      </div>

      {/* =========================================================================
          VARIATION 1: KYOTO PAPER & OBSIDIAN (Centered Architectural Editorial)
          ========================================================================= */}
      {variation === "v1-kyoto" && (
        <div className="w-full">
          {/* Header */}
          <nav className="w-full max-w-7xl mx-auto px-6 py-6 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-cyan-500 to-sky-300 flex items-center justify-center shadow-[0_0_20px_rgba(0,194,255,0.4)]">
                <span className="font-mono font-bold text-xs text-black">py</span>
              </div>
              <span className="font-bold text-lg tracking-tight">pyscriptc</span>
            </div>
            <div className="hidden md:flex items-center gap-8 text-sm font-medium text-slate-400">
              <a href="/docs" className="hover:text-white transition-colors">Documentation</a>
              <a href="/compat" className="hover:text-white transition-colors">Are We Native Yet?</a>
              <a href="https://github.com/sigilco/pyscriptc" className="hover:text-white transition-colors">GitHub ↗</a>
            </div>
            <div className="flex items-center gap-4">
              <span className="text-xs font-mono text-slate-500">🌐 EN</span>
              <a
                href="/docs"
                className="px-5 py-2.5 rounded-full text-xs font-semibold bg-white text-black hover:bg-slate-200 transition-all shadow-sm"
              >
                Get Started ↗
              </a>
            </div>
          </nav>

          {/* Centered Editorial Hero with Protective Capsule */}
          <section className="relative w-full overflow-hidden flex flex-col items-center justify-center min-h-[82dvh] px-4">
            <HeroMeshGrid
              state="active"
              theme={theme}
              showGridLines={false}
              interactiveHover={true}
              activeCount={0}
              className="!min-h-[82dvh]"
            >
              <div className="relative z-10 max-w-4xl mx-auto px-6 py-14 text-center flex flex-col items-center">
                {/* Protective Blur Capsule */}
                <div
                  className={`absolute inset-0 -z-10 rounded-3xl backdrop-blur-md transition-colors duration-500 ${
                    isDark ? "bg-[#0c0d12]/70 shadow-[0_0_80px_rgba(12,13,18,0.95)]" : "bg-[#f7f7f5]/75 shadow-[0_0_80px_rgba(247,247,245,0.95)]"
                  }`}
                />
                <p className="text-xs sm:text-sm font-mono uppercase tracking-[0.25em] text-cyan-400 font-semibold mb-6">
                  CPython 3.14 Ahead-of-Time Compiler
                </p>
                <h1 className="text-4xl sm:text-6xl lg:text-7xl font-bold tracking-tight leading-[1.08] mb-6">
                  Run Python at <span className="bg-gradient-to-r from-cyan-400 via-sky-300 to-white bg-clip-text text-transparent">C Speed</span> without Rewriting a Single Line
                </h1>
                <p className="text-lg sm:text-xl text-slate-300 max-w-2xl font-light leading-relaxed mb-10">
                  The native ahead-of-time compiler turning Python workloads into pure standalone single binaries.
                  Zero Python interpreter. Zero virtualenv. Instant sub-millisecond cold start.
                </p>
                <div className="flex flex-wrap items-center justify-center gap-4">
                  <a
                    href="/docs"
                    className="group inline-flex items-center gap-3 px-8 py-4 rounded-full bg-cyan-400 text-slate-950 font-semibold text-sm shadow-[0_0_32px_rgba(0,194,255,0.4)] hover:bg-cyan-300 active:scale-[0.98] transition-all duration-300"
                  >
                    <span>Read the Docs</span>
                    <span className="w-6 h-6 rounded-full bg-black/15 flex items-center justify-center text-xs group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform">↗</span>
                  </a>
                  <a
                    href="/compat"
                    className="group inline-flex items-center gap-3 px-8 py-4 rounded-full bg-white/5 text-white border border-white/10 hover:border-white/25 hover:bg-white/10 active:scale-[0.98] transition-all duration-300 font-medium text-sm backdrop-blur-md"
                  >
                    <span>Are We Native Yet?</span>
                    <span className="w-6 h-6 rounded-full bg-white/10 flex items-center justify-center text-xs group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform">↗</span>
                  </a>
                </div>
              </div>
            </HeroMeshGrid>
          </section>

          {/* ROI Metric Strip */}
          <section className="w-full border-y border-white/5 py-12 bg-white/[0.01]">
            <div className="max-w-7xl mx-auto px-6 grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
              <div>
                <div className="text-3xl sm:text-5xl font-bold tracking-tight text-white mb-1">24x</div>
                <div className="text-xs sm:text-sm font-mono text-slate-400">Faster Execution</div>
              </div>
              <div>
                <div className="text-3xl sm:text-5xl font-bold tracking-tight text-white mb-1">6x</div>
                <div className="text-xs sm:text-sm font-mono text-slate-400">Lower Peak RSS</div>
              </div>
              <div>
                <div className="text-3xl sm:text-5xl font-bold tracking-tight text-cyan-400 mb-1">0ms</div>
                <div className="text-xs sm:text-sm font-mono text-slate-400">Cold Start Latency</div>
              </div>
              <div>
                <div className="text-3xl sm:text-5xl font-bold tracking-tight text-white mb-1">100%</div>
                <div className="text-xs sm:text-sm font-mono text-slate-400">CPython Semantics</div>
              </div>
            </div>
          </section>

          {/* Section 1: Lumio Carousel */}
          <LumioCarousel theme={theme} variant="warm-editorial" />

          {/* Section 2: Isometric Surface Stack */}
          <section className="w-full border-t border-white/5">
            <IsometricSurfaceStack theme={theme} />
          </section>
        </div>
      )}

      {/* =========================================================================
          VARIATION 2: TOKYO MONOLITH / CYBER-PRECISION (Asymmetrical Split Hero)
          ========================================================================= */}
      {variation === "v2-tokyo" && (
        <div className="w-full">
          {/* Header */}
          <nav className="w-full max-w-7xl mx-auto px-6 py-5 flex items-center justify-between border-b border-cyan-500/10">
            <div className="flex items-center gap-3">
              <span className="font-mono text-xs px-2 py-1 bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 rounded font-bold">
                PYSC-3.14
              </span>
              <span className="font-bold text-lg tracking-tight font-mono">pyscriptc</span>
            </div>
            <div className="hidden md:flex items-center gap-8 text-xs font-mono uppercase tracking-wider text-slate-400">
              <a href="/docs" className="hover:text-cyan-400 transition-colors">// Documentation</a>
              <a href="/compat" className="hover:text-cyan-400 transition-colors">// Compatibility</a>
              <a href="https://github.com/sigilco/pyscriptc" className="hover:text-cyan-400 transition-colors">// GitHub [v0.6.0]</a>
            </div>
            <div className="flex items-center gap-4">
              <a
                href="/docs"
                className="px-4 py-2 font-mono text-xs font-bold bg-cyan-400 text-black rounded hover:bg-cyan-300 transition-all shadow-[0_0_20px_rgba(0,194,255,0.4)]"
              >
                INSTALL_CLI ↗
              </a>
            </div>
          </nav>

          {/* Asymmetrical 2-Column Split Hero */}
          <section className="relative w-full max-w-7xl mx-auto px-6 py-16 lg:py-24">
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
              {/* Left Column (7 cols): Editorial Monolith Headline */}
              <div className="lg:col-span-7 space-y-8 text-left">
                <div className="inline-flex items-center gap-2 font-mono text-xs text-cyan-400">
                  <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
                  <span>AOT Machine Code Emission Engine</span>
                </div>
                <h1 className="text-4xl sm:text-6xl font-black tracking-tight leading-[1.05] text-white">
                  Full CPython 3.14 semantics.<br />
                  <span className="text-cyan-400">Compiled to native metal.</span>
                </h1>
                <p className="text-lg text-slate-300 font-light leading-relaxed max-w-xl">
                  pyscriptc lowers pure Python AST into typed SSA IR and produces zero-dependency machine binaries.
                  Zero interpreter startup tax. Zero runtime dependencies. 24x faster execution.
                </p>

                {/* Live CLI Install Box */}
                <div className="p-4 rounded-xl bg-white/[0.03] border border-cyan-500/20 font-mono text-xs max-w-lg flex items-center justify-between">
                  <span className="text-slate-400">$ uv tool install pyscriptc</span>
                  <span className="text-cyan-400 font-bold text-[11px] uppercase tracking-wider">CPython 3.14 Target</span>
                </div>

                <div className="flex flex-wrap items-center gap-4 pt-2">
                  <a
                    href="/docs"
                    className="px-7 py-3.5 rounded-lg bg-cyan-400 text-black font-bold text-sm font-mono shadow-[0_0_24px_rgba(0,194,255,0.35)] hover:bg-cyan-300 transition-all"
                  >
                    DEPLOY_BINARY ↗
                  </a>
                  <a
                    href="/compat"
                    className="px-7 py-3.5 rounded-lg bg-white/5 text-white border border-white/10 hover:border-cyan-500/40 transition-all text-sm font-mono"
                  >
                    ORACLE_MATRIX [72 GATES] ↗
                  </a>
                </div>
              </div>

              {/* Right Column (5 cols): Embedded Real-Time Compiler Diagnostics Panel */}
              <div className="lg:col-span-5">
                <div className="rounded-2xl border border-cyan-500/30 bg-[#090b12] p-5 shadow-[0_20px_50px_rgba(0,0,0,0.8)] font-mono text-xs space-y-4">
                  <div className="flex items-center justify-between pb-3 border-b border-white/10 text-slate-400">
                    <span className="text-cyan-400 font-bold">COMPILER_PIPELINE.LOG</span>
                    <span>3.14.0 FINAL</span>
                  </div>
                  <div className="space-y-2 text-slate-300 text-[11px]">
                    <div className="flex justify-between">
                      <span className="text-slate-500">AST_PARSER</span>
                      <span className="text-emerald-400">PASS (1.2ms)</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">TYPE_INFERENCE</span>
                      <span className="text-emerald-400">ZERO_FENCE_VIOLATIONS</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">SSA_IR_LOWERING</span>
                      <span className="text-emerald-400">UNBOXED_REGISTERS (3.8ms)</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-500">LLVM_OPTIMIZER</span>
                      <span className="text-cyan-400">O3_VECTORS_APPLIED</span>
                    </div>
                    <div className="flex justify-between pt-2 border-t border-white/5 font-bold">
                      <span className="text-white">STANDALONE_BINARY</span>
                      <span className="text-cyan-300">2.1 MB (0 DEPS)</span>
                    </div>
                  </div>
                  <div className="p-3 rounded bg-black/60 text-slate-400 text-[11px] leading-relaxed">
                    <span className="text-emerald-400 font-bold">❯ ./app</span><br />
                    Server listening on 0.0.0.0:8080 (0.61ms cold start, RSS 14MB)
                  </div>
                </div>
              </div>
            </div>
          </section>

          {/* Section 1: Lumio Carousel (Cyber Precision Variant) */}
          <LumioCarousel theme={theme} variant="cyber-precision" />

          {/* Section 2: Isometric Surface Stack */}
          <section className="w-full border-t border-cyan-500/10">
            <IsometricSurfaceStack theme={theme} />
          </section>
        </div>
      )}

      {/* =========================================================================
          VARIATION 3: MUJI STUDIO / PURE RESTRAINT (Direct MUJI & Notion Reduction)
          ========================================================================= */}
      {variation === "v3-muji" && (
        <div className="w-full">
          {/* Extremely Sparse Minimal Header */}
          <nav className="w-full max-w-5xl mx-auto px-6 py-10 flex items-center justify-between">
            <span className="font-semibold text-base tracking-tight">pyscriptc</span>
            <div className="flex items-center gap-8 text-xs font-normal text-slate-400">
              <a href="/docs" className="hover:text-white transition-colors">Docs</a>
              <a href="/compat" className="hover:text-white transition-colors">Compat</a>
              <a href="https://github.com/sigilco/pyscriptc" className="hover:text-white transition-colors">GitHub</a>
            </div>
          </nav>

          {/* Ultra-Restrained Headline with Massive Whitespace Rhythm */}
          <section className="w-full max-w-4xl mx-auto px-6 pt-20 pb-28 text-left space-y-10">
            <h1 className="text-4xl sm:text-6xl font-normal tracking-tight text-white leading-[1.12]">
              Python at C speed.<br />
              <span className="text-slate-400">Without rewrites.</span>
            </h1>
            <p className="text-xl sm:text-2xl text-slate-400 font-light max-w-2xl leading-relaxed">
              pyscriptc compiles pure Python workloads into standalone single executables.
              No runtime virtual environments. No Docker layers. Sub-millisecond cold start.
            </p>
            <div className="flex items-center gap-6 pt-4">
              <a
                href="/docs"
                className="px-6 py-3 rounded-full bg-white text-black font-medium text-xs hover:bg-slate-200 transition-all shadow-sm"
              >
                Read the documentation ↗
              </a>
              <a
                href="/compat"
                className="text-xs text-slate-400 hover:text-white transition-colors"
              >
                View compatibility matrix ↗
              </a>
            </div>
          </section>

          {/* Minimal 4-Row Metric Strip */}
          <section className="w-full max-w-4xl mx-auto px-6 py-12 border-t border-white/5">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-8">
              <div>
                <div className="text-2xl font-normal text-white mb-0.5">24x</div>
                <div className="text-xs text-slate-500 font-mono">execution speed</div>
              </div>
              <div>
                <div className="text-2xl font-normal text-white mb-0.5">6x</div>
                <div className="text-xs text-slate-500 font-mono">lower peak memory</div>
              </div>
              <div>
                <div className="text-2xl font-normal text-white mb-0.5">0 ms</div>
                <div className="text-xs text-slate-500 font-mono">cold start</div>
              </div>
              <div>
                <div className="text-2xl font-normal text-white mb-0.5">100%</div>
                <div className="text-xs text-slate-500 font-mono">CPython 3.14 semantics</div>
              </div>
            </div>
          </section>

          {/* Section 1: Lumio Carousel (Quiet Variant) */}
          <section className="w-full border-t border-white/5">
            <LumioCarousel theme={theme} variant="warm-editorial" />
          </section>

          {/* Section 2: Isometric Surface Stack */}
          <section className="w-full border-t border-white/5">
            <IsometricSurfaceStack theme={theme} />
          </section>
        </div>
      )}

      {/* Shared Minimal Footer */}
      <footer className="w-full border-t border-white/10 py-12 px-6 text-center text-xs font-mono text-slate-500">
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-1.5">
            <span>Made with</span>
            <span className="text-rose-500 inline-block animate-pulse">❤️</span>
            <span>from Spain</span>
          </div>
          <div className="flex items-center gap-6">
            <a href="/docs" className="hover:text-white transition-colors">Docs</a>
            <a href="/compat" className="hover:text-white transition-colors">Compat</a>
            <a href="https://github.com/sigilco/pyscriptc" className="hover:text-white transition-colors">GitHub</a>
            <a href="https://github.com/sigilco/pyscriptc/blob/master/LICENSE" className="hover:text-white transition-colors">Apache 2.0</a>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default VariationsStudio;
