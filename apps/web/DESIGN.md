## pyscriptc Design System Specification & Governance

## 1. Philosophy: Restraint-First Editorial Precision

The pyscriptc landing design language is guided by **restraint-first, editorial precision**: a quiet, near-black obsidian canvas, deliberate use of a single interactive accent, and typography that carries the narrative.

### Core Pillars
1. **Quiet Canvas, One Accent:** 90% of the canvas is deep obsidian neutrals (`#07080a`, `#0e1013`) in dark mode and cool off-white tones (`#fafafa`, `#ffffff`) in light mode. The only chromatic accent is the logo's deep blue (`#4d80c4` dark, `#1e5aa8` light). The logo's neon teal and the Python yellow are reserved for the hero motion layer and appear nowhere else.
2. **High-Conviction Typography:** Crisp contrast using sans-serif system typography for narrative copy paired with precise monospace for compiler flags, file names, benchmarks, and diagnostic logs.
3. **No Decorative Gimmicks:** Fake browser window chrome (colored window dots, artificial close buttons) are forbidden. Only functional chrome (minimal tab indicators `>_ filename.py`, sliding terminal runners) is permitted.
4. **Authentic Motion:** Animations serve architectural comprehension, not entertainment. Slow organic breathing (3.5s - 5.0s cycles) for background mesh grids; staggered 2-stage typing/terminal execution; smooth auto-looping carousels with hover-pause.

---

## 2. Design System Tokens & Base Architecture

The UI is built on **daisyUI 5** powered by **Tailwind CSS v4** (`@plugin "daisyui"` in `src/styles/global.css`).

### Token Source of Truth

All design tokens live in **`src/styles/theme.css`**, which is the single source of truth for color and typography variables. `src/styles/global.css` imports it first (`@import "./theme.css";`, before `@import "tailwindcss";`) and keeps only the box-sizing reset, the `html`/`body` rules, the global focus ring and the local utility classes (`.glow-quiet`, `.faint-word`, `.dot-grid`, `.pulse-anim`, plus the reduced-motion guard).

`src/styles/compat.css` reads the same tokens, so the compat page and the landing page cannot drift apart. The docs theme (`docs/.docmd/theme.css`) maps the docmd variables onto the same values.

Tokens are declared on `:root` (dark is the default) and overridden on `[data-theme="light"]`.

### Token Inventory

| Variable | Dark | Light | Role |
| --- | --- | --- | --- |
| `--color-bg-base` | `#07080a` | `#fafafa` | Page canvas |
| `--color-bg-surface` | `#0e1013` | `#ffffff` | Elevated surfaces, cards |
| `--color-bg-card` | `rgba(22, 25, 30, 0.72)` | `rgba(255, 255, 255, 0.88)` | Translucent card fill |
| `--color-border` | `rgba(255, 255, 255, 0.09)` | `rgba(15, 18, 25, 0.1)` | Hairline borders |
| `--color-border-strong` | `rgba(255, 255, 255, 0.18)` | `rgba(15, 18, 25, 0.2)` | Hairlines that need more presence |
| `--color-text-primary` | `#f2f4f7` | `#101828` | Headings, primary copy |
| `--color-text-secondary` | `#9aa3b2` | `#475467` | Supporting copy |
| `--color-text-muted` | `#7c8798` | `#667085` | Tertiary, metadata |
| `--color-accent` | `#4d80c4` | `#1e5aa8` | The one interactive accent |
| `--color-accent-strong` | `#6c9bd8` | `#174b9a` | Accent hover / emphasis |
| `--color-accent-soft` | `rgba(77, 128, 196, 0.14)` | `rgba(30, 90, 168, 0.1)` | Accent washes and code chips |
| `--color-accent-border` | `rgba(77, 128, 196, 0.38)` | `rgba(30, 90, 168, 0.32)` | Accent hairline borders |
| `--color-accent-slate` | `#b4bec8` | `#334155` | Neutral accent, the logo's cool grey |
| `--color-status-full` | `#3fb950` | `#1a7f37` | Support state: full |
| `--color-status-partial` | `#d29922` | `#9a6700` | Support state: partial |
| `--color-status-none` | `#f85149` | `#cf222e` | Support state: none |
| `--motion-teal` | `#31e8f5` | `#0e7c86` | Hero motion layer only |
| `--motion-yellow` | `#fbbf24` | `#d97706` | Hero motion layer only |
| `--font-sans` | system sans stack | same | Narrative copy |
| `--font-mono` | system monospace stack | same | Code, metrics, diagnostics |

