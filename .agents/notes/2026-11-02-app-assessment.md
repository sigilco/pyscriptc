# Anonymized third-party app assessment with master (2026-11-02)

Target: a typical Flask plus OpenAI SDK LLM backend (anonymized, a public GitHub repo; identity withheld). Input: two outside analyses (doc-based and empirical). Treated as a lead; every number below was reproduced locally.

## Method

- Compiler: pyscriptc 0.7.0 built from master, commit `9d6c831`, on CPython 3.14.9 (`uv venv --python 3.14.9`, editable install of the worktree).
- Target: fresh sparse clone of the app (a public GitHub backend), sparse-checkout `backend`, at `/tmp/scratch/app/backend`. No files modified; a copy at `/tmp/scratch/mfbuild` got a `[project.scripts]` and an island `[tool.pyscriptc.imports]` table appended to pyproject.toml only, to test project mode.
- Commands: `pyscriptc imports`, `pyscriptc coverage`, `pyscriptc assess` (text and `--json`), `pyscriptc build`.

## Measured numbers (master, not the report's 3.14.10 install)

| What | Report said | Measured on master | Verdict |
| --- | --- | --- | --- |
| Total fences (coverage) | 1371 | 1285 fences (1213 root) in 548 of 934 definitions; 1368 "more fences" tail in build output | report's 1371 came from the build-error tail, not coverage; use 1285 |
| Native share of definitions | 42.9% (234/546) | 41.3% (386 of 934, 58 files); 7 island, 541 blocked | report scoped to `app/` only (546 defs); whole tree is 934 defs |
| Runtime plane map | 546 of 546 island | 903 of 903 planned functions island, `moduleTopLevel: island`, one demotion (`is_gpt5_family` demoted by `create_chat_completion`) | reproduced: zero application code runs native |
| Island imports | 12 deps + 2 stdlib | assess lists 15 island imports, includes `time` and `action_logger` (a local module fenced by PS2002) in addition to the report's list | report missed `time` and `action_logger` |
| Python / version | 3.14.10, sandbox | 3.14.9 per the task environment | environment difference, no claim impact |
| run.py fences | 15, 100% blocked | 9 fences on run.py, 1 definition fenced (the `main()` function rides the island top level) | report's "15" not reproduced; direction correct |
| scripts/ fenced | 67.7% | scripts files show 17/27, 9/35, 9/27, 9/27 native etc.; 0 of 6 files lower fully | direction correct |
| `[project.scripts]` required for build | yes | yes; the app has none (`run.py` is script-style with `if __name__ == '__main__'`) | reproduced |
| Fence codes | Any, Dict, kwargs | PS2002 644, PS1001 221, PS1104 (Any) 156, PS2001 106, PS1204 78, PS2003 24, PS1003 17, PS1106 16, PS1103 13, PS1202 7, PS1201 2, PS4203 1 | new detail |

Note: the report first installed the PyPI 0.4.1 wheel before installing from git; PyPI is stale relative to master. Anyone reproducing must install from master.

## What forces the island (the `assess` "path to the island" output, reproduced verbatim in mechanism)

`flask`, `flask_cors`, `openai`, `zep_cloud`, `httpx`, `fitz` (PyMuPDF), `chardet`, `charset_normalizer`, `pytest` (tests), plus the local `action_logger` module, plus stdlib `collections.abc`, `concurrent.futures`, `time`. Each names its forcing import site, e.g. `from flask import Flask, request (app/__init__.py:12)`, `from openai import OpenAI (app/services/oasis_profile_generator.py:18)`, `import fitz (app/utils/file_parser.py:114)`.

Once the module top level is planned for the island (divergence 42, all-or-nothing), every function in that module plans island too: the plane map routes 903/903 functions. Native share at run time: 0%.

An actual island build still stops: 1368 remaining fences, led by PS2002 (`call to Blueprint is not lowered`, package-level `bp = Blueprint(...)` module assignments), PS1001 (`def create_app(config_class=Config)`), PS1204 class-body assigns, and PS1104 `Any` in the camel-ai scripts. The island policy clears the imports, not the 541 blocked definitions.

## Root causes, ranked (from the 1285-fence histogram)

1. **Untyped/untable imports, PS2002 (644 fences, 50%)**: flask, flask_cors, zep_cloud, openai, httpx, fitz, chardet, charset_normalizer, traceback, uuid, types, sqlite3, signal, pytest. Structural for the third-party half (C extensions, reflection frameworks); fixable-by-policy for the stdlib half (`collections.abc`, `concurrent.futures`, `time` are island-routable).
2. **Missing annotations, PS1001 (221, 17%)**: `def create_app(config_class=Config)`, unannotated locals, pytest fixtures (`monkeypatch`, `tmp_path`). Category (b): the typed-first rule; annotating clears these.
3. **`Any` / bare generics, PS1104/PS1103 (169, 13%)**: camel-ai message dicts, `Dict`/`List` spellings. Mostly (b): retype with concrete shapes; some (a): the values genuinely come from untyped libraries, so the boundary stays island.
4. **Constructs outside the subset, PS2001/PS2003 (130, 10%)**: keyword args to stdlib calls, `contextlib.ExitStack`, `dataclasses.field`. (b) mostly, hand-written idioms exist.
5. **Class model, PS1204/PS1202/PS1201 (87, 7%)**: `Assign in a class body is not lowered`, `call to field is not lowered`, non-frozen dataclass options. Mixed: (b) for plain records, (c) for class-body state machines (the logger, the simulation manager).
6. **Reflection, PS4203 `__del__`, `**kwargs` forwarding (locale.py)**: (c) structural.

