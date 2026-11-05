// Terminal card: the real transcript of demo/session.cast (asciinema), ANSI stripped.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { here, render } from "./lib.mjs";

const lines = readFileSync(join(here, "demo/session.cast"), "utf8").trim().split("\n").slice(1).map((l) => JSON.parse(l)).filter((e) => e[1] === "o");
const text = lines.map((e) => e[2]).join("").replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, "").replace(/\r/g, "").trimEnd();
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
const html = text.split("\n").map((l) => (l.startsWith("$ ") ? `<span class="p">$</span> ${esc(l.slice(2))}` : `<span class="o">${esc(l)}</span>`)).join("\n");
await render({ terminal: `<div class="term">${html}</div>` });
