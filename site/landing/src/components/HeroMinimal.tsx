import React, { useState } from "react";
import { HeroMeshGrid } from "./HeroMeshGrid";

export interface HeroMinimalProps {
  theme?: "dark" | "light";
}

export const HeroMinimal: React.FC<HeroMinimalProps> = ({ theme = "dark" }) => {
  const [copied, setCopied] = useState(false);

  const copyInstall = () => {
    navigator.clipboard.writeText("uv tool install pyscriptc");
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <HeroMeshGrid
      state="active"
      theme={theme}
      activeCount={0}
      showGridLines={false}
      interactiveHover={true}
      className="pt-24 pb-16 sm:pt-32 sm:pb-24"
    >
      {/* Hero Capsule Container with Backdrop Blur */}
      <div className="w-full max-w-4xl mx-auto px-4 sm:px-6 relative z-10 text-center flex flex-col items-center">
        <div className="w-full backdrop-blur-2xl bg-black/40 border border-white/10 rounded-3xl p-8 sm:p-12 md:p-16 shadow-[0_24px_64px_rgba(0,0,0,0.6)]">
          {/* Main Headline */}
          <h1 className="text-4xl sm:text-6xl md:text-7xl font-bold tracking-tight text-white leading-[1.08] mb-6">
            Python at C speed.<br />
            <span className="text-slate-400 font-normal">Without rewrites.</span>
          </h1>

          {/* Subtitle Narrative */}
          <p className="text-lg sm:text-xl md:text-2xl text-slate-300 font-light max-w-2xl mx-auto leading-relaxed mb-8">
            pyscriptc compiles pure Python workloads into standalone single executables.
            No runtime virtual environments. No Docker layers. Sub-millisecond cold start.
          </p>

          {/* Action CTAs & Install Box */}
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-8">
            <a
              href="/docs"
              className="w-full sm:w-auto px-7 py-3.5 rounded-full bg-white text-slate-950 font-semibold text-sm hover:bg-slate-200 transition-all shadow-[0_0_24px_rgba(255,255,255,0.2)]"
            >
              Read the documentation ↗
            </a>
            <a
              href="/compat"
              className="w-full sm:w-auto px-7 py-3.5 rounded-full bg-white/5 border border-white/10 text-white font-medium text-sm hover:bg-white/10 transition-all"
            >
              View compatibility matrix ↗
            </a>
          </div>

          {/* Inline Install Box with Copy Feedback */}
          <div className="inline-flex items-center gap-3 px-4 py-2 rounded-xl bg-black/50 border border-white/10 text-xs font-mono text-slate-400">
            <span className="text-cyan-400">$</span>
            <span className="text-slate-200">uv tool install pyscriptc</span>
            <button
              onClick={copyInstall}
              className="ml-2 px-2 py-0.5 rounded bg-white/10 hover:bg-white/20 text-[11px] text-slate-300 transition-colors"
              title="Copy to clipboard"
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
        </div>
      </div>
    </HeroMeshGrid>
  );
};

export default HeroMinimal;
