# Landing Site Agent Guidelines (`site/landing`)

## 1. Tooling & Execution Environment
- **Task Runner:** Use `mise` / `mise.toml` as the primary project task manager.
- **Package Manager:** Direct `pnpm` (managed via `mise`). Do NOT wrap frontend/JS commands in `uv`.
  - Development: `pnpm dev` or `mise run landing:dev`
  - Production Build: `pnpm build` or `mise run landing:build`
  - Typecheck: `pnpm check`
  - Cosmos Component Explorer: `pnpm cosmos` or `mise run cosmos`
- **Python Tooling:** Use `uv` exclusively for Python tests, compiler probes, benchmarks, and ruff checks.

---

## 2. Design System Governance
- **Design Specification:** Follow `site/landing/DESIGN.md` unconditionally.
- **Frameworks:** Astro 5 + React 19 + Tailwind CSS v4 + daisyUI 5.
- **Rule of Composition:** **NO component is built outside the design system.** All components must directly use or compose daisyUI base components and Tailwind semantic design tokens.
- **Design Philosophy:** Japanese-style restraint (MUJI / Notion) paired with technical editorial precision (Together.ai / OnOrca). Off-dark/off-white neutrals, saturated Python brand accents reserved for motion, interactive states, and benchmarks.
- **Mobile First:** Every layout must adapt gracefully to mobile viewports (`390px` width) without horizontal overflow or clipped text. Verify using responsive testing scripts before opening or updating PRs.

---

## 3. i18n & Content Management
- Text messages are managed using `@inlang/paraglide-js` and stored in `site/landing/messages/en.json`.
- When adding or modifying user-facing copy, maintain ICU-compliant paraglide messages.
- Spanish origin tag line is standardized as: `Made with ❤️ from Spain`.

---

## 4. Verification Protocol
Before pushing commits or modifying pull requests:
1. Run `pnpm build` in `site/landing` (must pass with 0 errors).
2. Verify visual output across desktop (`1440x900`) and mobile (`390x844`).
3. Commit using atomic conventional commits (`feat(site): ...`, `fix(site): ...`).
4. **NO co-author trailers on commits.**
