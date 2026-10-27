import React from "react";
import { HeroMeshGrid } from "./HeroMeshGrid";

export interface HeroMinimalProps {
  theme?: "dark" | "light";
  docsUrl?: string;
  compatUrl?: string;
}

export const HeroMinimal: React.FC<HeroMinimalProps> = ({
  theme = "dark",
  docsUrl = "/docs",
  compatUrl = "/compat",
}) => {
  return (
    <HeroMeshGrid
      state="active"
      theme={theme}
      activeCount={8}
      showGridLines={false}
      interactiveHover={true}
      copyVignette={true}
      className="relative w-full"
    >
      {/* Strictly left-aligned Hero matching Screenshot 2026-10-23 at 22.42.22 with pure CSS blur */}
      <div className="w-full max-w-5xl mx-auto px-6 pt-32 pb-24 sm:pt-40 sm:pb-32 text-left relative z-10 pointer-events-none">
        {/* Lens layer sits behind the copy; purely visual, no layout impact */}
        <div className="hero-copy-lens pointer-events-none absolute inset-0" aria-hidden="true" />
        <div className="relative max-w-3xl pointer-events-auto">
          <h1 className="text-4xl sm:text-6xl lg:text-7xl font-normal tracking-tight text-white leading-[1.08] mb-8">
            Python at C speed.<br />
            <span className="text-slate-400">Without rewrites.</span>
          </h1>

          <p className="text-xl sm:text-2xl text-slate-300 font-light max-w-2xl leading-relaxed mb-10">
            pyscriptc compiles pure Python workloads into standalone single executables.
            No runtime virtual environments. No Docker layers.
          </p>

          <div className="flex flex-wrap items-center gap-6">
            <a
              href={docsUrl}
              className="px-6 py-3 rounded-full bg-white text-black font-medium text-xs hover:bg-slate-200 transition-all shadow-sm"
            >
              Read the documentation ↗
            </a>
            <a
              href={compatUrl}
              className="text-xs font-normal text-slate-400 hover:text-white transition-colors"
            >
              View compatibility matrix ↗
            </a>
          </div>
        </div>
      </div>
    </HeroMeshGrid>
  );
};

export default HeroMinimal;