Categories:
(a) the island tier already makes work: all routed imports above run under embedded CPython with the documented marshalling cost; the app would execute (not compile native) if the 541 construct fences cleared.
(b) fixable by annotating: 221 PS1001 fences, most of the Any/dict typing in `app/services` (the fences name the exact line and fix; `assess` prints the typed idiom per fence).
(c) structural: flask's Blueprint/context-proxy model, pydantic validators (openai and zep_cloud clients), C extensions (fitz, chardet, charset-normalizer), camel-ai/OASIS reflection-driven agent loops. What each would take: flask needs a compiled router + context locals as native slots (a framework, not a fix); C extensions need an extern-manifest FFI story for arbitrary distributions; pydantic needs runtime annotation reads, which a no-interpreter binary cannot provide; camel needs dynamic dispatch the subset refuses by design.

## First-run experience findings (all reproduced)

1. **Stale PyPI**: the report first got 0.4.1 from PyPI; master is 0.7.0. A new user following "pip install pyscriptc" lands on a version far behind master (issue draft A).
2. **No entry point in a script-style app**: `pyscriptc build` requires `[project.scripts]`; the app's `run.py` is top-level code with a `__main__` guard and no console script. Nothing in the error path says "add [project.scripts]" until you read docs (issue draft B).
3. **The assess "path to the island" output is good**: it prints the exact pyproject lines and the forcing import for every island-routed module. Keep and reference it.
4. `pyscriptc build` takes no `--fallback` flag; fallback must be configured in pyproject.toml. Not obvious from the compile flag docs (folded into issue draft B).
5. coverage/assess counts differ in scope (definitions in fenced files vs the build "and N more" tail); the report conflated them. A doc sentence clarifying which number is which would prevent the same confusion (folded into issue draft A as a docs nit).

## Issue drafts (never posted)

### Draft A: `docs: document the first-run gap between the PyPI release and master`
```
### Summary
pyscriptc 0.4.1 is the newest PyPI release while master is 0.7.0. A new user
who installs from PyPI gets a compiler that lacks the current diagnostics,
the plane map and the island policy, then reads docs that describe master.

### Reproduced
- `uv tool install pyscriptc` resolves 0.4.1 (2026-11-02).
- Installing from git (`uv pip install git+...`) gets 0.7.0 and behaves as documented.
- The docs, the compat site and `assess` output on master do not mention the gap.

### Proposal
State the supported install channel in getting-started, and make the PyPI
project description or a `pyscriptc doctor` check name the master/PyPI delta
so a stale install is visible on the first run.
```

### Draft B: `feat(build): actionable error when a script-style app has no [project.scripts]`
```
### Summary
`pyscriptc build` requires a `[project.scripts]` table. Apps whose entry is a
script-style module (top-level code plus an `if __name__ == "__main__"`
guard, no console function) stop with a bare argparse usage error, and
`build` accepts no `--fallback` flag, so the island tier is invisible there.

### Reproduced
- Sparse clone of the app (a public GitHub backend), sparse-checkout `backend`,
  then `pyscriptc build` in `backend/` where the entry is `run.py` (main guard,
  no callable entry) -> "unrecognized arguments"/usage error, exit 2, nothing
  names `[project.scripts]` or `pyscriptc compile run.py`.
- Commands run against the app: `pyscriptc imports`, `pyscriptc coverage`,
  `pyscriptc assess` (text and `--json`), `pyscriptc build`.
- Adding a `[project.scripts]` entry plus an island `[tool.pyscriptc.imports]`
  table reaches the real fences, so the path works once discovered.

### Proposal
1. When `build` finds no scripts table but the directory has a plausible
   entry module, print the exact pyproject lines to add (same style as
   assess's "path to the island").
2. Surface the fallback/island configuration note from `build --help` or the
   error text.
```

## Compiler gaps found while measuring (issue drafts, never posted)

### Draft C: `feat(assess): state the scope of definition counts in the summary`
```
### Summary
`coverage` prints "1285 fences in 548 of 934 definitions" over the whole
tree; `build` prints "and 1368 more fences" which counts the cascade tail.
An outside reviewer reproduced the app with 1371 and 42.9%/546 by scoping
to one subpackage and comparing different counters, and both numbers
looked authoritative.

### Proposal
`assess`/`coverage` summaries should name their scope (files included,
definitions counted, root vs cascade fences) in the one-line summary, and
the build "and N more" line should match coverage's root count so the two
tools can be quoted side by side.
```

### Draft D: `feat: stdlib island routes should not require a policy line for partially lowered modules`
```
### Summary
`collections.abc`, `concurrent.futures` and `time` are stdlib modules that
assess routes to the island, but each still needs an explicit
`[tool.pyscriptc.imports]` line listing a dotted name
(`collections.abc = "island"`), and one forcing site was a local module
fenced by PS2002 (`action_logger`) that also needs a line to route.

### Reproduced
`pyscriptc assess` on a Flask backend lists 15 island imports including the
three stdlib entries, each with a `forced by:` site.

### Proposal
Consider routing stdlib submodules automatically when the parent lane is
decided, and printing (not just suggesting) the policy block when the user
passes a flag, so the first successful island build is one command.
```
