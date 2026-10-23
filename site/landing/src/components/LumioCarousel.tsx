import React, { useState, useEffect, useRef } from "react";

export interface CarouselPillar {
  id: string;
  title: string;
  subtitle: string;
  description: string;
  badge: string;
  codeSnippet: string;
  command: string;
  output: string;
  metric: string;
}

export const PILLARS: CarouselPillar[] = [
  {
    id: "semantics",
    title: "Full Python Semantics",
    subtitle: "CPython 3.14.x behavior without subset dialects or rewrites",
    description:
      "Write idiomatic, expressive Python. pyscriptc compiles full generator pipelines, pattern matching, custom descriptors, and dynamic dispatch directly into native machine code.",
    badge: "Zero Dialect Drift",
    codeSnippet: `# Full CPython 3.14 pattern matching & dynamic dispatch
class Pipeline:
    def __init__(self, name: str):
        self.name = name

    def dispatch(self, packet: dict) -> str:
        match packet:
            case {"type": "stream", "payload": bytes(data)}:
                return f"{self.name}: processed {len(data)} bytes"
            case {"type": "ping", "ts": float(t)}:
                return f"{self.name}: pong at {t:.2f}s"
            case _:
                return f"{self.name}: unknown packet"`,
    command: "$ pyscriptc compile pipeline.py --target=native -O3",
    output: `[1/3] Parsing CPython 3.14 AST .............. OK (1.2ms)
[2/3] Lowering to typed SSA IR ............ OK (3.8ms)
[3/3] Emitting standalone native binary ... OK (4.1ms)
✓ Produced: ./pipeline (2.1 MB single executable, 0 dependencies)`,
    metric: "100% CPython Parity",
  },
  {
    id: "predictable",
    title: "Predictable, Not Probabilistic",
    subtitle: "Deterministic execution without JIT warmup cliffs or GC spikes",
    description:
      "Unlike JIT runtimes that require millions of warmup iterations and stutter during deoptimization, pyscriptc compiles ahead-of-time. First-iteration speed equals 1,000,000th iteration speed.",
    badge: "0ms Cold Start",
    codeSnippet: `# High-throughput vectorized matrix transformation
def transform_dataset(records: list[tuple[int, float]]) -> float:
    total = 0.0
    for idx, weight in records:
        # Ahead-of-time compiled: zero boxing overhead
        total += (idx * 1.414213) * weight
    return total

records = [(i, i * 0.001) for i in range(1_000_000)]
res = transform_dataset(records)`,
    command: "$ hyperfine './pipeline_native' 'python3 pipeline.py'",
    output: `Benchmark 1: ./pipeline_native (AOT compiled)
  Time (mean ± σ):       0.61 ms ±   0.04 ms    [User: 0.5 ms]
Benchmark 2: python3 pipeline.py (CPython 3.14)
  Time (mean ± σ):      17.82 ms ±   0.62 ms    [User: 16.9 ms]

Summary: ./pipeline_native ran 29.2x faster than CPython 3.14`,
    metric: "29.2x Execution Speedup",
  },
  {
    id: "diagnostics",
    title: "Compile-Time Diagnostics",
    subtitle: "Catch type mismatches and fence violations before shipping",
    description:
      "Rich compiler diagnostics identify type errors, unsupported dynamic mutation, and unboxed fallback boundaries before your binary ever reaches production servers.",
    badge: "Static Verification",
    codeSnippet: `# Explicit compiler boundary verification
from pyscriptc import fence

@fence.native
def compute_hash(seed: int, key: str) -> int:
    # Compiler verifies type stability at compile time
    h = seed ^ len(key)
    for ch in key:
        h = ((h << 5) + h) ^ ord(ch)
    return h & 0xFFFFFFFF`,
    command: "$ pyscriptc check worker.py --strict",
    output: `worker.py:6:5: info: native boundary @fence.native verified
    h = seed ^ len(key)
    ^~~~~~~~~~~~~~~~~~~ unboxed 64-bit integer registers
worker.py:10:12: info: tail-call optimization applied
✓ All 14 functions verified for pure native code emission`,
    metric: "Zero Runtime Surprise",
  },
];

export interface LumioCarouselProps {
  theme?: "dark" | "light";
  variant?: "warm-editorial" | "cyber-precision" | "muji-restraint";
  className?: string;
}

