# Decisions

The recorded decisions that bind the compiler. Each entry is dated, names the decision, says why, names the alternatives that were rejected, and points at the artifact that carries the detail. A change to a frozen gate (`AGENTS.md`, "Frozen v2 gates") needs an entry here first.

Entries are appended in date order. The newest is last.

## 2026-10-24 The `str` model is the CPython code-point model

Decision (owner, 2026-10-24): pyscriptc targets byte-for-byte CPython compatibility, so `str` becomes the full CPython code-point model. The storage is UTF-8 bytes, and every operation CPython defines over code points (`len`, indexing, slicing, iteration, comparison, `find`, the case family, `format`, `encode`/`decode`, the regular expression engine, `json`, `csv`, `tomllib`, `yaml`, `unicodedata`) is defined over code points here. Text I/O is UTF-8 by default, per PEP 540 and PEP 686.

Option (c), UTF-8 storage with byte semantics, is rejected as an end state. It would make the stdout bytes match CPython for a literal while `len`, indexing and slicing stayed byte-based, which trades one divergence for another and makes the divergence document claim a parity the model does not have.

Why: the parity contract in `docs/reference/divergences.md` claims that compiled programs match CPython 3.14 on stdout for everything that lowers, and that the empty divergence space is load-bearing. That claim is already false and the failure is silent. A source literal `café` prints `63 61 66 e9` where CPython prints `63 61 66 c3 a9`, and `len()` reads the byte count from `extractvalue %ps.str {v}, 0` (`src/pyscriptc/emit/llvm/_calls.py:30-33`) instead of the code-point count. The contract and the model are one problem, not two.

The alternatives:

- Latin-1 byte model (today): stdout bytes match CPython only for ASCII, and the difference is undocumented. Rejected as the end state.
- UTF-8 storage with byte semantics: matches stdout bytes for literals and keeps `len`, indexing and slicing wrong. Rejected as the end state; not needed as a stepping stone either, because the phases below land the full model without an intermediate state that has to be unwound.
- A code-point array (UCS-4 or UCS-2) with UTF-8 only at the I/O boundary: this is what CPython does, and it makes every string operation an array walk. Rejected for pyscriptc: it costs 4 bytes per code point for every program, including the ones that never see a non-ASCII character, and it turns every print and file write into a conversion.

The representation that is chosen is UTF-8 bytes with a cached code-point count and an ASCII flag, carried in a widened `%ps.str` payload. The ASCII flag keeps the common case free (byte count equals code-point count), the cached count keeps `len` a field read, and the memory profile stays at one byte per code point for ASCII text. The surrogate policy is narrow: a `str` holds code points outside U+D800 to U+DFFF, `surrogateescape` decoding is fenced with a named diagnostic, and the gap is recorded as a numbered divergence.

Cost accepted: the runtime audit. The `str` surface is roughly 9.3k lines of bytewise C (`ps_rt.c` 2186, `ps_more.c` 1073, `ps_fmt.c` 1039, `ps_re.c` 1356, `ps_unicode.c` 1521, and the json, csv, toml and yaml codecs 2640), plus the `IR_VERSION` bump, the emitter payload change and the frozen-gate movement in `benchmarks/compat2`.

Where: the plan is `.agents/plans/2026-10-24-codepoint-str-model.md`. The design detail is `.agents/docs/spec/str-model.md` (landed with S1). The epic is issue #472 and the phases are #473 (S1), #474 (S2), #475 (S3), #476 (S4), #477 (S5), #478 (S6) and #479 (S7).

Interim, until the model lands: a compile-time warning fires when a compiled program can emit a non-ASCII byte, and divergence item 3 names the stdout difference. The warning does not change the exit code and does not fence a working program.

## 2026-10-24 S1 moves the frozen v2 gates for the str payload (gate change)

