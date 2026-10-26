import React, { useState, useEffect, useRef } from "react";

export interface SurfaceTier {
  id: "native" | "shared" | "island";
  number: string;
  name: string;
  subtitle: string;
  headline: string;
  description: string;
  highlights: { title: string; desc: string }[];
  accentColor: string;
  badge: string;
}

export const SURFACE_TIERS: SurfaceTier[] = [
  {
    id: "native",
    number: "01",
    name: "Pure Native Binary",
    subtitle: "The Flagship Bedrock",
    headline: "Zero dependencies. Machine code. One self-contained file.",
    description:
      "Your Python source is compiled down to optimized native assembly. No bundled interpreter, no container bloat, no virtualenv. A single self-contained binary (about half a megabyte) for programs inside the supported subset.",
    highlights: [
      {
        title: "Standalone Single Binary",
        desc: "Ship a self-contained executable with zero runtime dependencies. Drop it into a distroless container, a bare metal server, or an embedded device.",
      },
      {
        title: "Measured Kernel Speedup",
        desc: "22.8x geometric-mean speedup on compute kernels and 5.3x lower peak RSS than CPython 3.14.9 on Linux x86_64.",
      },
      {
        title: "Target Every Real Architecture",
        desc: "One command compiles for Linux x86_64 and aarch64 (glibc and musl), plus aarch64 and x86_64 macOS.",
      },
    ],
    accentColor: "#00c2ff",
    badge: "Bedrock Flagship",
  },
  {
    id: "shared",
    number: "02",
    name: "Python Callable from C",
    subtitle: "Shared Libraries & Headers",
    headline: "Export typed Python as a shared library with a C surface.",
    description:
      "--emit binding compiles a module into a shared library and ships a ctypes loader, so CPython imports your typed functions without Python.h or an extension build. --emit lib produces a static library with pyscriptc.h for embedding.",
    highlights: [
      {
        title: "ctypes Loader, No Build Matrix",
        desc: "The generated binding module loads the shared library with ctypes.CDLL and exposes typed functions directly to CPython, with no per-version ABI matrix.",
      },
      {
        title: "Static Library & Headers",
        desc: "--emit lib emits libpyscriptc.a, pyscriptc.h and a module.modulemap for mobile and embedded targets.",
      },
      {
        title: "Foreign Runners on the Roadmap",
        desc: "Calling compiled Python from Rust, Go or C++ binaries is a planned next step, not shipped today.",
      },
    ],
    accentColor: "#38bdf8",
    badge: "Shipped",
  },
  {
    id: "island",
    number: "03",
    name: "CPython Island Fallback",
    subtitle: "Opt-in Migration Safety Net",
    headline: "Falls back to the host CPython runtime when a call cannot be compiled.",
    description:
      "When your code calls deeply coupled C-extensions or dynamic metaprogramming that has not migrated yet, you can opt in to an island fallback: the compiled core runs at native speed and only the uncompiled call branches out to the host CPython runtime.",
    highlights: [
      {
        title: "Opt-In, Not Automatic",
        desc: "The island is opt-in via the fallback tier: --fallback=cpython routes the definitions that cannot compile to an embedded host CPython.",
      },
      {
        title: "Measured Island Floor",
        desc: "The island floor is measured in the same benchmark suite as native: island-routed programs currently run about 2x faster than CPython.",
      },
      {
        title: "Audit & Drop Path",
        desc: "Clear compiler diagnostics show exactly which calls trigger the island, giving your team a checklist to reach a fully native build.",
      },
    ],
    accentColor: "#fbbf24",
    badge: "Conditional Safety Net",
  },
];

export interface IsometricSurfaceStackProps {
  theme?: "dark" | "light";
  className?: string;
  autoPlayInterval?: number;
}

