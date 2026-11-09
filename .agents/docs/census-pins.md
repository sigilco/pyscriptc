# Census pins (2026-11-04, refs #817; sharded 2026-11-06)

`tests/test_census_self.py` used to pin one global fence number and one exact
module count, so every PR that moved any fence or added any module conflicted
with every other PR touching `src/pyscriptc`. It now pins fences per module,
stored as one JSON shard per module under `tests/census_pins/`.

## How it works

- `tests/census_pins/<module>.json` holds a single
  `{"<module>": <fence ceiling>}` entry, where the module is fully qualified
  (`pyscriptc.lower._expr`, package inits without `.__init__`) and the file
  name is the module name plus `.json`. One file per module means a repin
  rewrites only the shards whose measured count moved, so PRs lowering
  fences in different modules never share a file.
- `scripts/repin_census.py` is the only writer. `load_pins()` in the same
  file aggregates the shards for `tests/test_census_self.py`; it refuses a
  shard whose key does not match its file name.
- The test asserts each module's measured fences are at or under its pinned
  ceiling, asserts the census total is at most the sum of the pins (upper
  bound only), keeps the line-ceiling assertion, and makes the module count a
  +/-3 range around the pins module count.
- A module the census measures but the pins shards do not list must measure
  0 fences; since the shards are generated from the census, a missing key
  with fences means the file was edited by hand.

## Repinning

Regenerate the shards from the current tree with:

```
uv run python scripts/repin_census.py
```

It runs the same census the test uses
(`benchmarks.compat2.census_self`), joins the per-package fence counts with
the discovered module units (the census labels packages with bare stems, so
the join restores the dotted names), writes only the shards that changed,
and deletes shards for modules that no longer exist. `git status` after a
repin shows exactly the modules your change moved; anything else dirty is
not yours.

When a PR legitimately raises a module's fences, run the script, commit the
shard diffs in the same PR, and take master's version on conflict before
re-pinning. `scripts/rebase_helper.py` automates the take-master-then-repin
flow for census shards and `data/compat/support.json`; see
`.agents/docs/parallel-pr-rules.md`.