Decision (recorded 2026-10-24, under the owner decision above): S1 of the str epic (issue #473) changes the IR shape, so the frozen v2 gate files move. This row is the recorded gate change that AGENTS.md requires before any frozen file is touched; the owner decision above is the why.

What moves and what does not:

- `benchmarks/compat2/probes/IR_HASHES.json`: every hash changes or is expected to, because the `%ps.str` payload widens and `IR_VERSION` stamps 2 into every module the probes lower. Regenerated with the A/B byte-identical discipline (two runs of `python -m benchmarks.compat2.irscore`, then `cmp`), on Linux only, never on macOS (`canonical.py` folds host constants, and the macOS baseline differs from the Linux one by hundreds of hashes).
- `benchmarks/compat2/probes/*.jsonl`, `CALIBRATION.json` and `TRIAGE.md`: not moved by S1 itself. S1 changes the payload shape, not the semantics, and the probes' expected stdout does not change. They move in S7, when the model's semantics land and the probe expectations re-run against CPython.
- `outcomes.py`, the `score.py` weights, `coverage_map.py` and `cache.py`: not moved at all. The S7 receipt records that they did not change.
- `canonical.py` rules (`.agents/docs/spec/ir-canonicity.md`): unchanged; the canonical form stays deterministic and host-independent in its rules.
- The `subset` stamp stays `gates-v1`: the same programs lower before and after S1.

Procedural note carried with this row: the IR_HASHES regeneration for S1 lands in the same PR as the payload change and only after this decision, through the `ir-hashes` workflow (generate A and B, `cmp`, copy the artifact). On a macOS checkout the regeneration is never run by hand.

Where: the plan phase S1 is `.agents/plans/2026-10-24-codepoint-str-model.md` ("The frozen gates"), the workflow is `.github/workflows/ir-hashes.yml`, and the platform note is `.agents/notes/2026-10-22-ir-hashes-regen.md`.

## 2026-10-24 The package tiering policy for the blessed list

Decision (owner direction, researched and recorded 2026-10-24): the beta does not chase PyPI compatibility. The compiler builds on the CPython builtin and standard-library subset, and a short blessed list of packages gets a stated surface under exactly one tier each. The tier is the promise made to a beta tester about scope.

The four tiers: T1 compiles the real package from source, unmodified, matching CPython. T2 ships a blessed native pack under the pack format (`.agents/docs/spec/pack-format.md`), the real import name, a subset surface, out of the compiler tree. T3 ships a pyscriptc-native alternative with a CPython-compatible API subset, reached by changing one import line. T4 is out of scope, said plainly.

Two rules bind the policy, and they are what make the promise keepable.

First, a tier is per package, never per feature. A part of a package that is outside its tier's surface is not "partially supported"; the user who needs it is out of scope for that package. A per-feature tier would be a promise nobody can check.

Second, the compiler never approximates a real package. Every name outside a pack's or an alternative's surface fences at compile time with the package name in the reason. This is what makes a conformance suite meaningful: the supported surface is exactly the written surface, and a diff against the real library under CPython covers all of it.

The assignments: `requests` is T1 (epic #428, the pure-Python lane is already open). `orjson` is T2 (the pack format's worked example, #411). `pydantic` is T3 (`pyscriptc-val`, #156). `fastapi` is T3 (`pyscriptc-fastapi`, #231 to #234 under epic #219). `numpy` is T3 (`pyscriptc-ndarray`, #496). `pandas` is T3 (`pyscriptc-frame`, #497). `langchain` is T4.

Why `langchain` is refused: 40 distributions, 7 native extensions, three frameworks deep, and I/O bound against a remote API, so native compilation buys nothing over CPython while every dependency costs fences. Measured fence counts for the rest are in the plan.

Why `pydantic`, `fastapi`, `numpy` and `pandas` are alternatives and not compiled originals: the fence census is dominated by the typing surface (`pydantic` alone is 2565 fences), and `numpy` and `pandas` are ABI-specific wheels, so compiling them is the island tier by another name. The alternatives carry the shapes a compiled program actually wants (validated models, sync routes, a flat typed buffer, a columnar frame) and one import line changes them.

The rejected alternatives: chasing the full typing-introspection surface so the real `pydantic` compiles (real work, recorded on #86 and #88, but it does not unblock the other packages and it cannot compile `pydantic-core`, a Rust extension); an island for `numpy` (links `libpython`, pins the speedup near 1x, and puts the binary back under the boxed ledger); and pretending T4 does not need saying (a tester who asks for `langchain` needs a plain answer, not silence).

What reopens the decision: a package shown to need a second tier (a real-package subset plus an alternative), or a tier surface shown unusable for the archetype it was picked for. A tier change for one package is a new entry, not an edit of this one.

Where: the plan is `.agents/plans/2026-10-24-apps-and-package-strategy.md` (the fence census, the two app archetypes, the grind plan). The probes are `.agents/probes/web_server.py` and `.agents/probes/log_pipeline.py`. The user-facing statement is `docs/guide/packages.md`.
