import React, { useState } from "react";

export interface SurfaceTier {
  id: "native" | "shared" | "island";
  name: string;
  badge: string;
  isOptional?: boolean;
  isConditional?: boolean;
  status: "Solid Bedrock" | "Optional Interface" | "Conditional Sandbox";
  description: string;
  flag: string;
  targets: string[];
  specs: { label: string; value: string }[];
}

export const SURFACE_TIERS: SurfaceTier[] = [
  {
    id: "island",
    name: "CPython Island Fallback",
    badge: "Conditional Tier",
    isConditional: true,
    status: "Conditional Sandbox",
    description:
      "Isolated fallback tier for legacy code or complex native C-extensions. Sandboxed via CPython ABI island so only uncompiled modules run in the fallback.",
    flag: "--fallback=cpython (opt-in)",
    targets: ["Legacy C-Extensions", "Complex Dynamic Metaprogramming"],
    specs: [
      { label: "Execution", value: "Isolated Boxed Island" },
      { label: "Overhead", value: "0% when uncalled" },
      { label: "Drop Flag", value: "Fully removable tier" },
    ],
  },
  {
    id: "shared",
    name: "Native Shared Library",
    badge: "Optional Interface",
    isOptional: true,
    status: "Optional Interface",
    description:
      "Compile Python business logic into a standard C ABI shared library (.so, .dylib, .dll) callable from Rust, Go, C++, Node.js, or iOS/Android.",
    flag: "--emit=shared-lib",
    targets: ["Embedded Systems", "Cross-Language Host", "Plugin APIs"],
    specs: [
      { label: "ABI Format", value: "Standard C ABI (.so / .dylib)" },
      { label: "Bindings", value: "Zero PyObject wrappers" },
      { label: "Thread Safety", value: "True multi-threading without GIL" },
    ],
  },
  {
    id: "native",
    name: "Pure Native Binary",
    badge: "Bedrock Tier",
    status: "Solid Bedrock",
    description:
      "100% standalone, zero-dependency executable. Contains your compiled Python logic, trimmed runtime runtime, and machine code in a single compact file.",
    flag: "--target=native -O3",
    targets: ["Linux (x86_64, aarch64)", "macOS (Apple Silicon, Intel)", "Windows", "Web WASM"],
    specs: [
      { label: "Dependencies", value: "0 external libraries or CPython" },
      { label: "Cold Start", value: "0.0ms instant execution" },
      { label: "Binary Size", value: "From 1.8 MB single file" },
    ],
  },
];

export interface IsometricSurfaceStackProps {
  theme?: "dark" | "light";
  className?: string;
}

