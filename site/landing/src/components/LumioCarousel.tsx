import React, { useState, useEffect, useRef } from "react";

export interface CarouselPillar {
  id: string;
  title: string;
  subtitle: string;
  description: string;
  filename: string;
  codeSnippet: string;
  command: string;
  output: string[];
}

export const PILLARS: CarouselPillar[] = [
  {
    id: "semantics",
    title: "Full Python Semantics",
    subtitle: "CPython 3.14.x behavior without subset dialects or rewrites",
    description:
      "Write idiomatic, expressive Python. pyscriptc compiles full generator pipelines, pattern matching, custom descriptors, and dynamic dispatch directly into native machine code.",
    filename: "pipeline.py",
    codeSnippet: `class EventStream:
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
    command: "pyscriptc compile pipeline.py --target=native -O3",
    output: [
      "[1/3] Parsing CPython 3.14 AST ............... OK (1.2ms)",
      "[2/3] Lowering to typed SSA IR ............. OK (3.8ms)",
      "[3/3] Emitting standalone native binary .... OK (4.1ms)",
      "✓ Output: ./pipeline (2.1 MB single executable, 0 dependencies)",
    ],
  },
  {
    id: "predictable",
    title: "Predictable, Not Probabilistic",
    subtitle: "Deterministic execution without JIT warmup cliffs or GC spikes",
    description:
      "Unlike JIT runtimes that require millions of warmup iterations and stutter during deoptimization, pyscriptc compiles ahead-of-time. First-iteration speed equals 1,000,000th iteration speed.",
    filename: "transform.py",
    codeSnippet: `def transform_dataset(records: list[tuple[int, float]]) -> float:
    total = 0.0
    for idx, weight in records:
        # AOT compiled: unboxed 64-bit scalar registers
        total += (idx * 1.414213) * weight
    return total

records = [(i, i * 0.001) for i in range(1_000_000)]
res = transform_dataset(records)`,
    command: "hyperfine './transform_native' 'python3 transform.py'",
    output: [
      "Benchmark 1: ./transform_native (pyscriptc AOT): 0.61 ms ± 0.04 ms",
      "Benchmark 2: python3 transform.py (CPython 3.14): 17.82 ms ± 0.62 ms",
      "✓ Summary: ./transform_native ran 29.2x faster than CPython 3.14",
    ],
  },
  {
    id: "diagnostics",
    title: "Compile-Time Diagnostics",
    subtitle: "Catch type mismatches and fence violations before shipping",
    description:
      "Rich compiler diagnostics identify type errors, unsupported dynamic mutation, and unboxed fallback boundaries before your binary ever reaches production servers.",
    filename: "verify.py",
    codeSnippet: `from pyscriptc import fence

@fence.native
def compute_hash(seed: int, key: str) -> int:
    # Compiler verifies type stability and unboxing boundaries
    h = seed ^ len(key)
    for ch in key:
        h = ((h << 5) + h) ^ ord(ch)
    return h & 0xFFFFFFFF`,
    command: "pyscriptc check verify.py --strict",
    output: [
      "verify.py:4:1: info: native boundary @fence.native verified",
      "verify.py:7:9: info: unboxed 64-bit integer registers applied",
      "verify.py:9:5: info: tail-call optimization applied",
      "✓ All 14 functions verified for pure native code emission",
    ],
  },
];

export interface LumioCarouselProps {
  theme?: "dark" | "light";
  className?: string;
}

export const LumioCarousel: React.FC<LumioCarouselProps> = ({
  theme = "dark",
  className = "",
}) => {
  const [activeIndex, setActiveIndex] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const [showTerminal, setShowTerminal] = useState(false);
  const activePillar = PILLARS[activeIndex];

  // Code typing simulation
  const [displayedCode, setDisplayedCode] = useState("");
  const typingTimerRef = useRef<number | null>(null);

  // Typewriter sequence -> Terminal Pop sequence
  useEffect(() => {
    const fullCode = activePillar.codeSnippet;
    setDisplayedCode("");
    setShowTerminal(false);
    let charIdx = 0;

    if (typingTimerRef.current) clearInterval(typingTimerRef.current);

    typingTimerRef.current = window.setInterval(() => {
      charIdx += 7;
      if (charIdx >= fullCode.length) {
        setDisplayedCode(fullCode);
        if (typingTimerRef.current) clearInterval(typingTimerRef.current);
        // Stage 2: Terminal pops open after code writeup is finished!
        setTimeout(() => {
          setShowTerminal(true);
        }, 300);
      } else {
        setDisplayedCode(fullCode.slice(0, charIdx));
      }
    }, 16);

    return () => {
      if (typingTimerRef.current) clearInterval(typingTimerRef.current);
    };
  }, [activeIndex, activePillar.codeSnippet]);

  // Automatic looping: rotates every 7.5 seconds
  useEffect(() => {
    if (isPaused) return;
    const interval = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % PILLARS.length);
    }, 7500);
    return () => clearInterval(interval);
  }, [isPaused]);

  const isDark = theme === "dark";

  return (
    <div
      className={`w-full max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-20 lg:py-28 ${className}`}
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
    >
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 lg:gap-16 items-start">
        {/* Left Column: Lumio-Style Vertical Text Carousel */}
        <div className="lg:col-span-5 flex flex-col justify-center text-left">
          <div className="mb-10">
            <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-white leading-tight">
              Full Python semantics.<br />
              <span className="text-slate-400 font-light">Pure machine execution.</span>
            </h2>
          </div>

          {/* Vertical Timeline / Pillar List */}
          <div className="relative pl-6 sm:pl-8 border-l-2 border-slate-700/30 space-y-8 sm:space-y-10">
            {/* Sliding Indicator Bar */}
            <div
              className="absolute left-[-2px] w-[2px] transition-all duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] bg-gradient-to-b from-cyan-400 to-sky-300 shadow-[0_0_12px_rgba(56,189,248,0.5)]"
              style={{
                top: `${(activeIndex * 100) / PILLARS.length}%`,
                height: `${100 / PILLARS.length}%`,
              }}
            />

            {PILLARS.map((pillar, idx) => {
              const isActive = activeIndex === idx;
              return (
                <div
                  key={pillar.id}
                  onClick={() => setActiveIndex(idx)}
                  className={`cursor-pointer transition-all duration-300 select-none ${
                    isActive ? "opacity-100 translate-x-1" : "opacity-40 hover:opacity-75"
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1">
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

        {/* Right Column: Minimal OnOrca-Style Code Editor & Terminal Popup */}
        <div className="lg:col-span-7 w-full">
          <div
            className={`w-full rounded-2xl overflow-hidden border transition-all duration-500 shadow-2xl ${
              isDark
                ? "bg-[#090b10] border-white/10 shadow-[0_20px_50px_rgba(0,0,0,0.7)]"
                : "bg-white border-slate-200 shadow-xl"
            }`}
          >
            {/* Minimal Clean Tab Header (No fake window buttons) */}
            <div
              className={`flex items-center justify-between px-5 py-3 border-b text-xs font-mono ${
                isDark ? "bg-[#0c0e16] border-white/5" : "bg-slate-50 border-slate-200"
              }`}
            >
              <div className="flex items-center gap-2 text-slate-300 font-semibold">
                <span className="text-cyan-400">&gt;_</span>
                <span>{activePillar.filename}</span>
              </div>
              <div className="text-[11px] text-slate-500">
                CPython 3.14 AST
              </div>
            </div>

            {/* Stage 1: Editor Code Writeup */}
            <div className="p-5 sm:p-6 overflow-x-auto font-mono text-xs sm:text-[13px] leading-relaxed text-slate-200 bg-[#06080d] min-h-[190px]">
              <pre className="whitespace-pre">
                <code>{displayedCode}</code>
                {!showTerminal && (
                  <span className="inline-block w-2 h-4 ml-0.5 bg-cyan-400 animate-pulse align-middle" />
                )}
              </pre>
            </div>

            {/* Stage 2: Popping Terminal Runner (OnOrca style) */}
            <div
              className={`border-t transition-all duration-500 overflow-hidden font-mono text-xs ${
                showTerminal ? "max-h-[220px] opacity-100 p-5" : "max-h-0 opacity-0 p-0"
              } ${isDark ? "bg-[#040508] border-white/10" : "bg-slate-100 border-slate-200"}`}
            >
              <div className="flex items-center gap-2 text-slate-300 font-semibold mb-2.5">
                <span className="text-cyan-400 font-bold">$</span>
                <span>{activePillar.command}</span>
              </div>
              <div className="space-y-1 text-slate-400 text-[11px] sm:text-xs">
                {activePillar.output.map((line, i) => (
                  <div
                    key={i}
                    className={line.startsWith("✓") ? "text-cyan-300 font-bold pt-1" : ""}
                  >
                    {line}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LumioCarousel;
