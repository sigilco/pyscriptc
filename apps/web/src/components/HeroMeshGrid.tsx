import React, { useEffect, useState, useRef, useMemo, useCallback } from "react";

export interface HeroMeshGridProps {
  /**
   * Animation state:
   * - "disabled": all mesh cells hidden (opacity 0), background is fully dark/light.
   * - "active": random mesh cells continuously fade in (0 -> 100) and fade out (100 -> 0)
   *             with Python logo accent colors (blue/yellow).
   */
  state?: "disabled" | "active";
  /** Theme mode */
  theme?: "dark" | "light";
  /** Number of simultaneous actively pulsing cells (default: 16) */
  activeCount?: number;
  /** Whether to show subtle hairline cell grid lines */
  showGridLines?: boolean;
  /** Whether mouse hover dynamically illuminates nearby cells */
  interactiveHover?: boolean;
  /** Dim kinetic words behind the hero copy zone (feathered mask vignette) */
  copyVignette?: boolean;
  /** Class name for root container */
  className?: string;
  /** Children to overlay on top of the mesh (e.g. Hero content) */
  children?: React.ReactNode;
}

// Authentic top 100 PyPI package names
export const TOP_100_PYPI_PACKAGES: readonly string[] = [
  "fastapi", "pydantic", "numpy", "pandas", "torch", "requests", "urllib3", "pytest",
  "setuptools", "typing-extensions", "certifi", "idna", "charset-normalizer", "s3transfer",
  "boto3", "botocore", "six", "pip", "packaging", "cffi", "cryptography", "pycparser",
  "attrs", "pyyaml", "click", "jinja2", "markupsafe", "zipp", "importlib-metadata",
  "wheel", "pluggy", "iniconfig", "exceptiongroup", "tomli", "scikit-learn", "scipy",
  "pillow", "tqdm", "rich", "aiohttp", "uvicorn", "greenlet", "sqlalchemy", "alembic",
  "pydantic-core", "annotated-types", "anyio", "starlette", "sniffio", "httpx", "httpcore",
  "h11", "websockets", "watchfiles", "dnspython", "email-validator", "python-dotenv",
  "google-api-core", "protobuf", "grpcio", "cachetools", "googleapis-common-protos",
  "pyasn1", "rsa", "pytz", "dateutil", "filelock", "virtualenv", "platformdirs", "distlib",
  "networkx", "sympy", "mpmath", "pygments", "markdown-it-py", "mdurl", "typing-inspection",
  "colorama", "psutil", "humanize", "regex", "multidict", "yarl", "frozenlist", "aiosignal",
  "async-timeout", "fsspec", "safetensors", "huggingface-hub", "timm", "transformers",
  "tokenizers", "datasets", "accelerate", "polars", "pyarrow", "duckdb", "litellm",
  "lxml", "scapy"
];

// Python official logo accent colors
// Hero motion-layer colors: the logo's head teal and the Python yellow. These
// are exempt from the no-cyan rule because they belong to the hero animation
// only (DESIGN.md, "motion layer"), never to links, buttons or chrome.
const PYTHON_TEAL = "#31e8f5";
const PYTHON_YELLOW = "#fbbf24";

interface ActiveCellPulse {
  key: number;
  color: string;
  durationMs: number;
}

