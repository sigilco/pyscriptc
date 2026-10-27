import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";
import { paraglideVitePlugin } from "@inlang/paraglide-js";
import react from "@astrojs/react";

// The single pyscriptc site app: the landing page at base "/" and the compat
// page at base "/compat" (src/pages/compat/). The build output is `dist/`
// inside this directory; scripts/assemble.mts copies it to the root of
// `deploy/` together with the docs build.
export default defineConfig({
  outDir: "dist",
  base: "/",
  integrations: [react()],
  vite: {
    plugins: [
      paraglideVitePlugin({
        project: "./project.inlang",
        outdir: "./src/paraglide",
      }),
      tailwindcss(),
    ],
  },
});
