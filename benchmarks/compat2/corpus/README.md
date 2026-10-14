# compat2 corpus

Real program corpus for scorer v2: pinned pure Python packages, per function scoring spec, and a scripts corpus. Loaded as `Probe` objects by `load.py`.

| Piece | Where | Count (visible / holdout) |
| --- | --- | --- |
| Package pins (v1 plus new) | `pins.json`, `holdout/pins.json` | 18 v1 + 21 new / 5 new |
| Closed signature functions | `package_spec.json` | 55 / 0 |
| Scripts | `scripts/`, `holdout/scripts/` | 20 / 5 |

Probes: 114 visible (20 script programs, 39 package programs, 55 package functions), 10 holdout.

## Files

- `pin.py`: fetches sdists from PyPI, checks sha256, extracts into a fresh temp dir with the `data` tar filter, copies the importable tree, the upstream tests and the license. Never imports or runs the fetched code.
- `spec.py`: builds `package_spec.json` with `ast` only. A function is closed when it is public, top level, undecorated, not async or a generator, has no varargs or keyword only parameters, and every parameter and the return are annotated `int`, `str` or `bool`. This is the `_closed_args` rule of the frozen v1 scorer applied to source, so packages that still have fences can be listed. `oracles` lists the upstream tests and which closed functions they call by name.
- `load.py`: the Probe loader and its conventions (see its docstring). The holdout is only opened with `holdout=True`.
- `src/`, `tests/`, `licenses/`: vendored sources, upstream tests and licenses of the new pins. v1 pins stay in `../../compat/top100/` (frozen) and are referenced by relative `tree` paths.

Upstream tests are untrusted and Linux container only. Single file modules (`six.py`, `typing_extensions.py`) are pinned as files; v1 `classify_tree` only accepts directories, so it reports them as unattempted.

## Baseline at snapshot 046 (v1 `classify_tree`, 2026-10-14)

None of the 24 new visible and held out directory packages reaches `subset` or `partial`: all are `fenced`, most at PS2002 (closed import table, decorators, class bases, class body assignments, star imports) and a few at PS1001 (unannotated parameters). None of the 25 scripts compiles: the compiler only lowers module level definitions and constants, so the first module level `for` or call statement is fenced. The corpus therefore starts at zero, which is the intended signal.

## Applies to

Checkpoints `top100` (categories `program`, `function`) and `scripts` (category `program`) of compat2. The holdout rules are in `holdout/README.md`.

## References

Kernels follow pyperformance and Benchmarks Game algorithms and Shed Skin and Codon style examples. They are original code, MIT licensed, with no upstream source copied. See `scripts/*/meta.json`.
