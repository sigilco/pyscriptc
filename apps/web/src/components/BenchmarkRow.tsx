import React, { useState, useEffect, useRef } from "react";

export interface BenchmarkRowProps {
  theme?: "dark" | "light";
  benchmarkUrl?: string;
}

interface BenchmarkSeries {
  name: string;
  shortName: string;
  value: string;
  // Bar length on this card's own linear scale: value / max(values) * 100.
  heightPct: number;
  color: string;
  isFlagship?: boolean;
}

interface BenchmarkCardData {
  title: string;
  subtitle: string;
  unit: string;
  better: "Higher is better" | "Lower is better";
  series: BenchmarkSeries[];
}

// Measured on Linux x86_64 against the pinned CPython 3.14.9 oracle
// (benchmarks/baseline). Ratios are geometric means over the workload group.
// heightPct is value / max(values) * 100 so every card is a linear scale.
const BENCHMARKS: BenchmarkCardData[] = [
  {
    title: "Throughput",
    subtitle: "Compute kernels geometric mean",
    unit: "Speedup vs CPython 3.14.9 (Linux x86_64)",
    better: "Higher is better",
    series: [
      { name: "CPython 3.14.9", shortName: "CPython", value: "1.0x", heightPct: pct(1.0, 22.8), color: "#334155" },
      { name: "Pure Native", shortName: "Native", value: "22.8x", heightPct: pct(22.8, 22.8), color: "#4d80c4", isFlagship: true },
    ],
  },
  {
    title: "Peak Memory (RSS)",
    subtitle: "Peak RSS, kernel group",
    unit: "Megabytes (MB) vs CPython 3.14.9",
    better: "Lower is better",
    series: [
      { name: "CPython 3.14.9", shortName: "CPython", value: "~11.5 MB", heightPct: pct(11.5, 11.5), color: "#334155" },
      { name: "Pure Native", shortName: "Native", value: "~2.2 MB", heightPct: pct(2.2, 11.5), color: "#4d80c4", isFlagship: true },
    ],
  },
  {
    title: "Standard Library",
    subtitle: "Standard library workloads geometric mean",
    unit: "Speedup vs CPython 3.14.9 (Linux x86_64)",
    better: "Higher is better",
    series: [
      { name: "CPython 3.14.9", shortName: "CPython", value: "1.0x", heightPct: pct(1.0, 14.9), color: "#334155" },
      { name: "Pure Native", shortName: "Native", value: "14.9x", heightPct: pct(14.9, 14.9), color: "#4d80c4", isFlagship: true },
    ],
  },
];

function pct(value: number, max: number): number {
  return Math.round((value / max) * 100);
}

// Exact SVG dimensions & coordinate scale constants
const SVG_WIDTH = 300;
const SVG_HEIGHT = 180;
const BASELINE_Y = 142; // Shared horizontal baseline for all bars
const MAX_BAR_HEIGHT = 100; // Max bar height in pixels (from baseline upwards)
const BAR_WIDTH = 58;
const PLOT_LEFT = 16;
const PLOT_RIGHT = SVG_WIDTH - 16;

// Distribute the bars evenly across the plot area, so any number of series
// shares the same baseline and margins.
function barCenters(count: number): number[] {
  const span = PLOT_RIGHT - PLOT_LEFT;
  return Array.from({ length: count }, (_, i) => PLOT_LEFT + (i + 0.5) * (span / count));
}