export const IsometricSurfaceStack: React.FC<IsometricSurfaceStackProps> = ({
  theme = "dark",
  className = "",
  autoPlayInterval = 7500,
}) => {
  const [activeIndex, setActiveIndex] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const [animKey, setAnimKey] = useState(0);

  const selectedTier = SURFACE_TIERS[activeIndex];

  const handleSelect = (idx: number) => {
    setActiveIndex(idx);
    setAnimKey((prev) => prev + 1);
  };

  // Auto-looping timer with synchronized countdown
  useEffect(() => {
    if (isPaused) return;

    const interval = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % SURFACE_TIERS.length);
      setAnimKey((prev) => prev + 1);
    }, autoPlayInterval);

    return () => clearInterval(interval);
  }, [isPaused, autoPlayInterval, activeIndex]);

  return (
    <section
      className={`w-full max-w-6xl mx-auto px-6 py-20 lg:py-28 ${className}`}
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => {
        setIsPaused(false);
        setAnimKey((prev) => prev + 1);
      }}
    >
      {/* Section Header (No chips) */}
      <div className="max-w-3xl mb-14 text-left">
        <h2 className="text-3xl sm:text-4xl lg:text-5xl font-normal tracking-tight text-white leading-[1.12] mb-4">
          Two surfaces today. One unified compiler.
        </h2>
        <p className="text-base sm:text-lg text-slate-400 font-light leading-relaxed">
          Target the execution surface your infrastructure requires without rewriting a single line
          of Python. Today that means zero-dependency native binaries, shared libraries with a C surface, and an opt-in island fallback.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-10 lg:gap-16 items-center">
        {/* Left Column: Only the Isometric Architectural Graphic is Selectable */}
        <div className="lg:col-span-5 flex flex-col items-center justify-center p-8 sm:p-12 rounded-2xl bg-[#090b10] border border-white/5 relative overflow-hidden select-none">
          {/* Subtle Dynamic Radial Glow */}
          <div
            className="pointer-events-none absolute inset-0 opacity-30 transition-all duration-700"
            style={{
              background: `radial-gradient(circle at 50% ${
                activeIndex === 0 ? "70%" : activeIndex === 1 ? "50%" : "30%"
              }, ${selectedTier.accentColor}25, transparent 65%)`,
            }}
          />

          {/* Isometric SVG Illustration */}
          <div className="relative w-full max-w-[340px] aspect-square flex items-center justify-center">
            <svg
              viewBox="0 0 400 440"
              className="w-full h-full drop-shadow-[0_25px_50px_rgba(0,0,0,0.85)] cursor-pointer"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              {/* TIER 3: Top Slab (CPython Island - Dotted/Conditional) */}
              <g
                onClick={() => handleSelect(2)}
                className="transition-all duration-300 group"
                style={{
                  transform: activeIndex === 2 ? "translateY(-14px)" : "translateY(0)",
                }}
              >
                {/* Left Face */}
                <path
                  d="M130 115 L200 155 L200 195 L130 155 Z"
                  fill="#0c101d"
                  stroke={activeIndex === 2 ? "#fbbf24" : "rgba(255,255,255,0.2)"}
                  strokeDasharray="4 4"
                  strokeWidth="2"
                />
                {/* Right Face */}
                <path
                  d="M200 155 L270 115 L270 155 L200 195 Z"
                  fill="#151a2d"
                  stroke={activeIndex === 2 ? "#fbbf24" : "rgba(255,255,255,0.2)"}
                  strokeDasharray="4 4"
                  strokeWidth="2"
                />
                {/* Top Face */}
                <path
                  d="M200 75 L270 115 L200 155 L130 115 Z"
                  fill={activeIndex === 2 ? "#1f2214" : "#121624"}
                  stroke={activeIndex === 2 ? "#fbbf24" : "rgba(255,255,255,0.3)"}
                  strokeDasharray="4 4"
                  strokeWidth="2.5"
                />
                <circle cx="170" cy="115" r="2" fill="#fbbf24" opacity={activeIndex === 2 ? 0.95 : 0.35} />
                <circle cx="200" cy="98" r="2" fill="#fbbf24" opacity={activeIndex === 2 ? 0.95 : 0.35} />
                <circle cx="200" cy="132" r="2" fill="#fbbf24" opacity={activeIndex === 2 ? 0.95 : 0.35} />
                <circle cx="230" cy="115" r="2" fill="#fbbf24" opacity={activeIndex === 2 ? 0.95 : 0.35} />
              </g>

              {/* TIER 2: Middle Slab (C-ABI Shared Library - Planned / Dotted) */}
              <g
                onClick={() => handleSelect(1)}
                className="transition-all duration-300 group"
                style={{
                  transform: activeIndex === 1 ? "translateY(-10px)" : "translateY(0)",
                }}
              >
                {/* Left Face */}
                <path
                  d="M130 205 L200 245 L200 285 L130 245 Z"
                  fill="#0c101d"
                  stroke={activeIndex === 1 ? "#38bdf8" : "rgba(255,255,255,0.2)"}
                  strokeDasharray="4 4"
                  strokeWidth="2"
                />
                {/* Right Face */}
                <path
                  d="M200 245 L270 205 L270 245 L200 285 Z"
                  fill="#151a2d"
                  stroke={activeIndex === 1 ? "#38bdf8" : "rgba(255,255,255,0.2)"}
                  strokeDasharray="4 4"
                  strokeWidth="2"
                />
                {/* Top Face */}
                <path
                  d="M200 165 L270 205 L200 245 L130 205 Z"
                  fill={activeIndex === 1 ? "#102336" : "#121624"}
                  stroke={activeIndex === 1 ? "#38bdf8" : "rgba(255,255,255,0.3)"}
                  strokeDasharray="4 4"
                  strokeWidth="2.5"
                />
                <circle cx="170" cy="205" r="2" fill="#38bdf8" opacity={activeIndex === 1 ? 0.95 : 0.35} />
                <circle cx="200" cy="188" r="2" fill="#38bdf8" opacity={activeIndex === 1 ? 0.95 : 0.35} />
                <circle cx="200" cy="222" r="2" fill="#38bdf8" opacity={activeIndex === 1 ? 0.95 : 0.35} />
                <circle cx="230" cy="205" r="2" fill="#38bdf8" opacity={activeIndex === 1 ? 0.95 : 0.35} />
              </g>

              {/* TIER 1: Bottom Slab (Pure Native Binary - Solid Bedrock) */}
              <g
                onClick={() => handleSelect(0)}
                className="transition-all duration-300 group"
                style={{
                  transform: activeIndex === 0 ? "translateY(-6px)" : "translateY(0)",
                }}
              >
                {/* Left Face */}
                <path
                  d="M130 295 L200 335 L200 375 L130 335 Z"
                  fill="#0a0d18"
                  stroke={activeIndex === 0 ? "#00c2ff" : "rgba(255,255,255,0.35)"}
                  strokeWidth={activeIndex === 0 ? "2.5" : "2"}
                />
                {/* Right Face */}
                <path
                  d="M200 335 L270 295 L270 335 L200 375 Z"
                  fill="#121829"
                  stroke={activeIndex === 0 ? "#00c2ff" : "rgba(255,255,255,0.35)"}
                  strokeWidth={activeIndex === 0 ? "2.5" : "2"}
                />
                {/* Top Face */}
                <path
                  d="M200 255 L270 295 L200 335 L130 295 Z"
                  fill={activeIndex === 0 ? "#122a44" : "#151b2e"}
                  stroke={activeIndex === 0 ? "#00c2ff" : "rgba(255,255,255,0.4)"}
                  strokeWidth={activeIndex === 0 ? "3" : "2"}
                />
                <circle cx="170" cy="295" r="2.5" fill="#00c2ff" opacity={activeIndex === 0 ? 1 : 0.6} />
                <circle cx="200" cy="278" r="2.5" fill="#00c2ff" opacity={activeIndex === 0 ? 1 : 0.6} />
                <circle cx="200" cy="312" r="2.5" fill="#00c2ff" opacity={activeIndex === 0 ? 1 : 0.6} />
                <circle cx="230" cy="295" r="2.5" fill="#00c2ff" opacity={activeIndex === 0 ? 1 : 0.6} />
              </g>
            </svg>
          </div>

          {/* Upstream-style Dots/Pills indicator (LTR countdown) */}
          <div
            className="flex items-center justify-center gap-2 mt-5"
            role="group"
            aria-label="Surface tier navigation"
          >
            {SURFACE_TIERS.map((tier, idx) => {
              const isActive = activeIndex === idx;
              return (
                <button
                  key={tier.id}
                  type="button"
                  onClick={() => handleSelect(idx)}
                  aria-label={`Tier ${tier.number}: ${tier.name}`}
                  aria-pressed={isActive}
                  className={`relative overflow-hidden rounded-full transition-all duration-300 h-1.5 cursor-pointer border-0 p-0 ${
                    isActive ? "w-8 bg-white/20" : "w-2 bg-white/10 hover:bg-white/25"
                  }`}
                >
                  {isActive && (
                    <span
                      key={`tier-fill-${activeIndex}-${animKey}`}
                      className="absolute inset-0 rounded-full origin-left"
                      style={{
                        backgroundColor: tier.accentColor,
                        animation: `progress-ltr ${autoPlayInterval}ms linear forwards`,
                        animationPlayState: isPaused ? "paused" : "running",
                      }}
                    />
                  )}
                </button>
              );
            })}
          </div>

          <div className="flex items-center gap-2 mt-2.5 text-[11px] font-mono text-slate-500">
            <span>Click any tier or dot to inspect</span>
          </div>
        </div>

        {/* Right Column: Bold Narrative */}
        <div
          key={selectedTier.id}
          className="lg:col-span-7 flex flex-col justify-center text-left transition-all duration-500 animate-fadeIn"
        >
          {/* Surface Tier Name & Badge */}
          <div className="flex items-center gap-3 mb-4">
            <span className="font-mono text-xs text-slate-400 font-semibold">
              {selectedTier.number}
            </span>
            <span className="text-white/20">/</span>
            <span
              className="text-xs font-mono font-medium"
              style={{ color: selectedTier.accentColor }}
            >
              {selectedTier.name}
            </span>
          </div>

          {/* Prominent Headline */}
          <h3 className="text-2xl sm:text-3xl lg:text-4xl font-normal text-white tracking-tight leading-[1.2] mb-4">
            {selectedTier.headline}
          </h3>

          {/* Narrative Summary */}
          <p className="text-slate-300 text-base sm:text-lg leading-relaxed font-light mb-8">
            {selectedTier.description}
          </p>

          {/* Value Differentiator Points */}
          <div className="space-y-4 border-t border-white/10 pt-6">
            {selectedTier.highlights.map((item, idx) => (
              <div key={idx} className="flex items-start gap-3">
                <div
                  className="w-5 h-5 rounded-md flex items-center justify-center shrink-0 mt-0.5"
                  style={{
                    backgroundColor: `${selectedTier.accentColor}15`,
                    color: selectedTier.accentColor,
                  }}
                >
                  <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-white mb-0.5">
                    {item.title}
                  </h4>
                  <p className="text-xs sm:text-sm text-slate-400 font-light leading-relaxed">
                    {item.desc}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

export default IsometricSurfaceStack;
