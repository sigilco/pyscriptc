# Holdout

About 20% of the corpus (5 of 25 new packages, 5 of 25 new scripts) lives here and nowhere else. The v1 packages are all visible.

Rules:

- Scored only at epic boundaries, never per iteration.
- Must never be shown to the generator agent: not the sources, the expected output, the pins or the per program results. Only aggregate numbers may be reported to it.
- A visible score that rises while the holdout score stays flat flags gaming of the visible corpus.
- Move a program in or out of the holdout only by a commit that says why, and re-pin it in the same commit.

`pins.json` here lists the held out packages with their sha256. `scripts/` holds the held out programs. `load.py --holdout` is the only supported way to load them.