export const LumioCarousel: React.FC<LumioCarouselProps> = ({
  theme = "dark",
  variant = "warm-editorial",
  className = "",
}) => {
  const [activeIndex, setActiveIndex] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const activePillar = PILLARS[activeIndex];

  // Code typing simulation state
  const [displayedCode, setDisplayedCode] = useState("");
  const typingTimerRef = useRef<number | null>(null);

  // Typewriter effect when active tab changes
  useEffect(() => {
    const fullCode = activePillar.codeSnippet;
    setDisplayedCode("");
    let charIdx = 0;

    if (typingTimerRef.current) clearInterval(typingTimerRef.current);

    // Fast fluid typing simulation
    typingTimerRef.current = window.setInterval(() => {
      charIdx += 6;
      if (charIdx >= fullCode.length) {
        setDisplayedCode(fullCode);
        if (typingTimerRef.current) clearInterval(typingTimerRef.current);
      } else {
        setDisplayedCode(fullCode.slice(0, charIdx));
      }
    }, 16);

    return () => {
      if (typingTimerRef.current) clearInterval(typingTimerRef.current);
    };
  }, [activeIndex, activePillar.codeSnippet]);

  // Gentle auto-rotation every 7 seconds if not paused
  useEffect(() => {
    if (isPaused) return;
    const interval = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % PILLARS.length);
    }, 7000);
    return () => clearInterval(interval);
  }, [isPaused]);

  const isDark = theme === "dark";

  return (
    <div
      className={`w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20 lg:py-28 ${className}`}
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
    >
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 lg:gap-16 items-center">
        {/* Left Column: Lumio-Style Vertical Text Carousel */}
        <div className="lg:col-span-5 flex flex-col justify-center">
          <div className="mb-8">
            <h2 className="text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight text-text leading-[1.15]">
              Full Python semantics.<br />
              <span className="text-slate-400 font-normal">Pure machine execution.</span>
            </h2>
          </div>

          {/* Vertical Timeline / Pillar List */}
          <div className="relative pl-6 sm:pl-8 border-l-2 border-slate-700/30 space-y-8 sm:space-y-10">
            {/* Sliding Indicator Pill */}
            <div
              className="absolute left-[-2px] w-[2px] transition-all duration-500 ease-[cubic-bezier(0.16,1,0.3,1)]"
              style={{
                top: `${(activeIndex * 100) / PILLARS.length}%`,
                height: `${100 / PILLARS.length}%`,
                background:
                  variant === "cyber-precision"
                    ? "linear-gradient(to bottom, #00c2ff, #38bdf8)"
                    : "linear-gradient(to bottom, #38bdf8, #fbbf24)",
                boxShadow: "0 0 12px rgba(56, 189, 248, 0.5)",
              }}
            />

            {PILLARS.map((pillar, idx) => {
              const isActive = activeIndex === idx;
              return (
                <div
                  key={pillar.id}
                  onClick={() => setActiveIndex(idx)}
                  className={`cursor-pointer transition-all duration-300 text-left select-none ${
                    isActive ? "opacity-100 translate-x-1" : "opacity-40 hover:opacity-75"
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="font-mono text-xs uppercase tracking-wider text-cyan-400 font-semibold">
                      0{idx + 1}
                    </span>
                    <h3 className="text-lg sm:text-xl font-bold tracking-tight text-white">
                      {pillar.title}
                    </h3>
                  </div>

                  <p className="text-sm font-medium text-slate-300 leading-snug mb-2">
                    {pillar.subtitle}
                  </p>

                  {isActive && (
                    <p className="text-xs sm:text-sm text-slate-400 leading-relaxed transition-all">
                      {pillar.description}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Column: Asciinema-Style Animated Scratchpad */}
        <div className="lg:col-span-7">
          <div
            className={`w-full rounded-2xl overflow-hidden border transition-all duration-500 shadow-2xl ${
              isDark
                ? "bg-[#0b0d14] border-white/10 shadow-[0_20px_50px_rgba(0,0,0,0.6)]"
                : "bg-white border-slate-200 shadow-[0_20px_50px_rgba(0,0,0,0.06)]"
            }`}
          >
            {/* Terminal Window Header */}
            <div
              className={`flex items-center justify-between px-4 py-3 border-b ${
                isDark ? "bg-[#08090e] border-white/5" : "bg-slate-50 border-slate-200"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-rose-500/80 inline-block" />
                <span className="w-3 h-3 rounded-full bg-amber-500/80 inline-block" />
                <span className="w-3 h-3 rounded-full bg-emerald-500/80 inline-block" />
                <span className="ml-2 font-mono text-xs text-slate-400 tracking-wider">
                  pyscriptc :: {activePillar.id}.py
                </span>
              </div>
              <div className="flex items-center gap-2 font-mono text-[11px] text-cyan-400/90">
                <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-ping" />
                <span>{activePillar.metric}</span>
              </div>
            </div>

            {/* Python Code Scratchpad Area */}
            <div className="p-4 sm:p-6 overflow-x-auto font-mono text-xs sm:text-[13px] leading-relaxed text-slate-200 bg-[#06080d]/90 min-h-[220px]">
              <pre className="whitespace-pre">
                <code>{displayedCode}</code>
                <span className="inline-block w-2 h-4 ml-0.5 bg-cyan-400 animate-pulse align-middle" />
              </pre>
            </div>

            {/* Terminal Compiler Output Drawer */}
            <div className="p-4 sm:p-5 border-t border-white/5 bg-[#030407] font-mono text-xs">
              <div className="flex items-center gap-2 text-slate-400 mb-2">
                <span className="text-emerald-400 font-bold">❯</span>
                <span className="text-slate-200 font-semibold">{activePillar.command}</span>
              </div>
              <pre className="text-slate-400 text-[11px] sm:text-xs whitespace-pre-wrap leading-relaxed">
                {activePillar.output}
              </pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LumioCarousel;
