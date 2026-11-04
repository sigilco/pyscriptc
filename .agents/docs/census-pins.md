# Census pins (2026-11-04, refs #817)

`tests/test_census_self.py` used to pin one global fence number and one exact
module count, so every PR that moved any fence or added any module conflicted
with every other PR touching `src/pyscriptc`. It now pins fences per module,
stored in `tests/census_pins.json`.

## How it works

- `tests/census_pins.json` maps one fully qualified module name
  (`pyscriptc.lower._expr`, package inits without `.__init__`) to its
  measured fence count. Keys are sorted, one per line, so PRs touching
  different modules merge without conflicts.
- The test asserts each module's measured fences are at or under its pinned
  ceiling, asserts the census total is at most the sum of the pins (upper
  bound only), keeps the line-ceiling assertion, and makes the module count a
  +/-3 range around the pins file length.
- A module the census measures but the pins file does not list must measure
  0 fences; since the pins file is generated from the census, a missing key
  with fences means the file was edited by hand.

## Repinning

Regenerate the pins from the current tree with:

```
uv run python scripts/repin_census.py
```

It runs the same census the test uses
(`benchmarks.compat2.census_self`), joins the per-package fence counts with
the discovered module units (the census labels packages with bare stems, so
the join restores the dotted names) and rewrites `tests/census_pins.json`
sorted, one line per module.

When a PR legitimately raises a module's fences, run the script, commit the
pins diff in the same PR, and take master's version on conflict before
re-pinning.