export const BenchmarkRow: React.FC<BenchmarkRowProps> = ({
  theme = "dark",
  benchmarkUrl = "https://github.com/sigilco/pyscriptc/tree/master/benchmarks",
}) => {
  const [animProgress, setAnimProgress] = useState(0);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const animatedRef = useRef(false);

  // Smooth ease-out cubic animation triggered via IntersectionObserver
  useEffect(() => {
    let animationFrameId: number;
    let startTime: number | null = null;
    const duration = 900; // 900ms smooth animation

    const startAnimation = () => {
      if (animatedRef.current) return;
      animatedRef.current = true;
      const step = (timestamp: number) => {
        if (!startTime) startTime = timestamp;
        const elapsed = timestamp - startTime;
        const progress = Math.min(elapsed / duration, 1);
        // Ease-out cubic: 1 - (1 - t)^3
        const eased = 1 - Math.pow(1 - progress, 3);
        setAnimProgress(eased);

        if (progress < 1) {
          animationFrameId = requestAnimationFrame(step);
        }
      };
      animationFrameId = requestAnimationFrame(step);
    };

    if (containerRef.current) {
      const rect = containerRef.current.getBoundingClientRect();
      if (rect.top < window.innerHeight && rect.bottom > 0) {
        startAnimation();
        return () => {
          if (animationFrameId) cancelAnimationFrame(animationFrameId);
        };
      }
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          startAnimation();
          observer.disconnect();
        }
      },
      { threshold: 0.05 }
    );

    if (containerRef.current) {
      observer.observe(containerRef.current);
    } else {
      startAnimation();
    }

    return () => {
      observer.disconnect();
      if (animationFrameId) cancelAnimationFrame(animationFrameId);
    };
  }, []);

  return (
    <section ref={containerRef} className="w-full max-w-6xl mx-auto px-6 py-20 lg:py-24 text-left">
      {/* 1x3 Vertical Bar Charts Row */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-12 lg:gap-16">
        {BENCHMARKS.map((b) => (
          <div key={b.title} className="flex flex-col justify-between">
            {/* Header info */}
            <div className="mb-6">
              <div className="flex items-baseline justify-between mb-1">
                <h3 className="text-lg font-bold text-white tracking-tight">{b.title}</h3>
                <span className="text-[11px] font-mono text-slate-400 uppercase tracking-wider">
                  {b.better}
                </span>
              </div>
              <p className="text-xs text-slate-400 font-light">{b.subtitle}</p>
            </div>

            {/* Cartesian Coordinate SVG Visualizer (Visx / Observable Plot Architecture) */}
            <div className="w-full select-none">
              <svg
                viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
                className="w-full h-auto overflow-visible"
                aria-label={`${b.title} benchmark bar chart`}
              >
                {/* Horizontal Baseline (Shared exact pixel alignment) */}
                <line
                  x1="16"
                  y1={BASELINE_Y}
                  x2={SVG_WIDTH - 16}
                  y2={BASELINE_Y}
                  stroke="rgba(255, 255, 255, 0.15)"
                  strokeWidth="1"
                />

                {/* Render Bars and Labels on Shared Coordinate Grid */}
                {b.series.map((item, idx) => {
                  const centerX = barCenters(b.series.length)[idx];
                  const barX = centerX - BAR_WIDTH / 2;
                  const targetHeight = (item.heightPct / 100) * MAX_BAR_HEIGHT;
                  const currentHeight = Math.max(targetHeight * animProgress, 2);
                  const currentY = BASELINE_Y - currentHeight;

                  return (
                    <g key={item.name} className="transition-opacity duration-300 hover:opacity-90">
                      {/* Metric Value Text (Directly Above Bar) */}
                      <text
                        x={centerX}
                        y={currentY - 7}
                        textAnchor="middle"
                        fill={item.isFlagship ? "#4d80c4" : "#cbd5e1"}
                        className={`font-mono text-xs ${item.isFlagship ? "font-bold" : "font-normal"}`}
                        style={{ opacity: animProgress > 0.1 ? 1 : 0 }}
                      >
                        {item.value}
                      </text>

                      {/* Pure Rectangular Vertical Bar (Strict rx=0, ry=0) */}
                      <rect
                        x={barX}
                        y={currentY}
                        width={BAR_WIDTH}
                        height={currentHeight}
                        fill={item.color}
                        opacity={item.isFlagship ? 1 : 0.85}
                      />

                      {/* Fixed Baseline Label Text Below Rule */}
                      <text
                        x={centerX}
                        y={BASELINE_Y + 20}
                        textAnchor="middle"
                        fill="#94a3b8"
                        className="font-mono text-[11px]"
                      >
                        {item.shortName}
                      </text>
                    </g>
                  );
                })}
              </svg>
            </div>
          </div>
        ))}
      </div>

      {/* Reproducible Benchmark Link */}
      <div className="mt-10 pt-6 border-t border-white/5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 text-xs text-slate-400">
        <span>
          Baseline: CPython 3.14.9, Linux x86_64. Geometric means over the workload group.
        </span>
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
