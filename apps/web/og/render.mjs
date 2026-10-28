// Render apps/web/og/og-image.html to apps/web/public/og-image.png.
//
//   pnpm --dir apps/web run og:image
//
// Uses playwright-cli against the system Chrome: same template, same bytes,
// every time. The template is a static HTML file with the site tokens inlined,
// so the card can be regenerated years from now without a browser driver in
// the repo. No dependency is added to the web app.
//
// The size check fails the script if the render is not exactly 1200x630, so a
// template edit cannot silently ship a wrong-sized card.
//
// The HTTP server lives in this process, so the playwright calls must be
// awaited, never execFileSync: a blocking call would freeze the event loop
// and the browser would never get a response.
import { execFile } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, "..");
const template = join(webRoot, "og", "og-image.html");
const out = join(webRoot, "public", "og-image.png");

const WIDTH = 1200;
const HEIGHT = 630;
const SESSION = "ogrender";

function fail(message) {
  console.error(`og:image: ${message}`);
  process.exit(2);
}

async function pw(...args) {
  try {
    const { stdout } = await run("playwright-cli", [`-s=${SESSION}`, ...args], {
      maxBuffer: 8 * 1024 * 1024,
    });
    return stdout;
  } catch (error) {
    const detail = [error.stdout, error.stderr].filter(Boolean).join("\n").trim();
    fail(`playwright-cli ${args[0]} failed\n${detail}`);
  }
}

if (!existsSync(template)) fail(`missing template ${template}`);

// Serve the web app root so the template and the logo asset both resolve
// over http. The template references the logo as /public/logo-512.png.
const server = createServer((req, res) => {
  const requestUrl = new URL(req.url, "http://localhost");
  const rel =
    requestUrl.pathname === "/og-image.html"
      ? "og/og-image.html"
      : requestUrl.pathname.replace(/^\/+/, "");
  const file = join(webRoot, rel);
  if (!file.startsWith(webRoot) || !existsSync(file)) {
    res.writeHead(404);
    res.end("not found");
    return;
  }
  const body = readFileSync(file);
  const type = file.endsWith(".html") ? "text/html" : "image/png";
  res.writeHead(200, { "content-type": type });
  res.end(body);
});

await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const port = server.address().port;
const url = `http://127.0.0.1:${port}/og-image.html`;

const tmp = mkdtempSync("/tmp/og-render-");
const shot = join(tmp, "og-image.png");

try {
  await pw("open", url);
  await pw("resize", String(WIDTH), String(HEIGHT));
  await pw("eval", "document.fonts.ready.then(() => true)");

  // Overflow guard: the card is a fixed 630px box, so content taller than the
  // box is silently clipped. Measure the content column and fail loudly.
  const metrics = await pw(
    "eval",
    `(() => {
      const card = document.getElementById("og-card");
      const box = card.getBoundingClientRect();
      let overflow = 0;
      for (const el of card.querySelectorAll(".masthead, .pitch, .terminal, .footer")) {
        const r = el.getBoundingClientRect();
        overflow = Math.max(overflow, Math.round(r.bottom - box.bottom));
      }
      return JSON.stringify({
        height: Math.round(box.height),
        scrollHeight: card.scrollHeight,
        overflow,
      });
    })()`,
  );
  const match = metrics.match(/"(\{[^\n]*?\})"/s);
  if (!match) fail(`could not read the card metrics from playwright-cli:\n${metrics}`);
  const { height, scrollHeight, overflow } = JSON.parse(
    match[1].replace(/\\"/g, '"').replace(/\\\\/g, "\\"),
  );
  if (height !== HEIGHT) fail(`the card rendered at ${height}px, expected ${HEIGHT}px`);
  if (scrollHeight > HEIGHT + 1) {
    fail(`the card content is ${scrollHeight}px tall, over the ${HEIGHT}px card; trim the template`);
  }
  if (overflow > 1) fail(`a card block overflows the bottom by ${overflow}px; trim the template`);

  await pw("screenshot", "#og-card", `--filename=${shot}`);
} finally {
  try {
    await pw("close");
  } catch {
    // the browser session may already be gone; the render is what matters
  }
  server.close();
}

if (!existsSync(shot)) fail("playwright-cli did not write the screenshot");

const bytes = statSync(shot).size;
if (bytes < 20_000) fail(`screenshot suspiciously small (${bytes} bytes); PNG header likely broken`);
if (bytes > 900_000) fail(`screenshot too large for a social card (${bytes} bytes); compress the template`);

rmSync(out, { force: true });
writeFileSync(out, readFileSync(shot));
rmSync(tmp, { recursive: true, force: true });

console.log(`og:image: wrote ${out} (${bytes} bytes, ${WIDTH}x${HEIGHT})`);
