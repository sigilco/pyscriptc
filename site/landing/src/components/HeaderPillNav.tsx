import React, { useState } from "react";

export interface HeaderPillNavProps {
  theme?: "dark" | "light";
  docsUrl?: string;
  compatUrl?: string;
  githubUrl?: string;
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
      <nav
        className={`pointer-events-auto inline-flex items-center gap-2 sm:gap-4 px-4 py-2 sm:px-5 sm:py-2.5 rounded-full border transition-all duration-300 shadow-[0_12px_36px_rgba(0,0,0,0.55)] ${
          isDark
            ? "bg-[#101116]/90 border-white/10 text-slate-200 backdrop-blur-xl"
            : "bg-[#fbf9f5]/90 border-black/10 text-slate-800 backdrop-blur-xl shadow-lg"
        }`}
        aria-label="Main Navigation"
      >
        {/* Brand Group (Logomark + Name Combo) */}
        <a
          href="/"
          className="flex items-center gap-2 py-0.5 rounded-full hover:opacity-80 transition-opacity"
        >
          <div className="w-5 h-5 rounded-full bg-gradient-to-tr from-cyan-400 to-sky-300 flex items-center justify-center shadow-[0_0_10px_rgba(56,189,248,0.4)]">
            <span className="font-mono font-bold text-[9px] text-slate-950">py</span>
          </div>
          <span className="font-semibold text-sm tracking-tight text-white">pyscriptc</span>
        </a>

        {/* Separator */}
        <span className="text-white/15 hidden md:inline">/</span>

        {/* Desktop Links */}
        <div className="hidden md:flex items-center gap-2 text-xs font-medium text-slate-400">
          <a
            href={docsUrl}
            className="px-2.5 py-1 rounded-full hover:text-white transition-colors"
          >
            Docs
          </a>
          <a
            href={compatUrl}
            className="px-2.5 py-1 rounded-full hover:text-white transition-colors"
          >
            Are We Native Yet?
          </a>
          <a
            href={githubUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="px-2.5 py-1 rounded-full hover:text-white transition-colors"
          >
            GitHub ↗
          </a>
        </div>

        {/* Mobile Breadcrumb Menu Button */}
        <div className="md:hidden flex items-center">
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="flex items-center justify-center w-7 h-7 rounded-full bg-white/10 hover:bg-white/15 text-slate-200 transition-colors"
            aria-label="Toggle Navigation Menu"
          >
            <span className="font-mono text-sm leading-none font-bold">···</span>
          </button>
        </div>
      </nav>

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
              Are We Native Yet? (Compatibility Matrix)
            </a>
            <a
              href={githubUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => setMobileMenuOpen(false)}
              className="px-3 py-2 rounded-lg hover:bg-white/10 transition-colors"
            >
              GitHub ↗
            </a>
          </div>
        </div>
      )}
    </header>
  );
};

export default HeaderPillNav;
