import React from "react";
import { useSelect, useValue } from "react-cosmos/client";
import { HeroMeshGrid } from "./HeroMeshGrid";

export default {
  "Interactive Studio": () => {
    const [state] = useSelect("State", {
      options: ["active", "disabled"],
      defaultValue: "active",
    });
    const [theme] = useSelect("Theme", {
      options: ["dark", "light"],
      defaultValue: "dark",
    });
    const [showGridLines] = useValue("Show Grid Lines", { defaultValue: true });
    const [interactiveHover] = useValue("Interactive Hover", { defaultValue: true });
    const [activeCount] = useValue("Active Pulse Count", { defaultValue: 18 });

    return (
      <HeroMeshGrid
        state={state as "active" | "disabled"}
        theme={theme as "dark" | "light"}
        showGridLines={showGridLines}
        interactiveHover={interactiveHover}
        activeCount={activeCount}
      />
    );
  },

  "State 1: All Disabled (Dark Stealth)": () => (
    <HeroMeshGrid state="disabled" theme="dark" />
  ),

  "State 1: All Disabled (Light Minimal)": () => (
    <HeroMeshGrid state="disabled" theme="light" />
  ),

  "State 2: Active Constellation (Dark Mode)": () => (
    <HeroMeshGrid state="active" theme="dark" activeCount={18} />
  ),

  "State 2: Active Constellation (Light Mode)": () => (
    <HeroMeshGrid state="active" theme="light" activeCount={18} />
  ),

  "Hero Context Overlay Preview": () => (
    <HeroMeshGrid state="active" theme="dark" activeCount={14}>
      <div className="max-w-4xl mx-auto px-6 text-center space-y-8 select-text">
        <div className="inline-flex items-center gap-2.5 px-3.5 py-1.5 rounded-full text-[11px] font-mono uppercase tracking-[0.2em] bg-cyan-500/10 text-cyan-300 border border-cyan-500/25 shadow-[0_0_20px_rgba(0,194,255,0.15)] backdrop-blur-md">
          <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
          <span>Zero Dialect Drift</span>
          <span className="text-cyan-500/60">✦</span>
          <span>CPython 3.14 Semantics</span>
        </div>

        <h1 className="text-4xl sm:text-6xl lg:text-7xl font-bold tracking-tight text-white leading-[1.08]">
          Run Python at <span className="bg-gradient-to-r from-cyan-400 via-sky-300 to-white bg-clip-text text-transparent">C Speed</span> without Rewriting a Single Line
        </h1>

        <p className="text-lg sm:text-xl text-slate-300 max-w-2xl mx-auto font-light leading-relaxed">
          The ahead-of-time compiler turning Python workloads into pure native single binaries,
          slashing cloud infrastructure bills and delivering sub-millisecond execution.
        </p>

        <div className="flex flex-wrap items-center justify-center gap-4 pt-4">
          <button className="group relative inline-flex items-center gap-3 px-7 py-3.5 rounded-full bg-cyan-400 text-slate-950 font-semibold text-sm shadow-[0_0_28px_rgba(0,194,255,0.45)] hover:bg-cyan-300 active:scale-[0.98] transition-all duration-300">
            <span>Read the Docs</span>
            <span className="w-6 h-6 rounded-full bg-slate-950/15 flex items-center justify-center text-xs group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform">
              ↗
            </span>
          </button>
          <button className="group relative inline-flex items-center gap-3 px-7 py-3.5 rounded-full bg-white/5 text-white border border-white/10 hover:border-white/20 hover:bg-white/10 active:scale-[0.98] transition-all duration-300 font-medium text-sm backdrop-blur-md">
            <span>Are We Native Yet?</span>
            <span className="w-6 h-6 rounded-full bg-white/10 flex items-center justify-center text-xs group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform">
              ↗
            </span>
          </button>
        </div>
      </div>
    </HeroMeshGrid>
  ),
};
