import { defineConfig } from "astro/config";

// The landing page of the single pyscriptc site. Served from the deploy root,
// so the base is "/". The build output is `dist/` inside this directory; the
// site assembly copies it to the root of `deploy/`.
export default defineConfig({
  outDir: "dist",
  base: "/",
  // Shared repo assets live at the repo root (docs/assets); the fallback
  // landing in scripts/build_site.py mirrors them the same way.
  publicDir: "../../docs/assets",
});