export const IsometricSurfaceStack: React.FC<IsometricSurfaceStackProps> = ({
  theme = "dark",
  className = "",
}) => {
  const [selectedTierId, setSelectedTierId] = useState<"native" | "shared" | "island">("native");
  const selectedTier = SURFACE_TIERS.find((t) => t.id === selectedTierId) ?? SURFACE_TIERS[2];

  const isDark = theme === "dark";

  return (
    <div className={`w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20 lg:py-28 ${className}`}>
      {/* Section Header */}
      <div className="max-w-3xl mb-16 text-left">
        <h2 className="text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-white leading-tight mb-4">
          Three surfaces. One unified compiler.
        </h2>
        <p className="text-base sm:text-lg text-slate-400 font-light leading-relaxed">
          From self-contained microservices to embedded plugins and resilient legacy fallbacks,
          pyscriptc targets three distinct execution surfaces from the exact same source code.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 lg:gap-16 items-center">
        {/* Left Column: Isometric Stack Architectural Graphic */}
        <div className="lg:col-span-6 flex flex-col items-center justify-center p-6 sm:p-10 rounded-3xl bg-[#090b10] border border-white/5 relative overflow-hidden">
          {/* Subtle Ambient Radial Glow */}
          <div
            className="pointer-events-none absolute inset-0 opacity-40 transition-opacity duration-700"
            style={{
              background:
                selectedTierId === "native"
                  ? "radial-gradient(circle at 50% 65%, rgba(0, 194, 255, 0.18), transparent 60%)"
                  : selectedTierId === "shared"
                  ? "radial-gradient(circle at 50% 50%, rgba(56, 189, 248, 0.18), transparent 60%)"
                  : "radial-gradient(circle at 50% 35%, rgba(251, 191, 36, 0.18), transparent 60%)",
            }}
          />

          {/* Isometric SVG Illustration */}
          <div className="relative w-full max-w-[380px] aspect-square flex items-center justify-center select-none">
            <svg
              viewBox="0 0 400 440"
              className="w-full h-full drop-shadow-[0_20px_40px_rgba(0,0,0,0.8)]"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              {/* TIER 3: Top Slab (CPython Island - Dotted/Conditional) */}
              <g
                onClick={() => setSelectedTierId("island")}
                className="cursor-pointer transition-all duration-300"
                style={{
                  transform: selectedTierId === "island" ? "translateY(-12px)" : "translateY(0)",
                }}
              >
                {/* Left Shadow Face */}
                <path
                  d="M130 115 L200 155 L200 195 L130 155 Z"
                  fill="#0c101d"
                  stroke={selectedTierId === "island" ? "#fbbf24" : "rgba(255,255,255,0.2)"}
                  strokeDasharray="4 4"
                  strokeWidth="2"
                />
                {/* Right Highlight Face */}
                <path
                  d="M200 155 L270 115 L270 155 L200 195 Z"
                  fill="#151a2d"
                  stroke={selectedTierId === "island" ? "#fbbf24" : "rgba(255,255,255,0.2)"}
                  strokeDasharray="4 4"
                  strokeWidth="2"
                />
                {/* Top Matrix Face */}
                <path
                  d="M200 75 L270 115 L200 155 L130 115 Z"
                  fill={selectedTierId === "island" ? "#1a2238" : "#121624"}
                  stroke={selectedTierId === "island" ? "#fbbf24" : "rgba(255,255,255,0.3)"}
                  strokeDasharray="4 4"
                  strokeWidth="2.5"
                />
                {/* Matrix Dots */}
                <circle cx="170" cy="115" r="2" fill="#fbbf24" opacity={selectedTierId === "island" ? 0.9 : 0.4} />
                <circle cx="200" cy="98" r="2" fill="#fbbf24" opacity={selectedTierId === "island" ? 0.9 : 0.4} />
                <circle cx="200" cy="132" r="2" fill="#fbbf24" opacity={selectedTierId === "island" ? 0.9 : 0.4} />
                <circle cx="230" cy="115" r="2" fill="#fbbf24" opacity={selectedTierId === "island" ? 0.9 : 0.4} />
                <circle cx="185" cy="107" r="1.5" fill="#ffffff" opacity={0.5} />
                <circle cx="215" cy="107" r="1.5" fill="#ffffff" opacity={0.5} />
                <circle cx="185" cy="123" r="1.5" fill="#ffffff" opacity={0.5} />
                <circle cx="215" cy="123" r="1.5" fill="#ffffff" opacity={0.5} />
              </g>

              {/* TIER 2: Middle Slab (Native Shared Library - Dotted/Optional) */}
              <g
                onClick={() => setSelectedTierId("shared")}
                className="cursor-pointer transition-all duration-300"
                style={{
                  transform: selectedTierId === "shared" ? "translateY(-8px)" : "translateY(0)",
                }}
              >
                {/* Left Shadow Face */}
                <path
                  d="M130 205 L200 245 L200 285 L130 245 Z"
                  fill="#0c101d"
                  stroke={selectedTierId === "shared" ? "#38bdf8" : "rgba(255,255,255,0.2)"}
                  strokeDasharray="4 4"
                  strokeWidth="2"
                />
                {/* Right Highlight Face */}
                <path
                  d="M200 245 L270 205 L270 245 L200 285 Z"
                  fill="#151a2d"
                  stroke={selectedTierId === "shared" ? "#38bdf8" : "rgba(255,255,255,0.2)"}
                  strokeDasharray="4 4"
                  strokeWidth="2"
                />
                {/* Top Matrix Face */}
                <path
                  d="M200 165 L270 205 L200 245 L130 205 Z"
                  fill={selectedTierId === "shared" ? "#1a2238" : "#121624"}
                  stroke={selectedTierId === "shared" ? "#38bdf8" : "rgba(255,255,255,0.3)"}
                  strokeDasharray="4 4"
                  strokeWidth="2.5"
                />
                {/* Matrix Dots */}
                <circle cx="170" cy="205" r="2" fill="#38bdf8" opacity={selectedTierId === "shared" ? 0.9 : 0.4} />
                <circle cx="200" cy="188" r="2" fill="#38bdf8" opacity={selectedTierId === "shared" ? 0.9 : 0.4} />
                <circle cx="200" cy="222" r="2" fill="#38bdf8" opacity={selectedTierId === "shared" ? 0.9 : 0.4} />
                <circle cx="230" cy="205" r="2" fill="#38bdf8" opacity={selectedTierId === "shared" ? 0.9 : 0.4} />
                <circle cx="185" cy="197" r="1.5" fill="#ffffff" opacity={0.5} />
                <circle cx="215" cy="197" r="1.5" fill="#ffffff" opacity={0.5} />
                <circle cx="185" cy="213" r="1.5" fill="#ffffff" opacity={0.5} />
                <circle cx="215" cy="213" r="1.5" fill="#ffffff" opacity={0.5} />
              </g>

              {/* TIER 1: Bottom Slab (Pure Native Binary - Solid Bedrock) */}
              <g
                onClick={() => setSelectedTierId("native")}
                className="cursor-pointer transition-all duration-300"
                style={{
                  transform: selectedTierId === "native" ? "translateY(-6px)" : "translateY(0)",
                }}
              >
                {/* Left Shadow Face (Solid Dark Navy) */}
                <path
                  d="M130 295 L200 335 L200 375 L130 335 Z"
                  fill="#0a0d18"
                  stroke={selectedTierId === "native" ? "#00c2ff" : "rgba(255,255,255,0.35)"}
                  strokeWidth={selectedTierId === "native" ? "2.5" : "2"}
                />
                {/* Right Highlight Face (Solid Navy) */}
                <path
                  d="M200 335 L270 295 L270 335 L200 375 Z"
                  fill="#121829"
                  stroke={selectedTierId === "native" ? "#00c2ff" : "rgba(255,255,255,0.35)"}
                  strokeWidth={selectedTierId === "native" ? "2.5" : "2"}
                />
                {/* Top Matrix Face (Solid Bedrock Plate) */}
                <path
                  d="M200 255 L270 295 L200 335 L130 295 Z"
                  fill={selectedTierId === "native" ? "#1c2540" : "#151b2e"}
                  stroke={selectedTierId === "native" ? "#00c2ff" : "rgba(255,255,255,0.4)"}
                  strokeWidth={selectedTierId === "native" ? "3" : "2"}
                />
                {/* High Density Matrix Dots */}
                <circle cx="170" cy="295" r="2.5" fill="#00c2ff" opacity={selectedTierId === "native" ? 1 : 0.6} />
                <circle cx="200" cy="278" r="2.5" fill="#00c2ff" opacity={selectedTierId === "native" ? 1 : 0.6} />
                <circle cx="200" cy="312" r="2.5" fill="#00c2ff" opacity={selectedTierId === "native" ? 1 : 0.6} />
                <circle cx="230" cy="295" r="2.5" fill="#00c2ff" opacity={selectedTierId === "native" ? 1 : 0.6} />
                <circle cx="185" cy="287" r="2" fill="#ffffff" opacity={0.7} />
                <circle cx="215" cy="287" r="2" fill="#ffffff" opacity={0.7} />
                <circle cx="185" cy="303" r="2" fill="#ffffff" opacity={0.7} />
                <circle cx="215" cy="303" r="2" fill="#ffffff" opacity={0.7} />
              </g>
            </svg>
          </div>

          {/* Quick Select Buttons */}
          <div className="flex items-center gap-2 mt-4 z-10">
            {SURFACE_TIERS.map((tier) => (
              <button
                key={tier.id}
                onClick={() => setSelectedTierId(tier.id)}
                className={`px-3 py-1.5 rounded-full text-xs font-mono transition-all duration-300 ${
                  selectedTierId === tier.id
                    ? "bg-white/15 text-white border border-white/25 shadow-sm"
                    : "bg-transparent text-slate-500 hover:text-slate-300 border border-transparent"
                }`}
              >
                {tier.name.split(" ")[0]}
              </button>
            ))}
          </div>
        </div>

        {/* Right Column: Architectural Tier Details */}
        <div className="lg:col-span-6 flex flex-col justify-center text-left">
          <div className="inline-flex items-center gap-2.5 mb-3">
            <span
              className={`w-2 h-2 rounded-full ${
                selectedTier.id === "native"
                  ? "bg-cyan-400"
                  : selectedTier.id === "shared"
                  ? "bg-sky-400"
                  : "bg-amber-400"
              }`}
            />
            <span className="font-mono text-xs uppercase tracking-wider text-slate-400 font-semibold">
              {selectedTier.status}
            </span>
          </div>

          <h3 className="text-2xl sm:text-3xl font-bold text-white mb-4 tracking-tight">
            {selectedTier.name}
          </h3>

          <p className="text-slate-300 text-base leading-relaxed mb-6 font-light">
            {selectedTier.description}
          </p>

          {/* CLI Flag Pill */}
          <div className="mb-6 p-3 rounded-xl bg-white/[0.03] border border-white/10 font-mono text-xs text-slate-200 flex items-center justify-between">
            <span className="text-slate-400">Compilation Flag:</span>
            <span className="text-cyan-300 font-semibold">{selectedTier.flag}</span>
          </div>

          {/* Technical Specs List */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6">
            {selectedTier.specs.map((spec) => (
              <div
                key={spec.label}
                className="p-3 rounded-xl bg-white/[0.02] border border-white/5 text-left"
              >
                <div className="text-[11px] text-slate-400 font-mono mb-1">{spec.label}</div>
                <div className="text-xs font-semibold text-slate-200">{spec.value}</div>
              </div>
            ))}
          </div>

          {/* Target Platforms */}
          <div>
            <div className="text-xs font-mono uppercase tracking-wider text-slate-400 mb-2">
              Target Surfaces & Platforms
            </div>
            <div className="flex flex-wrap gap-2">
              {selectedTier.targets.map((tgt) => (
                <span
                  key={tgt}
                  className="px-2.5 py-1 rounded-lg text-xs font-mono bg-white/[0.04] border border-white/10 text-slate-300"
                >
                  {tgt}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default IsometricSurfaceStack;
