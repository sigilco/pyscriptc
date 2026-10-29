// Render the Open Graph social preview to apps/web/public/og-preview.png.
//
// Uses playwright-cli (the project's browser QA tool) to screenshot the
// 1774x887 render target at exact dimensions, then downscales a 360px-wide
// evidence preview with macOS `sips` (no new deps). The render target is
// served by intercepting an http://og.local/ route in the page, because
// playwright-cli blocks the file: protocol.
//
//   pnpm --dir apps/web run og:render
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const ogDir = resolve(here, "..", "og");
const outPng = resolve(here, "..", "public", "og-preview.png");
const downscale = join(ogDir, "og-preview-360.png");
const html = join(ogDir, "og-preview.html");

if (!existsSync(html)) {
  console.error(`og:render: missing ${html}`);
  process.exit(1);
}

function cli(args: string[]): string {
  return execFileSync("playwright-cli", args, { encoding: "utf8" });
}

const steps: Array<[string, () => void]> = [
  [
    "open browser",
    () => cli(["-s=ogpreview", "open", "--browser=chrome", "about:blank"]),
  ],
  [
    "set viewport 1774x887",
    () => cli(["-s=ogpreview", "resize", "1774", "887"]),
  ],
  [
    "navigate to og-preview.html",
    () =>
      cli([
        "-s=ogpreview",
        "run-code",
        `async page => { await page.route("http://og.local/**", route => route.fulfill({ body: ${JSON.stringify(readFileSync(html, "utf8"))}, contentType: "text/html" })); await page.goto("http://og.local/og-preview.html"); }`,
      ]),
  ],
  [
    "screenshot og-preview.png",
    () => {
      mkdirSync(dirname(outPng), { recursive: true });
      cli(["-s=ogpreview", "screenshot", `--filename=${outPng}`]);
    },
  ],
  ["close browser", () => cli(["-s=ogpreview", "close"])],
  [
    "downscale 360px evidence preview",
    () =>
      execFileSync("sips", ["-z", "181", "360", outPng, "--out", downscale], {
        encoding: "utf8",
      }),
  ],
];

for (const [label, fn] of steps) {
  try {
    fn();
    console.log(`ok: ${label}`);
  } catch (err) {
    console.error(`og:render: ${label} failed: ${(err as Error).message}`);
    process.exit(1);
  }
}

const bytes = statSync(outPng).size;
const kb = (bytes / 1024).toFixed(1);
console.log(`og:render: ${outPng} (${kb} KB)`);
console.log(`og:render: ${downscale}`);
if (bytes > 200 * 1024) {
  console.error("og:render: output exceeds 200KB; raise screenshot compression");
  process.exit(1);
}
