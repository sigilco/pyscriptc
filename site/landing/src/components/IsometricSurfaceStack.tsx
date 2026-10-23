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
    headline: "Zero dependencies. Sub-millisecond cold starts. Machine code.",
    description:
      "Your Python source is compiled down to optimized native assembly. No bundled interpreter, no container bloat, no virtualenv. A single self-contained binary that launches in 0.6 milliseconds and runs anywhere.",
    highlights: [
      {
        title: "Standalone Single Binary",
        desc: "Ship a self-contained executable with zero runtime dependencies. Drop it into a distroless container, a bare metal server, or an embedded device.",
      },
      {
        title: "Sub-Millisecond Cold Starts",
        desc: "Eliminate interpreter bootstrap overhead entirely. Execution begins at the first instruction in 0.6ms.",
      },
      {
        title: "Target Every Architecture",
        desc: "One command compiles for Linux x86_64, ARM64 Apple Silicon, Windows, and WebAssembly.",
      },
    ],
    accentColor: "#00c2ff",
    badge: "Bedrock Flagship",
  },
  {
    id: "shared",
    number: "02",
    name: "Native Shared Library",
    subtitle: "Universal C ABI Interop",
    headline: "Call Python at native speed from Rust, Go, C++, or Node.",
    description:
      "Export Python functions as clean, standard C ABI shared objects (.so, .dylib, .dll). Link Python algorithms directly into foreign language runtimes without GIL contention or foreign function interface friction.",
    highlights: [
      {
        title: "Standard C Calling Convention",
        desc: "Expose clean C ABI entry points that integrate into Rust, C++, Go, or iOS/Android toolchains seamlessly.",
      },
      {
        title: "True Multi-Threaded Execution",
        desc: "Compiled algorithms execute concurrently across all available CPU cores without Global Interpreter Lock bottlenecks.",
      },
      {
        title: "Zero PyObject Overhead",
        desc: "Pass native pointers, structs, and primitives directly without marshalling through interpreter object wrappers.",
      },
    ],
    accentColor: "#38bdf8",
    badge: "Optional Interface",
  },
  {
    id: "island",
    number: "03",
    name: "CPython Island Fallback",
    subtitle: "Zero-Risk Migration Safety Net",
    headline: "100% ecosystem compatibility when you need legacy extensions.",
    description:
      "When your code calls deeply coupled C-extensions or dynamic metaprogramming that hasn't migrated yet, pyscriptc isolates those modules into an isolated CPython island. Your compiled core runs at native speed; only the legacy call branches to the island.",
    highlights: [
      {
        title: "Zero Regression Guarantee",
        desc: "Never get blocked by an unmigrated third-party package. The island sandbox catches dynamic idioms automatically.",
      },
      {
        title: "Zero Cost When Unused",
        desc: "If your program stays on the ahead-of-time path, the CPython island is never initialized and consumes 0% CPU or memory.",
      },
      {
        title: "Audit & Drop Path",
        desc: "Clear compiler diagnostics show exactly which calls trigger the island, giving your team a clean checklist to reach 100% native.",
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
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  const selectedTier = SURFACE_TIERS[activeIndex];

  // Auto-looping timer that loops forever
  useEffect(() => {
    timerRef.current = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % SURFACE_TIERS.length);
    }, autoPlayInterval);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [autoPlayInterval]);

  const handleSelect = (idx: number) => {
    setActiveIndex(idx);
    // Reset timer to allow user to view selected layer, then continue looping
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % SURFACE_TIERS.length);
    }, autoPlayInterval);
  };

  return (
    <section className={`w-full max-w-6xl mx-auto px-6 py-20 lg:py-28 ${className}`}>
      {/* Section Header (No chips) */}
      <div className="max-w-3xl mb-14 text-left">
        <h2 className="text-3xl sm:text-4xl lg:text-5xl font-normal tracking-tight text-white leading-[1.12] mb-4">
          Three surfaces. One unified compiler.
        </h2>
        <p className="text-base sm:text-lg text-slate-400 font-light leading-relaxed">
          Target the execution surface your infrastructure requires without rewriting a single line
          of Python. From zero-dependency native binaries to embedded plugins and resilient sandbox fallbacks.
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

              {/* TIER 2: Middle Slab (Native Shared Library - Dotted/Optional) */}
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

          <div className="flex items-center gap-2 mt-4 text-[11px] font-mono text-slate-500">
            <span>Click any tier to inspect</span>
          </div>
        </div>

        {/* Right Column: Together.ai Style Bold Narrative */}
        <div
          key={selectedTier.id}
          className="lg:col-span-7 flex flex-col justify-center text-left transition-all duration-500 animate-fadeIn"
        >
          {/* Surface Tier Name & Badge */}
          <div className="flex items-center gap-3 mb-3">
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
