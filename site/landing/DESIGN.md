# pyscriptc Design System Specification & Governance

## 1. Philosophy: Japanese-Style Restraint & Editorial Precision

The pyscriptc brand and landing design language is guided by **MUJI-inspired restraint** combined with **modern technical editorial precision** (influenced by Notion, Together.ai, and OnOrca).

### Core Pillars
1. **Quiet Canvas, Intentional Accents:** 90% of the canvas consists of deep obsidian/slate neutrals (`#090b10`, `#0b0c10`, `#101114`) in dark mode and warm off-white tones (`#fbf9f5`, `#f7f7f5`) in light mode. Saturated Python brand colors (Cyan `#00c2ff`, Sky `#38bdf8`, Gold `#fbbf24`) are strictly reserved for motion, interactive states, diagnostic highlights, and data benchmarks.
2. **High-Conviction Typography:** Crisp contrast using sans-serif system typography for narrative copy paired with precise monospace (`JetBrains Mono`, `Fira Code`, system monospace) for compiler flags, file names, benchmarks, and diagnostic logs.
3. **No Decorative Gimmicks:** Fake browser window chrome (colored window dots, artificial close buttons) are forbidden. Only functional chrome (minimal tab indicators `>_ filename.py`, sliding terminal runners) is permitted.
4. **Authentic Motion:** Animations serve architectural comprehension, not entertainment. Slow organic breathing (3.5s - 5.0s cycles) for background mesh grids; staggered 2-stage typing/terminal execution; smooth auto-looping carousels with hover-pause.

---

## 2. Design System Tokens & Base Architecture

The UI is built on **daisyUI 5** powered by **Tailwind CSS v4** (`@plugin "daisyui"` in `src/styles/global.css`).

### Semantic Color Tokens
- **Canvas Base:**
  - Dark: `bg-[#0b0c10]` (primary ground), `bg-[#090b10]` (elevated surfaces), `bg-[#121624]` (active plates).
  - Light: `bg-[#fbf9f5]` (primary ground), `bg-white` (elevated surfaces).
- **Text Hierarchy:**
  - Primary: `text-white` / `text-slate-100` (Light: `text-slate-900`)
  - Secondary: `text-slate-300` / `text-slate-400` (Light: `text-slate-600`)
  - Tertiary / Monospace: `text-slate-500`
- **Functional Accents (CPython / Python Palette):**
  - **Flagship Native / Primary:** Cyan (`#00c2ff` / `text-cyan-400` / `bg-cyan-400`)
  - **Shared Library / Secondary:** Sky Blue (`#38bdf8` / `text-sky-400`)
  - **CPython Island / Safety Net:** Python Gold / Amber (`#fbbf24` / `text-amber-400`)
  - **Diagnostics Success:** Emerald (`#34d399` / `text-emerald-400`)
  - **Spain Origin Heart:** Crimson (`text-rose-500`)

---

## 3. Component Composition Rules

> [!IMPORTANT]
> **Strict Governance:** No UI component may be constructed outside the design system. Every visual element must either:
> 1. Use a standard daisyUI base component / utility class (`btn`, `card`, `badge`, `modal`, `tabs`, `progress`), OR
> 2. Compose daisyUI base styles and semantic Tailwind tokens. Ad-hoc arbitrary inline hex hacks without token mapping are rejected in code review.

### Standard Component Patterns
- **Floating Pill Capsule Navigation (`HeaderPillNav`):**
  - Floating capsule anchored with `fixed top-5 left-0 right-0 z-50 flex justify-center`.
  - Inlined links on desktop; minimal `···` breadcrumb menu trigger on mobile viewports.
  - Sits on `backdrop-blur-xl bg-[#101116]/90 border border-white/10`.
- **Metric Cards (`MetricsCardsWithBenchmark`):**
  - Prominent metric value positioned at the **top** of the card in high-contrast typography (`text-5xl sm:text-6xl font-black`).
  - Narrative explanation anchored at the **bottom** (`text-xs sm:text-sm font-mono opacity-90`).
  - Tinted surface background using complementary accent washes (`bg-[#0b1924]/80`, `bg-[#101524]/80`, `bg-[#1c180e]/80`).
- **Interactive Carousels (`LumioCarousel`, `IsometricSurfaceStack`):**
  - Continuous auto-looping on a predictable interval (~7.5s - 8s).
  - Explicit hover-pause behavior (`onMouseEnter={() => setIsPaused(true)}`).
  - Clear stepped progress indicators and active highlight states.
- **Terminal Chrome (OnOrca Pattern):**
  - Minimal top tab bar (`>_ filename.py`) with CPython 3.14 target note.
  - Phase 1: Code typewriter syntax rendering.
  - Phase 2: Popping slide-down terminal drawer executing `$ pyscriptc compile` with stdout.
