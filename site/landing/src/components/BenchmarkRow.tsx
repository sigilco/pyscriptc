import React from "react";

export interface BenchmarkRowProps {
  theme?: "dark" | "light";
  benchmarkUrl?: string;
}

interface BenchmarkMetric {
  title: string;
  subtitle: string;
  unit: string;
  better: "Higher is better" | "Lower is better";
  series: {
    name: string;
    value: string;
    rawHeightPct: number;
    color: string;
    isFlagship?: boolean;
  }[];
}

const BENCHMARKS: BenchmarkMetric[] = [
  {
    title: "Throughput",
    subtitle: "Compute kernels geometric mean",
    unit: "Speedup vs CPython",
    better: "Higher is better",
    series: [
      { name: "CPython 3.14", value: "1.0x", rawHeightPct: 10, color: "#334155" },
      { name: "Island Fallback", value: "4.2x", rawHeightPct: 30, color: "#fbbf24" },
      { name: "Pure Native", value: "29.2x", rawHeightPct: 100, color: "#00c2ff", isFlagship: true },
    ],
  },
  {
    title: "Peak Memory (RSS)",
    subtitle: "Dataset transformation loop",
    unit: "Megabytes (MB)",
    better: "Lower is better",
    series: [
      { name: "CPython 3.14", value: "84 MB", rawHeightPct: 100, color: "#334155" },
      { name: "Island Fallback", value: "38 MB", rawHeightPct: 45, color: "#fbbf24" },
      { name: "Pure Native", value: "14 MB", rawHeightPct: 17, color: "#00c2ff", isFlagship: true },
    ],
  },
  {
    title: "Cold Start Latency",
    subtitle: "Process initialization to exec",
    unit: "Milliseconds (ms)",
    better: "Lower is better",
    series: [
      { name: "CPython 3.14", value: "17.8 ms", rawHeightPct: 100, color: "#334155" },
      { name: "Island Fallback", value: "4.2 ms", rawHeightPct: 24, color: "#fbbf24" },
      { name: "Pure Native", value: "0.6 ms", rawHeightPct: 8, color: "#00c2ff", isFlagship: true },
    ],
  },
];

export const BenchmarkRow: React.FC<BenchmarkRowProps> = ({
  theme = "dark",
  benchmarkUrl = "https://github.com/sigilco/pyscriptc/tree/master/benchmarks",
}) => {
  return (
    <section className="w-full max-w-6xl mx-auto px-6 py-20 lg:py-24 text-left">
      {/* 1x3 Vertical Bar Charts Row */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-12 lg:gap-16">
        {BENCHMARKS.map((b) => (
          <div key={b.title} className="flex flex-col justify-between">
            {/* Minimal Header */}
            <div className="mb-8">
              <div className="flex items-baseline justify-between mb-1">
                <h3 className="text-lg font-bold text-white tracking-tight">{b.title}</h3>
                <span className="text-[11px] font-mono text-slate-500 uppercase tracking-wider">
                  {b.better}
                </span>
              </div>
              <p className="text-xs text-slate-400 font-light">{b.subtitle}</p>
            </div>

            {/* Vertical Bars Stage */}
            <div className="h-44 flex items-end justify-between gap-4 border-b border-white/10 pb-3 pt-6">
              {b.series.map((item) => (
                <div key={item.name} className="flex-1 flex flex-col items-center h-full justify-end">
                  {/* Metric Value Label on Top of Bar */}
                  <span
                    className={`font-mono text-xs mb-2 ${
                      item.isFlagship ? "text-cyan-400 font-bold" : "text-slate-300"
                    }`}
                  >
                    {item.value}
                  </span>

                  {/* Rectangular Vertical Bar */}
                  <div className="w-full bg-white/[0.03] flex items-end h-32 relative">
                    <div
                      className="w-full transition-all duration-700 ease-out"
                      style={{
                        height: `${item.rawHeightPct}%`,
                        backgroundColor: item.color,
                        opacity: item.isFlagship ? 1 : 0.85,
                      }}
                    />
                  </div>

                  {/* Series Name Below Baseline */}
                  <span className="font-mono text-[11px] text-slate-400 mt-2.5 text-center leading-tight">
                    {item.name.replace("pyscriptc ", "")}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Reproducible Benchmark Link */}
      <div className="mt-10 pt-6 border-t border-white/5 flex items-center justify-between text-xs text-slate-400">
        <span>Workload tested on Apple M3 Max / Linux x86_64 host</span>
        <a
          href={benchmarkUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-slate-400 hover:text-white transition-colors underline underline-offset-4"
        >
          See benchmark sources & methodology ↗
        </a>
      </div>
    </section>
  );
};

export default BenchmarkRow;
