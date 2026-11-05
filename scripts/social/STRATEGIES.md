# Social content strategies

Source of truth for how pyscriptc media is chosen. Output (`out/`) is gitignored; this file and the generators are committed.

## Rules for every piece

- Numbers come from a recorded run at v0.7.0 or later, with the version, machine and date on the card. Nightly CI records compat scores and the boxed ledger, not performance, so benchmark cards need a fresh `benchmarks.baseline.run` on a named machine.
- Workloads are chosen for how common they are in real scripts, never by best ratio. Synthetic micro kernels (for example `ssl-const`) are not headline material.
- A claim that holds only for some workloads says so on the card.
- Terminal content prefers real captured output. A scripted replay is allowed where a capture is impractical. Which one was used is recorded in the PR or message, never written in the content.
- Copy is first person, honest about limits, no emdashes, none of the banned words in `_own/AGENTS.md`.
- Drafts only. Nothing is posted without explicit approval.

## Strategies (tag each piece with one)

1. `win-spread`: geomean plus the full spread across a curated realistic set, including the ties and losses.
2. `real-script`: a recognisable task (log parser, CSV pass, JSON munging, CLI tool) compiled and timed against CPython, with the source shown.
3. `tradeoff`: a case where speed is not a clear win (for example a short script dominated by startup, or boxed fallback code) compared on packaging instead: single binary, no interpreter to install, size, cold start.
4. `compile-and-run`: terminal card or recording of `pyscriptc run` then `build -o` then the binary.
5. `boundary`: a type or divergence error caught at compile time, framed as what the compiler refuses and why.
6. `progress`: release-over-release deltas (same workload, v0.6.x to v0.7.x to v0.8.x), only when every bar is true.
7. `seam`: how it works, the pipeline from typed HIR to LLVM to binary, one diagram per post.

## Formats

- Stills: 1080x1080 PNG for X and LinkedIn, rendered from HTML by `cards.mjs`.
- Motion: terminal recordings with asciinema, converted with `agg` to GIF, or hyperframes for composed video. Same tokens as the site.
- Each piece lists its strategy tag, data source file and commit, and whether terminal content was captured or scripted.
