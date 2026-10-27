# pyscriptc Design System Specification & Governance

## 1. Philosophy: Restraint-First Editorial Precision

The pyscriptc landing design language is guided by **restraint-first, editorial precision**: a quiet, near-black obsidian canvas, deliberate use of a single interactive accent, and typography that carries the narrative.

### Core Pillars
1. **Quiet Canvas, Intentional Accents:** 90% of the canvas consists of deep obsidian neutrals (`#06080d`, `#0b0f19`) in dark mode and cool off-white tones (`#f8fafc`, `#ffffff`) in light mode. Saturated Python brand colors (Cyan `#00c2ff`, Sky `#38bdf8`, Gold `#fbbf24`) are strictly reserved for motion, glows, and animation visuals.
2. **High-Conviction Typography:** Crisp contrast using sans-serif system typography for narrative copy paired with precise monospace for compiler flags, file names, benchmarks, and diagnostic logs.
3. **No Decorative Gimmicks:** Fake browser window chrome (colored window dots, artificial close buttons) are forbidden. Only functional chrome (minimal tab indicators `>_ filename.py`, sliding terminal runners) is permitted.
4. **Authentic Motion:** Animations serve architectural comprehension, not entertainment. Slow organic breathing (3.5s - 5.0s cycles) for background mesh grids; staggered 2-stage typing/terminal execution; smooth auto-looping carousels with hover-pause.

---

## 2. Design System Tokens & Base Architecture

The UI is built on **daisyUI 5** powered by **Tailwind CSS v4** (`@plugin "daisyui"` in `src/styles/global.css`).

### Token Source of Truth

All design tokens live in **`src/styles/theme.css`**, which is the single source of truth for color and typography variables. `src/styles/global.css` imports it first (`@import "./theme.css";`, before `@import "tailwindcss";`) and keeps only the box-sizing reset, `html`/`body` rules, and the local utility classes (`.glow-cyan`, `.glow-yellow`, `.faint-word`, `.dot-grid`, `.pulse-anim`, plus the reduced-motion guard).

Tokens are declared on `:root` (dark is the default) and overridden on `[data-theme="light"]`.

### Token Inventory

| Variable | Dark | Light | Role |
| --- | --- | --- | --- |
| `--color-bg-base` | `#06080d` | `#f8fafc` | Page canvas |
| `--color-bg-surface` | `#0b0f19` | `#ffffff` | Elevated surfaces, cards |
| `--color-bg-card` | `rgba(17, 22, 37, 0.75)` | `rgba(255, 255, 255, 0.85)` | Translucent card fill |
| `--color-border` | `rgba(255, 255, 255, 0.08)` | `rgba(15, 23, 42, 0.08)` | Hairline borders |
| `--color-text-primary` | `#ffffff` | `#0f172a` | Headings, primary copy |
| `--color-text-secondary` | `#94a3b8` | `#475569` | Supporting copy |
| `--color-text-muted` | `#64748b` | `#94a3b8` | Tertiary, metadata |
| `--color-accent-cyan` | `#00c2ff` | `#0284c7` | Primary interactive accent |
| `--color-accent-azure` | `#0284c7` | `#0369a1` | Secondary interactive accent |
| `--color-accent-slate` | `#cbd5e1` | `#334155` | Neutral accent |
| `--glow-blue` | `#38bdf8` | `#0284c7` | Animation / glow only |
| `--glow-yellow` | `#fbbf24` | `#d97706` | Animation / glow only |
| `--font-sans` | system sans stack | same | Narrative copy |
| `--font-mono` | system monospace stack | same | Code, metrics, diagnostics |

Keyframes (`pulse-anim`, `progress-up-down`, `progress-ltr`) are also defined in `theme.css`.

### Color Policy

- **Neutral canvas:** the page ground is obsidian (`#06080d` / `#0b0f19`) with slate text hierarchy. Neutrals carry almost all of the surface area.
- **Cyan / azure:** reserved for interactive elements, links, focus states, primary calls to action, and progress fills that represent the native output path.
- **Emerald:** reserved exclusively for success and diagnostic-pass states (`text-emerald-400`).
- **Yellow (`#fbbf24`) and sky blue (`#38bdf8`):** the Python logo colors. They are reserved exclusively for animation and glow visuals (hero mesh glows, motion art, progress fills). They must not be used as a UI accent on static chrome, icons, or badges.
- Arbitrary hex values in components must map to a token above; ad-hoc hex outside the animation layer is rejected in review.

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
  - Sits on `backdrop-blur-xl bg-[#0b0f19]/90 border border-white/10`.
- **Metric Cards (`MetricsCardsWithBenchmark`):**
  - Prominent metric value positioned at the **top** of the card in high-contrast typography (`text-5xl sm:text-6xl font-black`).
  - Narrative explanation anchored at the **bottom** (`text-xs sm:text-sm font-mono opacity-90`).
  - Tinted surface background using cyan-tinted washes (`bg-[#0b1924]/80`, `bg-[#101524]/80`, `bg-[#0f1320]/80`).
- **Interactive Carousels (`LumioCarousel`, `IsometricSurfaceStack`):**
  - Continuous auto-looping on a predictable interval (~7.5s - 8s).
  - Explicit hover-pause behavior (`onMouseEnter={() => setIsPaused(true)}`).
  - Clear stepped progress indicators and active highlight states.
- **Terminal Chrome (terminal drawer pattern):**
  - Minimal top tab bar (`>_ filename.py`) with CPython 3.14 target note.
  - Phase 1: Code typewriter syntax rendering.
  - Phase 2: Popping slide-down terminal drawer executing `$ pyscriptc compile` with stdout.