export const HeroMeshGrid: React.FC<HeroMeshGridProps> = ({
  state = "active",
  theme = "dark",
  activeCount = 0,
  showGridLines = false,
  interactiveHover = true,
  copyVignette = false,
  className = "",
  children,
}) => {
  const packages = useMemo(() => TOP_100_PYPI_PACKAGES, []);

  // Map of index -> ActiveCellPulse
  const [activePulses, setActivePulses] = useState<Record<number, ActiveCellPulse>>({});
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  const stateRef = useRef(state);
  stateRef.current = state;
  const pulseKeyRef = useRef(0);

  // Active fade animation cycle
  useEffect(() => {
    if (state === "disabled") {
      setActivePulses({});
      return;
    }

    let isMounted = true;
    const timeouts: number[] = [];

    const fireCell = () => {
      if (!isMounted || stateRef.current === "disabled") return;

      const targetIndex = Math.floor(Math.random() * packages.length);
      const color = Math.random() > 0.45 ? PYTHON_TEAL : PYTHON_YELLOW;
      // Elegant, slower breathing cycle (3.5s - 5.0s)
      const durationMs = 3500 + Math.random() * 1500;
      pulseKeyRef.current += 1;
      const pulseId = pulseKeyRef.current;

      setActivePulses((prev) => ({
        ...prev,
        [targetIndex]: {
          key: pulseId,
          color,
          durationMs,
        },
      }));

      const timeout = window.setTimeout(() => {
        if (!isMounted) return;
        setActivePulses((prev) => {
          if (prev[targetIndex]?.key === pulseId) {
            const next = { ...prev };
            delete next[targetIndex];
            return next;
          }
          return prev;
        });
      }, durationMs);

      timeouts.push(timeout);
    };

    // Spawn initial pool
    for (let i = 0; i < activeCount; i++) {
      const delay = Math.random() * 1800;
      timeouts.push(window.setTimeout(fireCell, delay));
    }

    // Continuous organic replenishment (slower pace when activeCount is 0 or low)
    const intervalMs = activeCount === 0 ? 1100 : Math.max(240, 1600 / activeCount);
    const interval = window.setInterval(() => {
      fireCell();
    }, intervalMs);

    return () => {
      isMounted = false;
      timeouts.forEach((t) => clearTimeout(t));
      clearInterval(interval);
    };
  }, [state, packages.length, activeCount]);

  const handleMouseEnter = useCallback(
    (index: number) => {
      if (!interactiveHover || state === "disabled") return;
      setHoveredIndex(index);
    },
    [interactiveHover, state]
  );

  const handleMouseLeave = useCallback(() => {
    setHoveredIndex(null);
  }, []);

  const isDark = theme === "dark";

  // Deterministic per-cell accent so a word always breathes in the same Python
  // color, on hover and on pulse alike (roughly half blue, half yellow).
  const cellColor = useCallback(
    (index: number) => {
      const name = packages[index];
      let hash = 0;
      for (let i = 0; i < name.length; i++) {
        hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
      }
      return hash % 5 < 2 ? PYTHON_YELLOW : PYTHON_TEAL;
    },
    [packages]
  );

  return (
    <div
      className={`relative isolate w-full overflow-hidden select-none transition-colors duration-700 ${
        isDark ? "bg-[#06080d] text-slate-300" : "bg-[#f8fafc] text-slate-700"
      } ${className}`}
      style={{ minHeight: "80vh" }}
    >
      <style>{`
        @keyframes pypiWordPulse {
          0% {
            opacity: 0;
            transform: scale(0.97);
            filter: blur(2px);
          }
          40% {
            opacity: 1;
            transform: scale(1.04);
            filter: blur(0px);
          }
          65% {
            opacity: 0.95;
            transform: scale(1.02);
            filter: blur(0px);
          }
          100% {
            opacity: 0;
            transform: scale(0.97);
            filter: blur(1px);
          }
        }
      `}</style>

      {/* Atmospheric radial mesh background */}
      <div
        className="pointer-events-none absolute inset-0 z-0 transition-opacity duration-1000"
        style={{
          background: isDark
            ? "radial-gradient(ellipse 70% 45% at 50% 0%, rgba(49, 232, 245, 0.1), transparent 70%), radial-gradient(ellipse 60% 40% at 50% 100%, rgba(251, 191, 36, 0.06), transparent 60%)"
            : "radial-gradient(ellipse 70% 45% at 50% 0%, rgba(49, 232, 245, 0.12), transparent 70%), radial-gradient(ellipse 60% 40% at 50% 100%, rgba(251, 191, 36, 0.08), transparent 60%)",
          opacity: state === "disabled" ? 0.25 : 1,
        }}
      />

      {/* Mesh Grid Matrix */}
      <div
        className={`absolute inset-0 z-0 grid w-full h-full p-4 md:p-8 ${copyVignette ? "mesh-vignette" : ""}`}
        style={{
          gridTemplateColumns: "repeat(auto-fill, minmax(115px, 1fr))",
          gridAutoRows: "minmax(42px, 1fr)",
          gap: showGridLines ? "1px" : "6px",
          background: showGridLines
            ? isDark
              ? "rgba(255, 255, 255, 0.025)"
              : "rgba(0, 0, 0, 0.035)"
            : "transparent",
        }}
        onMouseLeave={handleMouseLeave}
      >
        {packages.map((pkgName, index) => {
          const pulse = activePulses[index];
          const isHovered = hoveredIndex === index;
          const isPulsing = Boolean(pulse) && state !== "disabled";

          const currentColor = isHovered
            ? cellColor(index)
            : pulse?.color ?? cellColor(index);

          return (
            <div
              key={`${pkgName}-${index}`}
              onMouseEnter={() => handleMouseEnter(index)}
              className={`relative flex items-center justify-center p-2 text-center transition-all duration-300 ${
                showGridLines
                  ? isDark
                    ? "bg-[#06080d]/85 border border-white/[0.015]"
                    : "bg-[#f8fafc]/85 border border-black/[0.02]"
                  : ""
              }`}
            >
              {/* Only the plain word: no chips, no badges, no icons */}
              <span
                key={pulse?.key ?? "idle"}
                className={`font-mono text-[11px] md:text-xs font-semibold tracking-wider select-none ${
                  isHovered ? "scale-110 !opacity-100" : ""
                }`}
                style={{
                  color: isHovered || isPulsing ? currentColor : "transparent",
                  opacity: state === "disabled" ? 0 : isHovered ? 1 : 0,
                  textShadow:
                    (isHovered || isPulsing) && state !== "disabled"
                      ? `0 0 16px ${currentColor}80, 0 0 32px ${currentColor}30`
                      : "none",
                  animation:
                    isPulsing && !isHovered
                      ? `pypiWordPulse ${pulse!.durationMs}ms cubic-bezier(0.32, 0.72, 0, 1) forwards`
                      : "none",
                  willChange: "transform, opacity, filter",
                }}
              >
                {pkgName}
              </span>
            </div>
          );
        })}
      </div>

      {/* Children Overlay */}
      {children && (
        <div className="relative z-20 w-full min-h-[80vh] flex flex-col justify-center pointer-events-none">
          {children}
        </div>
      )}
    </div>
  );
};

export default HeroMeshGrid;
