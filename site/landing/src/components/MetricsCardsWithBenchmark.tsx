import React from "react";

export interface MetricsCardsProps {
  theme?: "dark" | "light";
}

export const MetricsCardsWithBenchmark: React.FC<MetricsCardsProps> = ({ theme = "dark" }) => {
  const isDark = theme === "dark";

  return (
    <section className="w-full max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-20 lg:py-28">
      {/* 3 Metric Cards (Value at Top, Text at Bottom) */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-16">
        {/* Card 1: 24x */}
        <div
          className={`flex flex-col justify-between p-8 rounded-3xl border transition-all duration-300 min-h-[220px] ${
            isDark
              ? "bg-[#0b1924]/80 border-cyan-500/25 text-cyan-400 shadow-[0_12px_40px_rgba(0,194,255,0.06)]"
              : "bg-[#e8f7fd] border-cyan-500/30 text-cyan-700 shadow-sm"
          }`}
        >
          <div>
            <div className="text-5xl sm:text-6xl font-black tracking-tight leading-none mb-2">
              24x
            </div>
            <div className="text-xs font-mono uppercase tracking-widest opacity-80">
              Throughput Speedup
            </div>
          </div>
          <p className="text-xs sm:text-sm font-mono leading-relaxed opacity-90 mt-6 text-slate-300">
            Average execution speedup on compute-heavy Python workloads compared to standard CPython 3.14.
          </p>
        </div>

        {/* Card 2: 6x */}
        <div
          className={`flex flex-col justify-between p-8 rounded-3xl border transition-all duration-300 min-h-[220px] ${
            isDark
              ? "bg-[#101524]/80 border-cyan-600/25 text-cyan-500 shadow-[0_12px_40px_rgba(2,132,199,0.06)]"
              : "bg-[#e0f2fe] border-cyan-600/30 text-cyan-700 shadow-sm"
          }`}
        >
          <div>
            <div className="text-5xl sm:text-6xl font-black tracking-tight leading-none mb-2">
              6x
            </div>
            <div className="text-xs font-mono uppercase tracking-widest opacity-80">
              Lower Peak RSS
            </div>
          </div>
          <p className="text-xs sm:text-sm font-mono leading-relaxed opacity-90 mt-6 text-slate-300">
            Dramatically lower resident memory usage by stripping the runtime interpreter and unboxing scalar registers.
          </p>
        </div>

        {/* Card 3: 0ms */}
        <div
          className={`flex flex-col justify-between p-8 rounded-3xl border transition-all duration-300 min-h-[220px] ${
            isDark
              ? "bg-[#0f1320]/80 border-white/10 text-slate-200 shadow-[0_12px_40px_rgba(148,163,184,0.06)]"
              : "bg-[#f1f5f9] border-slate-300 text-slate-700 shadow-sm"
          }`}
        >
          <div>
            <div className="text-5xl sm:text-6xl font-black tracking-tight leading-none mb-2">
              0ms
            </div>
            <div className="text-xs font-mono uppercase tracking-widest opacity-80">
              Cold Start Latency
            </div>
          </div>
          <p className="text-xs sm:text-sm font-mono leading-relaxed opacity-90 mt-6 text-slate-300">
            Sub-millisecond native machine execution. Zero virtualenv activation lag. Zero JIT warmup penalty.
          </p>
        </div>
      </div>

      {/* Animated Benchmark Bar Chart Section */}
      <div
        className={`w-full p-6 sm:p-10 rounded-3xl border transition-all duration-300 ${
          isDark
            ? "bg-[#0b0f19]/90 border-white/10 shadow-[0_20px_50px_rgba(0,0,0,0.5)]"
            : "bg-white border-slate-200 shadow-lg"
        }`}
      >
        <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-6 border-b border-white/5 mb-8">
          <div>
            <h3 className="text-xl sm:text-2xl font-bold tracking-tight text-white mb-1">
              Benchmark: Latency to 1M Pipeline Records
            </h3>
            <p className="text-xs sm:text-sm font-mono text-slate-400">
              Lower is better · Workload: iterative dataset transformation loop
            </p>
          </div>
          <div className="mt-3 sm:mt-0 inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-mono bg-white/5 text-slate-300 border border-white/10">
            <span>CPython 3.14.0 Baseline</span>
          </div>
        </div>

        {/* Comparison Bars */}
        <div className="space-y-6 font-mono text-xs">
          {/* 1. Flagship: pyscriptc Native Output */}
          <div>
            <div className="flex items-center justify-between text-slate-200 mb-1.5 font-bold">
              <span className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-cyan-400 inline-block shadow-[0_0_8px_#00c2ff]" />
                <span className="text-white">pyscriptc Native Output</span>
                <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-cyan-500/20 text-cyan-300 font-normal">
                  Flagship AOT
                </span>
              </span>
              <span className="text-cyan-400 font-bold">0.61 ms (29.2x faster)</span>
            </div>
            <div className="w-full h-5 bg-white/5 rounded-full overflow-hidden p-0.5">
              <div
                className="h-full rounded-full bg-gradient-to-r from-cyan-400 via-sky-300 to-white shadow-[0_0_12px_rgba(0,194,255,0.6)] transition-all duration-1000"
                style={{ width: "3.5%" }}
              />
            </div>
          </div>

          {/* 2. Runner Up: pyscriptc CPython Island */}
          <div>
            <div className="flex items-center justify-between text-slate-300 mb-1.5">
              <span className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-slate-400 inline-block" />
                <span>pyscriptc CPython Island</span>
                <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-white/5 text-slate-300">
                  Runner Up (Boxed Fallback)
                </span>
              </span>
              <span className="text-slate-200 font-semibold">4.21 ms (4.2x faster)</span>
            </div>
            <div className="w-full h-5 bg-white/5 rounded-full overflow-hidden p-0.5">
              <div
                className="h-full rounded-full bg-gradient-to-r from-amber-500 to-yellow-300 transition-all duration-1000"
                style={{ width: "23.6%" }}
              />
            </div>
          </div>

          {/* 3. Baseline: Standard CPython 3.14 */}
          <div>
            <div className="flex items-center justify-between text-slate-400 mb-1.5">
              <span className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-slate-500 inline-block" />
                <span>Standard CPython 3.14</span>
                <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-white/5 text-slate-400">
                  Host Interpreter
                </span>
              </span>
              <span className="text-slate-400">17.82 ms (1x Baseline)</span>
            </div>
            <div className="w-full h-5 bg-white/5 rounded-full overflow-hidden p-0.5">
              <div
                className="h-full rounded-full bg-slate-600 transition-all duration-1000"
                style={{ width: "100%" }}
              />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

export default MetricsCardsWithBenchmark;
