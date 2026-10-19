# Fork or build a typechecker for the pyscriptc facts oracle

Date: 2026-10-19. Question: does forking (or building) a Python typechecker
make sense as pyscriptc's `TypeFacts`/`CallableFacts` oracle, under the hard
constraint of ~35MB binary/install size?

Prior context (established, not re-litigated): pyrefly is the default oracle
(`pyrefly check --report-pysa`, 34MB, MIT) but its Pysa report is
Pysa-selective (~16/869 exprs vs mypy on our corpus); `zmypy` was
byte-identical to mypy on our sample but ships no bulk map; `ty` is
beta/hover-only; jedi silently wrong; pytype dead; mypy works but carries a
heavy dep profile; `sigilco/pyright --facts` gives the ideal bulk map but the
bun build is 96MB binary + 32MB typeshed.

## Verified measurements this session

| Item | Verified fact | Source |
|---|---|---|
| pyrefly 1.4.0 binary | 34MB single binary (`bin/pyrefly`), MIT | measured; https://github.com/facebook/pyrefly/blob/main/LICENSE |
| pyrefly `--report-trace` | per-module `types` map keyed `line:col-col`, **but only binding-site names + annotation exprs**, not every expr | measured on sample below |
| pyrefly `--report-pysa` | only `Expr::Name`, attribute *base*, and call args are exported | https://github.com/facebook/pyrefly/blob/main/pyrefly/lib/report/pysa/type_of_expression.rs (`visit_expression` match) |
| zuban 0.10.1 | **AGPL-3.0-only** (not MIT — corrects earlier assumption); 103MB `bin/zuban` + 33MB vendored stubs (typeshed 30MB + django-stubs 3.4MB); ~136MB installed | measured; https://github.com/zubanls/zuban/blob/master/LICENSE |
| zuban activity | active (commits through 2026-10-19) but effectively single-author: davidhalter ~13.5k commits, next contributor 5 | https://github.com/zubanls/zuban/contributors |
| mypy 2.4.0 compiled wheel | ~57MB installed (36MB mypyc `.so` + 12MB pkg incl. 5MB typeshed + ast_serialize/librt/pathspec deps) | measured, `uv pip install mypy` |
| mypy 2.4.0 pure-python wheel | 2.8MB download; ~17MB installed incl. deps — **fits 35MB** | https://pypi.org/project/mypy/#files + measured |
| pyright typeshed-fallback | 32MB on disk | measured, sigilco/pyright checkout |
| scriptc CLI binary (0.3.6) | 47MB — a scriptc-compiled pyright would start at this order *before* its 3.5MB bundle and 32MB typeshed | measured, npm `scriptc` install |
| pyrefly repo | MIT, very active, Meta-run, accepts PRs (has CONTRIBUTING, .agents/skills for contributors) | https://github.com/facebook/pyrefly |

## Candidate assessments

### 1. Fork zuban — rejected

Three independent killers: (a) **AGPL-3.0-only** — the earlier "MIT" note is
wrong; a fork that ships a modified AGPL checker is a license minefield for a
compiler toolchain, and upstream offers a commercial license contact only;
(b) **size**: 103MB binary + 33MB stubs fails 35MB outright, and Rust release
binary slimming won't recover 3x; (c) single-author bus factor. Coverage
would be excellent (mypy-parity semantics) but moot.

### 2. Fork pyrefly — cheap fork, best coverage-per-effort

Verified from source: the Pysa report's selectivity is a ~15-line `match` in
`TypeOfExpressionVisitor::visit_expression` (`Expr::Name`, `Expr::Attribute`
base, `Expr::Call` args) gated by `current_function`. `get_type_trace(range)`
already answers any range. A fork patch that visits **every** `Expr` and
dumps `get_type_trace` into a plain JSON map is estimated **~100–300 LOC plus
a contract test** — days, not weeks. pyrefly checks the whole module
eagerly, so most expressions have a trace; residual gaps (untraced exprs in
unreachable/speculative regions) need a corpus spot-check, not new inference.

