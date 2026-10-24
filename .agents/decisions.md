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
