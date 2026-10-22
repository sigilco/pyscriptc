import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";

// The landing page of the single pyscriptc site. Served from the deploy root,
// so the base is "/". The build output is `dist/` inside this directory; the
// site assembly copies it to the root of `deploy/`.
export default defineConfig({
  outDir: "dist",
  base: "/",
  publicDir: "../../docs/assets",
  vite: {
    plugins: [tailwindcss()],
  },
});
