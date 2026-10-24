import React, { useState, useEffect, useRef } from "react";
import highlightedSnippetsData from "../data/highlightedSnippets.json";

export interface CarouselPillar {
  id: "semantics" | "predictable" | "diagnostics";
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
    subtitle: "CPython 3.14.x semantics for the supported subset",
    description:
      "Write idiomatic, expressive Python for the supported subset. pyscriptc compiles pattern matching and generator expressions (materialized eagerly today) directly into native machine code.",
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
      "✓ Output: ./pipeline (~139 KB standalone binary, 0 dependencies)",
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
      "Benchmark: ./transform_native vs python3 transform.py on 1,000,000 records",
      "✓ Geometric-mean speedup on compute kernels: 23.9x vs CPython",
      "✓ Peak RSS: 5.8x lower than CPython (kernel group geomean)",
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

const highlightedSnippets = highlightedSnippetsData as Record<string, string>;

export interface LumioCarouselProps {
  theme?: "dark" | "light";
  className?: string;
  autoPlayInterval?: number;
}

export const LumioCarousel: React.FC<LumioCarouselProps> = ({
  theme = "dark",
  className = "",
  autoPlayInterval = 8500,
}) => {
  const [activeIndex, setActiveIndex] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const [animKey, setAnimKey] = useState(0);
  const activePillar = PILLARS[activeIndex];

  const handleSelect = (idx: number) => {
    setActiveIndex(idx);
    setAnimKey((prev) => prev + 1);
  };

  // Shell typing states
  const [displayedCommand, setDisplayedCommand] = useState("");
  const [displayedOutput, setDisplayedOutput] = useState<string[]>([]);

  const shellTimerRef = useRef<number | null>(null);
  const lineTimerRef = useRef<number | null>(null);

  // Synchronized animation: When slide changes, both editor and shell fade in together.
  // Shell starts empty on fade-in, then types command and streams output lines.
  useEffect(() => {
    if (shellTimerRef.current) clearInterval(shellTimerRef.current);
    if (lineTimerRef.current) clearInterval(lineTimerRef.current);

    setDisplayedCommand("");
    setDisplayedOutput([]);

    const fullCmd = activePillar.command;
    const fullOut = activePillar.output;

    // Small delay for initial empty fade-in, then type shell command
    const startTimeout = window.setTimeout(() => {
      let cmdIdx = 0;
      shellTimerRef.current = window.setInterval(() => {
        cmdIdx += 2;
        if (cmdIdx >= fullCmd.length) {
          setDisplayedCommand(fullCmd);
          if (shellTimerRef.current) clearInterval(shellTimerRef.current);

          // Stream output lines
          let outIdx = 0;
          lineTimerRef.current = window.setInterval(() => {
            outIdx += 1;
            setDisplayedOutput(fullOut.slice(0, outIdx));
            if (outIdx >= fullOut.length && lineTimerRef.current) {
              clearInterval(lineTimerRef.current);
            }
          }, 200);
        } else {
          setDisplayedCommand(fullCmd.slice(0, cmdIdx));
        }
      }, 22);
    }, 400);

    return () => {
      clearTimeout(startTimeout);
      if (shellTimerRef.current) clearInterval(shellTimerRef.current);
      if (lineTimerRef.current) clearInterval(lineTimerRef.current);
    };
  }, [activeIndex]);

  // Automatic looping with synchronized countdown timer
  useEffect(() => {
    if (isPaused) return;
    const interval = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % PILLARS.length);
      setAnimKey((prev) => prev + 1);
    }, autoPlayInterval);
    return () => clearInterval(interval);
  }, [isPaused, autoPlayInterval, activeIndex]);

  const currentHighlightedHtml = highlightedSnippets[activePillar.id] || "";

  return (
    <div
      className={`w-full max-w-6xl mx-auto px-6 py-20 lg:py-28 ${className}`}
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => {
        setIsPaused(false);
        setAnimKey((prev) => prev + 1);
      }}
    >
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 lg:gap-16 items-start">
        {/* Left Column: Lumio-Style Vertical Text Carousel */}
        <div className="lg:col-span-5 flex flex-col justify-center text-left">
          <div className="mb-10">
            <h2 className="text-3xl sm:text-4xl font-normal tracking-tight text-white leading-tight">
              Full Python semantics.<br />
              <span className="text-slate-400">Pure machine execution.</span>
            </h2>
          </div>

          {/* Vertical Timeline / Pillar List */}
          <div className="relative pl-6 sm:pl-8 border-l border-white/10 space-y-8 sm:space-y-10">
            {PILLARS.map((pillar, idx) => {
              const isActive = activeIndex === idx;
              return (
                <div
                  key={pillar.id}
                  onClick={() => handleSelect(idx)}
                  className={`cursor-pointer transition-all duration-300 select-none ${
                    isActive ? "opacity-100" : "opacity-40 hover:opacity-75"
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="font-mono text-xs uppercase tracking-wider text-slate-400 font-semibold">
                      0{idx + 1}
                    </span>
                    <h3 className="text-lg font-bold tracking-tight text-white">
                      {pillar.title}
                    </h3>
                  </div>

                  <p className="text-sm font-normal text-slate-300 leading-snug mb-2">
                    {pillar.subtitle}
                  </p>

                  {isActive && (
                    <p className="text-xs sm:text-sm text-slate-400 font-light leading-relaxed">
                      {pillar.description}
                    </p>
                  )}
                </div>
              );
            })}

            {/* Upstream-style Dots/Pills indicator (LTR countdown) */}
            <div
              className="flex items-center gap-2 pt-2"
              role="group"
              aria-label="Pillar navigation"
            >
              {PILLARS.map((pillar, idx) => {
                const isActive = activeIndex === idx;
                return (
                  <button
                    key={pillar.id}
                    type="button"
                    onClick={() => handleSelect(idx)}
                    aria-label={`Pillar ${idx + 1}: ${pillar.title}`}
                    aria-pressed={isActive}
                    className={`relative overflow-hidden rounded-full transition-all duration-300 h-1.5 cursor-pointer border-0 p-0 ${
                      isActive ? "w-8 bg-white/20" : "w-2 bg-white/10 hover:bg-white/25"
                    }`}
                  >
                    {isActive && (
                      <span
                        key={`pill-fill-${activeIndex}-${animKey}`}
                        className="absolute inset-0 rounded-full origin-left bg-white"
                        style={{
                          animation: `progress-ltr ${autoPlayInterval}ms linear forwards`,
                          animationPlayState: isPaused ? "paused" : "running",
                        }}
                      />
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right Column: Minimal Terminal + Shiki Highlighted Code */}
        <div className="lg:col-span-7 w-full">
          {/* Keyed container so both editor and shell fade in/out together */}
          <div
            key={activeIndex}
            className="w-full rounded-xl overflow-hidden border border-white/10 bg-[#0b0f19] transition-opacity duration-500 animate-fadeIn"
          >
            {/* Minimal KISS Tab Bar matching Screenshot 2026-10-23 at 19.34.52 */}
            <div className="flex items-center justify-between px-3 pt-2.5 bg-[#06080d] border-b border-white/5 select-none">
              <div className="flex items-center">
                {/* Active Tab */}
                <div className="flex items-center gap-2 px-3 py-1.5 rounded-t-lg bg-[#0b0f19] border-t border-l border-r border-white/10 text-xs font-mono text-slate-200">
                  <span className="text-slate-400">&gt;_</span>
                  <span>{activePillar.filename}</span>
                </div>
                {/* Plus button */}
                <button
                  className="px-2.5 py-1 text-slate-600 hover:text-slate-400 text-sm font-mono transition-colors"
                  aria-label="New tab"
                >
                  +
                </button>
              </div>
            </div>

            {/* Code Editor Body with Shiki Syntax Highlighting */}
            <div className="p-5 font-mono text-xs sm:text-[13px] leading-relaxed bg-[#0b0f19] min-h-[190px] overflow-x-auto text-left">
              <div
                className="shiki-container [&>pre]:!bg-transparent [&>pre]:!p-0 [&>pre]:!m-0 [&_code]:font-mono"
                dangerouslySetInnerHTML={{ __html: currentHighlightedHtml }}
              />
            </div>

            {/* Shell Body (Starts empty, types command and streams output) */}
            <div className="border-t border-white/10 p-5 bg-[#06080d] font-mono text-xs text-slate-300 min-h-[130px] text-left">
              <div className="flex items-center gap-2 text-slate-300 font-semibold mb-2">
                <span className="text-slate-500 font-bold">$</span>
                <span>{displayedCommand}</span>
                {displayedCommand.length < activePillar.command.length && (
                  <span className="inline-block w-2 h-3.5 bg-white/70 animate-pulse align-middle" />
                )}
              </div>

              {/* Output stream */}
              <div className="space-y-1 text-slate-400 text-[11px] sm:text-xs pt-1">
                {displayedOutput.map((line, i) => (
                  <div
                    key={i}
                    className={line.startsWith("✓") ? "text-slate-200 font-bold pt-1" : ""}
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