- (a) Coverage ceiling: near-mypy once the visitor filter is lifted
  (inference — the trace records what the solver produced; unreachable code
  may be absent).
- (b) Effort-to-gap: lowest of all candidates; we already parse pysa JSON.
- (c) Maintenance: MIT, single-file patch against a fast-moving upstream —
  rebase cost small but real; pin releases.
- (d) Size: 34MB binary, compliant.
- (e) Risk: report/pysa is an internal API surface (no stability promise);
  Meta controls direction. Mitigation: upstream-first (see 6).

### 3. Fork mypy "facts-only" — fits only in pure-Python form

mypy's `type_map` is the coverage gold standard and the adapter already
exists. But "strip to facts-only" doesn't remove size: the checker *is* the
size — typeshed is bundled (5MB) and can't go. Verified: default compiled
install = **~57MB installed** (over budget); the `py3-none-any` wheel
(2.8MB → ~17MB installed) fits but runs ~10–20x slower on large files and
still means a CPython-runtime dependency story rather than a binary. Viable
as a *fallback oracle* for machines where speed doesn't matter; not as the
primary. Fork burden would be a packaging pipeline (dropping the mypyc
wheel), not code.

### 4. sigilco/pyright scriptc-native — fails the hard constraint, honestly

The fork's `--facts` output is the *best* contract we have (every expr's
resolved type + callables + overloads, no string re-parsing). But size math
is fatal: scriptc 0.3.6's own binary is 47MB, so a scriptc-compiled pyright
(3.5MB flat JS bundle) will land **≥ ~50MB binary + 32MB typeshed-fallback**,
and the bun build is worse (96MB+32MB). No realistic path under 35MB unless
the constraint is relaxed or the oracle is allowed to be a dev-machine tool
rather than shipped. Keep it as the **dev/parity oracle** — it's the
reference for validating whatever ships — not the shipped one.

### 5. Build new on ruff/ty/tree-sitter/libCST/mypy-as-library — rejected

Those give a parser and AST, not a checker. Honest floor: constraint
solving, narrowing, protocols, overloads, generics = **multiple person-years**
to mypy-parity (zuban took a single expert ~5 years standing on jedi; ty is
a funded Astral team still in beta). Off the table for an oracle.

### 6. No-fork: contribute the dump upstream to pyrefly

The patch in (2) is small and plausibly welcome upstream: pyrefly already
ships `--report-trace`, `--report-pysa`, `--report-glean`; a "full
expression map" mode is a natural extension, MIT-licensed, and Meta reviews
external PRs. If accepted, we get the bulk map with **zero fork burden**.
Risk: roadmap mismatch or slow review (days–weeks). The thin fork from (2)
is the standing fallback while a PR is pending.

## Ranked recommendation

1. **Upstream a full-trace export to pyrefly** (`--report-trace` "all exprs"
   mode or a `--facts` flag); meanwhile ship a thin pyrefly fork with the
   ~100–300 LOC visitor patch so the facts lane is unblocked today.
   Verify coverage on the compat2 corpus with `benchmarks/factsdiff.py`
   before switching the default.
2. **Thin pyrefly fork as standing fallback** if upstream rejects/stalls —
   pinned release, single patch file, MIT-clean.
3. **sigilco/pyright --facts stays the dev/parity oracle** — best contract,
   size-fails shipping; use it to validate pyrefly output.
4. **mypy pure-python wheel (~17MB) as emergency fallback oracle** — highest
   coverage, slow; keep the existing adapter warm but deprecated.
- Rejected: zuban (AGPL + 103MB + single-author), build-new (person-years),
  ty/jedi/pytype (per earlier sweeps).

## Verified vs inference

Verified: all sizes/licenses/activity in the table; the pysa visitor
selectivity (read the source); `--report-trace` scope (ran it). Inference:
the ~100–300 LOC patch estimate and "most exprs get a trace" claim —
confirm by diffing a patched build's map against pyright `--facts` on the
corpus; mypy ~10–20x pure-python slowdown is from the mypyc project docs
(github.com/mypyc/mypyc), not re-measured here.
