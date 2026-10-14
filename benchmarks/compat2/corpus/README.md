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

Checkpoints `top100` (categories `program`, `function`) and `scripts` (category `program`) of compat2. The holdout rules are in `holdout/README.md`; how it is kept closed is in the next section.

## Keeping the holdout closed

The holdout is committed, so the workflow is reproducible and the evaluator needs no secret. Generator agents are kept out of it in layers. The evaluator is a session or CI job started with `PYSCRIPTC_EVALUATOR=1`; nobody else gets that flag.

| Layer | What it does | Where |
| --- | --- | --- |
| Loader and scorer gate | `corpus.load(holdout=True)` raises and `score --holdout` exits 2 unless the flag is set | `load.py`, `../score.py` |
| Aggregate only output | `--holdout` prints counts and the weighted pass only, and never uses the shared cache | `../score.py` |
| Container mask | `run.sh` mounts an empty tmpfs over the holdout directory unless the flag is set | `.agents/docker/run.sh` |
| Claude Code deny rules | `Read`, `Edit` and `Write` of the holdout path are denied | `.claude/settings.json` |
| PreToolUse hook | blocks tool calls on the holdout path, and Bash commands that mention it, the flag, or `git cat-file`, `ls-tree`, `archive` | `.claude/hooks/holdout_guard.py` |
| Search ignore | `.ignore` hides the directory from ripgrep, so Grep and Glob skip it | `/.ignore` |
| Sparse worktrees | agent worktrees are created without the holdout on disk | `.agents/bin/agent-checkout` |

Limits, stated plainly. This stops accidental and incentive driven reads by cooperating agents. It is not a sandbox against a hostile one: `git log -p` or `git show` on a commit that added a holdout file would still print it, and shell obfuscation can dodge the Bash pattern. If that matters, encrypt the holdout at rest and keep the key only in the evaluator's keychain.

Tests that read holdout contents skip themselves when the holdout is closed, so the agent suite stays green. The evaluator runs them with the flag set:

```
PYSCRIPTC_EVALUATOR=1 .agents/docker/run.sh uv run pytest -q tests/test_compat2_corpus.py
PYSCRIPTC_EVALUATOR=1 HEAVY=1 .agents/docker/run.sh uv run python -m benchmarks.compat2.score --holdout --out /tmp/holdout.json
```

Create every agent worktree with `.agents/bin/agent-checkout <name>`, not plain `git worktree add`. Hooks and deny rules load when a session starts, so they apply to sessions started after this commit.

## References

Kernels follow pyperformance and Benchmarks Game algorithms and Shed Skin and Codon style examples. They are original code, MIT licensed, with no upstream source copied. See `scripts/*/meta.json`.
