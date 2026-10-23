import React, { useState } from "react";
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

  const isDark = theme === "dark";

  // Palette tokens based on variation & theme
  const getPalette = () => {
    if (variation === "v1-kyoto") {
      // Warm Minimal: Off-dark #0c0d12 / Off-white #f7f7f5
      return {
        bg: isDark ? "bg-[#0c0d12]" : "bg-[#f7f7f5]",
        cardBg: isDark ? "bg-[#13151f]" : "bg-[#ffffff]",
        border: isDark ? "border-white/10" : "border-slate-300/80",
        textPrimary: isDark ? "text-white" : "text-[#111111]",
        textSecondary: isDark ? "text-slate-300" : "text-[#555555]",
        textMuted: isDark ? "text-slate-500" : "text-[#888888]",
        accent: "#38bdf8",
      };
    }
    if (variation === "v2-tokyo") {
      // Cyber-Minimal: Deep Obsidian #08090d / Mist #f1f3f7
      return {
        bg: isDark ? "bg-[#08090d]" : "bg-[#f1f3f7]",
        cardBg: isDark ? "bg-[#0f1118]" : "bg-[#ffffff]",
        border: isDark ? "border-cyan-500/20" : "border-slate-300",
        textPrimary: isDark ? "text-white" : "text-[#0a0b0e]",
        textSecondary: isDark ? "text-slate-300" : "text-[#475569]",
        textMuted: isDark ? "text-slate-500" : "text-[#64748b]",
        accent: "#00c2ff",
      };
    }
    // MUJI Pure Restraint: Quiet Charcoal #101114 / Paper #faf8f5
    return {
      bg: isDark ? "bg-[#101114]" : "bg-[#faf8f5]",
      cardBg: isDark ? "bg-[#17181e]" : "bg-[#ffffff]",
      border: isDark ? "border-white/5" : "border-slate-200",
      textPrimary: isDark ? "text-slate-100" : "text-[#1c1b1b]",
      textSecondary: isDark ? "text-slate-400" : "text-[#5e5e5e]",
      textMuted: isDark ? "text-slate-600" : "text-[#949494]",
      accent: "#38bdf8",
    };
  };

  const p = getPalette();

  return (
    <div className={`w-full min-h-screen ${p.bg} ${p.textPrimary} transition-colors duration-500 font-sans antialiased`}>
      {/* Top Floating Studio Controller Toolbar */}
      <div className="sticky top-0 z-50 w-full backdrop-blur-xl bg-black/60 border-b border-white/10 px-4 py-3 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="font-mono text-xs font-bold text-cyan-400 uppercase tracking-widest">
            Design Studio
          </span>
          <span className="text-slate-600">/</span>
          <div className="flex items-center gap-1.5 bg-white/5 p-1 rounded-xl border border-white/10">
            <button
              onClick={() => setVariation("v1-kyoto")}
              className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                variation === "v1-kyoto"
                  ? "bg-cyan-500 text-black font-semibold shadow-sm"
                  : "text-slate-300 hover:text-white"
              }`}
            >
              V1: Kyoto Minimal
            </button>
            <button
              onClick={() => setVariation("v2-tokyo")}
              className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                variation === "v2-tokyo"
                  ? "bg-cyan-500 text-black font-semibold shadow-sm"
                  : "text-slate-300 hover:text-white"
              }`}
            >
              V2: Tokyo Precision
            </button>
            <button
              onClick={() => setVariation("v3-muji")}
              className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                variation === "v3-muji"
                  ? "bg-cyan-500 text-black font-semibold shadow-sm"
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
              onClick={() => setTheme("dark")}
              className={`px-2.5 py-1 rounded-lg text-xs font-mono transition-all ${
                isDark ? "bg-white/20 text-white font-bold" : "text-slate-400 hover:text-white"
              }`}
            >
              🌙 Dark (#0c0d12)
            </button>
            <button
              onClick={() => setTheme("light")}
              className={`px-2.5 py-1 rounded-lg text-xs font-mono transition-all ${
                !isDark ? "bg-white/20 text-white font-bold" : "text-slate-400 hover:text-white"
              }`}
            >
              ☀️ Light (#f7f7f5)
            </button>
          </div>
        </div>
      </div>

      {/* Variation Description Bar */}
      <div className="w-full bg-cyan-950/20 border-b border-cyan-500/10 py-2 px-4 text-center">
        <p className="text-xs font-mono text-cyan-300">
          {variation === "v1-kyoto" && "Variation 1: 'Kyoto Paper & Obsidian' — Warm off-dark canvas, editorial balance, restrained hairlines."}
          {variation === "v2-tokyo" && "Variation 2: 'Tokyo Monolith' — High-precision geometric typography, technical blueprint, cyber-minimal."}
          {variation === "v3-muji" && "Variation 3: 'MUJI Pure Restraint' — Quiet engineering, zero decorative pills, pure typographic spatial hierarchy."}
        </p>
      </div>

      {/* Main Nav (Minimal Japanese Style) */}
      <nav className={`w-full max-w-7xl mx-auto px-6 py-6 flex items-center justify-between`}>
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-tr from-cyan-500 to-sky-300 flex items-center justify-center shadow-[0_0_16px_rgba(0,194,255,0.4)]">
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
            className="px-5 py-2 rounded-full text-xs font-semibold bg-white text-black hover:bg-slate-200 transition-all shadow-sm"
          >
            Get Started ↗
          </a>
        </div>
      </nav>

      {/* HERO SECTION with MESH GRID & BACKDROP BLUR */}
      <section className="relative w-full overflow-hidden flex flex-col items-center justify-center min-h-[85dvh] px-4">
        {/* Hero Mesh Grid (Interactive, No Grid Lines, Slow Organic Cadence) */}
        <HeroMeshGrid
          state="active"
          theme={theme}
          showGridLines={false}
          interactiveHover={true}
          activeCount={0}
          className="!min-h-[85dvh]"
        >
          {/* Protected Content Container with Backdrop Blur to prevent word collision */}
          <div className="relative z-10 max-w-4xl mx-auto px-6 py-12 text-center flex flex-col items-center">
            {/* Soft Protective Backdrop Blur Vignette */}
            <div
              className={`absolute inset-0 -z-10 rounded-3xl backdrop-blur-md transition-colors duration-500 ${
                isDark ? "bg-[#06080d]/65 shadow-[0_0_80px_rgba(6,8,13,0.9)]" : "bg-white/60 shadow-[0_0_80px_rgba(255,255,255,0.9)]"
              }`}
            />

            {/* Subtitle Eyebrow (No pill wrapper) */}
            <p className="text-xs sm:text-sm font-mono uppercase tracking-[0.25em] text-cyan-400 font-semibold mb-6">
              CPython 3.14 Ahead-of-Time Compiler
            </p>

            {/* Main Headline */}
            <h1 className="text-4xl sm:text-6xl lg:text-7xl font-bold tracking-tight leading-[1.08] mb-6">
              Run Python at <span className="bg-gradient-to-r from-cyan-400 via-sky-300 to-white bg-clip-text text-transparent">C Speed</span> without Rewriting a Single Line
            </h1>

            {/* Value / ROI Paragraph */}
            <p className="text-lg sm:text-xl text-slate-300 max-w-2xl font-light leading-relaxed mb-10">
              The native ahead-of-time compiler turning Python workloads into pure standalone single binaries.
              Zero Python interpreter. Zero virtualenv. Instant sub-millisecond cold start.
            </p>

            {/* Clean CTAs (Japanese Minimal Button Architecture) */}
            <div className="flex flex-wrap items-center justify-center gap-4">
              <a
                href="/docs"
                className="group inline-flex items-center gap-3 px-8 py-4 rounded-full bg-cyan-400 text-slate-950 font-semibold text-sm shadow-[0_0_32px_rgba(0,194,255,0.4)] hover:bg-cyan-300 active:scale-[0.98] transition-all duration-300"
              >
                <span>Read the Docs</span>
                <span className="w-6 h-6 rounded-full bg-black/15 flex items-center justify-center text-xs group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform">
                  ↗
                </span>
              </a>

              <a
                href="/compat"
                className="group inline-flex items-center gap-3 px-8 py-4 rounded-full bg-white/5 text-white border border-white/10 hover:border-white/25 hover:bg-white/10 active:scale-[0.98] transition-all duration-300 font-medium text-sm backdrop-blur-md"
              >
                <span>Are We Native Yet?</span>
                <span className="w-6 h-6 rounded-full bg-white/10 flex items-center justify-center text-xs group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform">
                  ↗
                </span>
              </a>
            </div>
          </div>
        </HeroMeshGrid>
      </section>

      {/* ROI METRIC STRIP (4-Stat Restrained Layout) */}
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

      {/* SECTION 1: LUMIO-STYLE TEXT CAROUSEL + CODE SCRATCHPAD */}
      <section className="w-full">
        <LumioCarousel theme={theme} variant={variation === "v2-tokyo" ? "cyber-precision" : "warm-editorial"} />
      </section>

      {/* SECTION 2: THREE SURFACES ISOMETRIC STACK */}
      <section className="w-full border-t border-white/5">
        <IsometricSurfaceStack theme={theme} />
      </section>

      {/* MINIMAL FOOTER */}
      <footer className="w-full border-t border-white/10 py-12 px-6 text-center text-xs font-mono text-slate-500">
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div>pyscriptc :: Ahead-of-time Python compiler for native systems</div>
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
