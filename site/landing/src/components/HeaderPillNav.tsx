import React, { useState } from "react";

export interface HeaderPillNavProps {
  theme?: "dark" | "light";
  docsUrl?: string;
  compatUrl?: string;
  githubUrl?: string;
  playgroundUrl?: string;
}

export const HeaderPillNav: React.FC<HeaderPillNavProps> = ({
  theme = "dark",
  docsUrl = "/docs",
  compatUrl = "/compat",
  githubUrl = "https://github.com/sigilco/pyscriptc",
}) => {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const isDark = theme === "dark";

  return (
    <header className="fixed top-5 left-0 right-0 z-50 flex justify-center px-4 pointer-events-none">
      {/* Floating Pill Capsule Bar */}
      <div
        className={`pointer-events-auto inline-flex items-center gap-1.5 sm:gap-2 px-3 py-2 sm:px-4 sm:py-2.5 rounded-full border transition-all duration-300 shadow-[0_12px_36px_rgba(0,0,0,0.55)] ${
          isDark
            ? "bg-[#101116]/90 border-white/10 text-slate-200 backdrop-blur-xl"
            : "bg-[#fbf9f5]/90 border-black/10 text-slate-800 backdrop-blur-xl shadow-lg"
        }`}
      >
        {/* Brand Group (Logomark + Name Combo) */}
        <a
          href="/"
          className="flex items-center gap-2 pl-1 pr-2.5 py-1 rounded-full hover:opacity-85 transition-opacity"
        >
          <div className="w-6 h-6 rounded-full bg-gradient-to-tr from-cyan-400 to-sky-300 flex items-center justify-center shadow-[0_0_12px_rgba(56,189,248,0.5)]">
            <span className="font-mono font-bold text-[10px] text-slate-950">py</span>
          </div>
          <span className="font-semibold text-sm tracking-tight text-white">pyscriptc</span>
        </a>

        {/* Desktop Links (Inlined inside the pill) */}
        <div className="hidden md:flex items-center gap-1 text-xs font-medium text-slate-400">
          <a
            href={docsUrl}
            className="px-3 py-1.5 rounded-full hover:text-white hover:bg-white/5 transition-colors"
          >
            Docs
          </a>
          <a
            href={compatUrl}
            className="px-3 py-1.5 rounded-full hover:text-white hover:bg-white/5 transition-colors"
          >
            Are We Native Yet?
          </a>
          <a
            href={githubUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1.5 rounded-full hover:text-white hover:bg-white/5 transition-colors"
          >
            GitHub ↗
          </a>
        </div>

        {/* Action Button: Get Started */}
        <div className="flex items-center gap-1.5 pl-1">
          <a
            href={docsUrl}
            className="px-4 py-1.5 rounded-full text-xs font-semibold bg-white text-slate-950 hover:bg-slate-200 transition-all shadow-sm"
          >
            Get Started ↗
          </a>

          {/* Mobile Breadcrumb Menu Button */}
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="md:hidden flex items-center justify-center w-8 h-8 rounded-full bg-white/10 hover:bg-white/15 text-slate-200 transition-colors"
            aria-label="Toggle Navigation Menu"
          >
            <span className="font-mono text-sm leading-none font-bold">···</span>
          </button>
        </div>
      </div>

      {/* Mobile Breadcrumb Dropdown Drawer */}
      {mobileMenuOpen && (
        <div
          className={`pointer-events-auto absolute top-16 left-4 right-4 max-w-xs mx-auto p-3 rounded-2xl border transition-all duration-300 shadow-2xl backdrop-blur-2xl md:hidden ${
            isDark
              ? "bg-[#101116]/95 border-white/15 text-slate-200"
              : "bg-[#fbf9f5]/95 border-black/15 text-slate-800"
          }`}
        >
          <div className="flex flex-col space-y-1 text-xs font-medium">
            <a
              href={docsUrl}
              onClick={() => setMobileMenuOpen(false)}
              className="px-3 py-2 rounded-lg hover:bg-white/10 transition-colors"
            >
              Documentation
            </a>
            <a
              href={compatUrl}
              onClick={() => setMobileMenuOpen(false)}
              className="px-3 py-2 rounded-lg hover:bg-white/10 transition-colors"
            >
              Are We Native Yet? (Compatibility Tracker)
            </a>
            <a
              href={githubUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setMobileMenuOpen(false)}
              className="px-3 py-2 rounded-lg hover:bg-white/10 transition-colors"
            >
              GitHub Repository ↗
            </a>
          </div>
        </div>
      )}
    </header>
  );
};

export default HeaderPillNav;