Keyframes (`pulse-anim`, `progress-up-down`, `progress-ltr`) are also defined in `theme.css`.

### Where the Accent Comes From

The logo (`public/logo-512.png`) is a teal-and-blue snake. Its dominant colors, measured from the PNG, are:

| Color | Role in the logo | Use in the UI |
| --- | --- | --- |
| `#31e8f5` | the bright teal of the head | Hero motion layer only |
| `#174b9a`, `#1e61a8` | the deep blue of the body | **The accent** (`--color-accent`) |
| `#3885b1` | the mid blue of the transitions | motion layer only |
| `#b4bec8`, `#7a8ca0` | the cool greys of the trails | `--color-accent-slate`, ink ladder |

The accent is the deep blue family, desaturated into ink. The bright teal is the one color that reads as neon, so it never touches chrome.

### Color Policy

- **Neutral canvas:** the page ground is obsidian (`#07080a` / `#0e1013`) with a slate ink ladder. Neutrals carry almost all of the surface area.
- **One accent:** `--color-accent` is the only chromatic color used for interactive chrome (links, focus rings, active states, the compat headline stat). There is no second accent and no accent ramp beyond `accent` / `accent-strong` / `accent-soft` / `accent-border`.
- **No glow:** no colored `box-shadow` anywhere in the chrome. The only shadow utility is `.glow-quiet`, a neutral halo for the landing hero.
- **Status color is data, never decoration:** `--color-status-full`, `--color-status-partial` and `--color-status-none` mark the three compat support states. They appear on a dot, a badge or a cell, never on a large text block or a wide surface. They are the muted Primer steps, not the neon Tailwind 500 tier.
- **Motion layer:** `--motion-teal` and `--motion-yellow` are confined to the landing hero mesh (`HeroMeshGrid`). Nothing else may use them.
- **Banned:** `#00c2ff`, `#22d3ee`, `#0284c7`, `#38bdf8`, `#a5f3fc`, and every Tailwind `cyan-*` / `sky-*` class. A grep for those strings over `apps/web/src` and `docs/.docmd` must come back empty.
- Arbitrary hex values in components must map to a token above; ad-hoc hex outside the motion layer is rejected in review.

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
  - Sits on `backdrop-blur-xl bg-[#0e1013]/90 border border-white/10` (the token value, inlined because Tailwind arbitrary values cannot read a CSS variable).
  - Shared by the landing page and the compat page, so the two read as one site.
- **Metric Cards (`BenchmarkRow`):**
  - Prominent metric value positioned at the **top** of the card in high-contrast typography (`text-5xl sm:text-6xl font-black`).
  - Narrative explanation anchored at the **bottom** (`text-xs sm:text-sm font-mono opacity-90`).
  - Tinted surface background using neutral washes over `--color-bg-surface`.
- **Interactive Carousels (`LumioCarousel`, `IsometricSurfaceStack`):**
  - Continuous auto-looping on a predictable interval (~7.5s - 8s).
  - Explicit hover-pause behavior (`onMouseEnter={() => setIsPaused(true)}`).
  - Clear stepped progress indicators and active highlight states.
- **Terminal Chrome (terminal drawer pattern):**
  - Minimal top tab bar (`>_ filename.py`) with CPython 3.14 target note.
  - Phase 1: Code typewriter syntax rendering.
  - Phase 2: Popping slide-down terminal drawer executing `$ pyscriptc compile` with stdout.

---

## 4. The Social Card

The Open Graph card is `public/og-image.png`, 1200x630, rendered from `og/og-image.html` by `og/render.mjs` (`node og/render.mjs`). The template inlines the site tokens, so the card follows the palette; the render script fails if the card is not exactly 1200x630 or if any block overflows. `og/README.md` has the regeneration steps.

The card carries the logo mark, the wordmark, the pitch "Compile annotated Python to a native binary.", a terminal snippet with the compile command and its output, and the site URL. Every page of the site (`/`, `/compat/`, `/docs/`) points at the same image with `og:image`, `og:image:width`, `og:image:height`, `og:image:alt` and `twitter:card=summary_large_image`.
