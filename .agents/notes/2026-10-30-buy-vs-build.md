# Buy vs build: ten most expensive-to-maintain components

Date: 2026-10-30. Research session buyvbuild; no code changes except the notes copy of this file.
Repo state: master as of 2026-10-30 plus the component inventory from PR #785
(`.agents/docs/ARCHITECTURE.md`, branch `work/architecture-inventory`). Line counts below are
`wc -l` on the checked-out master, re-run for this session, so they may differ slightly from the
2026-10-29 inventory numbers where noted.

Constraints applied throughout: permissive license only; binary size matters (we section-GC);
Windows/wasi/static-link must stay possible; differential tests (compat2 probes, divergence
list in `docs/reference/divergences.md`) must stay green; native typed subset, no CPython at
runtime in the native tier. Items 5 and 8 sit in or beside the boxed fallback tier, which has
its own drop-flag economics (`mise run boxed:cost`, latest window 0.5% code, all else zero).

Verification method: library metadata fetched live from the GitHub API on 2026-10-30
(`repos/{repo}` and `repos/{repo}/releases/latest`). Anything not fetched or built today is
marked UNVERIFIED. I did not build hello-world binaries with each library, so binary-size
numbers are estimates marked UNVERIFIED unless a spike measures them.

## 1. lower/ (14,388 lines, 9 files)

Lines and files we own: `src/pyscriptc/lower/`, largest files `__init__.py` 3613,
`_calls.py` 2668, `_stmt.py` 2497, `_expr.py` 1992, `_classes.py` 2254, `_stdlib.py` 1701
(measured today; the inventory said 14,388 total, close enough).

What tests pin: the full language probe corpus (`benchmarks/compat2/probes/language.jsonl`,
207 KB) plus `tests/test_lower_*` and the fence diagnostics. Everything about statement,
expression, class and call semantics is byte-pinned against CPython stdout.

Buy candidates: none exists. There is no library that lowers CPython ast to a custom JSON IR.
CPython's own compile pipeline is not usable (we must not run CPython at runtime in the native
tier, and its bytecode is not our IR). The realistic "buy" would be adopting a whole existing
compiler (scriptc, Nuitka), which fails the divergence contract.

License/size/platforms/bus factor: n/a.

Integration effort: n/a.

Verdict: keep, plus shrink. What shrinks it is not a library but structure: `__init__.py` at
3,613 lines and the call/expr/stmt split suggest dispatch-table consolidation, dead subset
gates retiring as features ship, and pushing per-module lowering down into `restlower/` where
the table-driven pattern already costs less per feature. Track it by file-size budget in CI
(ruff or a small script that fails when a lower/ file crosses, say, 3,000 lines) rather than
by rewriting.

## 2. ps_re.c (1,356 lines, from-scratch backtracking regex)

Lines and files we own: `src/pyscriptc/runtime/ps_re.c`, 1,356 lines.

What tests pin: divergence 4 says explicitly that `re` is "the runtime's byte-oriented
backtracking subset, not CPython's re": flags `L`, `T`, `DEBUG` and unknown bits raise
`ValueError`, `A` and `U` are no-ops, non-consuming loops stop with `RuntimeError` after fuel,
five or more groups refused. `tests/test_re_a.py`, `test_re_ascii.py`, `test_re_compile_module.py`,
`test_re_debug.py`, `test_re_dotall.py`, `test_compare_chain.py`, `test_r27_re_search.py` plus
re probes in `language.jsonl` and `surface.jsonl`. A replacement must keep the fenced subset
semantics identical or the divergence list and probes change, which is a recorded-decision gate.

Candidates, verified today via the GitHub API:

- PCRE2 (PCRE2Project/pcre2): BSD-3-Clause, latest release `pcre2-10.49` 2026-09-28
  (a security release, GHSA-r9hj-j2rw-4q3m, JIT-stack out-of-bounds write), 1,389 stars,
  pushed 2026-10-29. Actively maintained with a documented 5-year support policy per release
  branch (inventory claim, matches release cadence; the policy itself UNVERIFIED).
- RE2 (google/re2): BSD-3-Clause, latest release `2025-11-05`, 9,839 stars, active. Ruled out
  on semantics: RE2 forbids backreferences and lookbehind by design, so it is a different
  language, not a parity replacement for even our subset.
- Oniguruma (kkos/oniguruma): **archived 2025-04** (repo `archived: true`), last release
  v6.9.10 2025-01-01. Ruled out: bus factor zero going forward. This contradicts the "BSD-2,
  C, unverified" note in the inventory.

The risky part: Python `re` semantics vs PCRE2. Overlaps are large for our subset
(both support named groups `(?P<name>)`, lookahead, non-greedy quantifiers), but the edges
differ: Python 3.14 changed `\z`/`\Z` semantics (verbatim-end vs the old `at-end-or-before-final-newline`),
possessive and atomic-group syntax defaults, `\b` behavior on non-ASCII bytes (we are
byte-oriented, PCRE2 is 8-bit but its UTF mode changes `\b`), and error-message text. Buying
PCRE2 moves the fence set and error texts rather than eliminating them; each moved fence is a
divergence-list edit plus probe churn. Binary size: PCRE2 without JIT is roughly 400-600 KB of
code on x86_64 UNVERIFIED; with JIT more. Against a hello-world budget where section GC keeps
us small, that is the single largest cost in this whole document. Static link works everywhere
including wasi (PCRE2 has no OS dependencies beyond mem/malloc); Windows fine. Bus factor:
large maintainer group (Philip Hazel and the PCRE2 Project org).

Integration effort: M for the seam swap, L to keep the differential tests green (every probe
that names a fence or error string). The fuel semantics (our `RuntimeError` on runaway loops)
maps to `pcre2_set_match_limit`, which is actually a win: PCRE2's match limit is a much better
ReDoS defense than our fuel counter.

Verdict: buy later. Trigger: a measured correctness-bug stream on ps_re.c (regex probes
flaking or security-class fixes landing in ps_re.c in two consecutive windows), or a product
requirement for regex features the subset fences (backrefs beyond 4 groups, lookbehind
variations). Until then the 1,356 lines are fenced tightly enough that rent is being paid.
When it fires, PCRE2 is the only credible candidate (RE2 and Oniguruma are disqualified as
above).

## 3. coro.py (2,222 lines)

Lines and files we own: `src/pyscriptc/coro.py`, 2,222 lines.

What tests pin: `tests/test_coro_*`, `test_coro_async*`, async probes; generator frame
resume, closure capture, async-for/with, cancellation and deadline semantics matching
CPython asyncio observable behavior.

Buy candidates: none. There is no library that turns Python generators/async functions into
LLVM-emitted frame closures in our IR. The runtime side (ps_loop, ps_async) is item 9;
this is the compiler side, and compiler-side coroutine lowering is inherent scope, not
buyable. CPython's own frame objects are the only alternative implementation and are banned.

Verdict: keep, plus shrink. The shrink path is the same as lower/: table-driven dispatch,
deleting boxed-adjacent branches if the boxed tier is ever dropped, and keeping the frame
layout documented so the emit side does not need per-case codegen. No spike.

## 4. emit/llvm/ (4,391 lines, 11 files)

Lines and files we own: `src/pyscriptc/emit/llvm/`: `_abi.py` 1,028, `_runtime.py` 526,
`_stmts.py` 539, `_driver.py` 521, `_core.py` 459, `_calls.py` 444, `_exprs.py` 375,
`_funcs.py` 372, `_stdlib.py` 336, `_print.py` 242, `__init__.py` 30. The clang/zig cc
delegation inside `_driver.py` is already delegated.

What tests pin: the emitter golden corpus (`tests/fixtures/backend/llvm/`, one fixture per
node kind and dispatch branch, byte-exact `.ll`), `tests/test_emitter_goldens.py`, and the
`--debug-dir` IR/LLVM dumps.

Buy candidates: none. Alternatives are LLVM's C API bindings or mlir, both of which add a
massive native dependency to every user link and change the artifact story; textual LLVM is
the deliberate design and the golden corpus is exactly the harness that makes owning it cheap.
What shrinks it: nothing structural is wrong; the shrink lever is emitting less, i.e. pushing
more behavior into shared runtime C functions (`ps_rt.c`) that every binary links once,
instead of per-node inline LLVM text. That also shrinks binaries.

Verdict: keep. No spike. Re-evaluate only if a second backend (the backend-contract doc
anticipates one) lands, which is when sharing a codegen core across backends becomes worth it.

## 5. ps_island.c (1,609 lines)

Lines and files we own: `src/pyscriptc/runtime/ps_island.c`, 1,609 lines, ~1.4% of compiler
lines per the boxed ledger baseline; links libpython.

What tests pin: island tests and the boxed ledger; GIL/thread-safety fixes land here
(the PR #342 incident in AGENTS.md).

Buy candidates: none; this is the delegation. It exists so the fallback tier can call real
CPython. The maintenance cost is CPython micro-version ABI tracking, which no library removes
(the python-build-standalone packs already pin the interpreter side).

Verdict: keep, governed by the boxed drop-flag rules rather than a buy/build decision. If the
flag ever raises on two consecutive windows, the correct move is deletion of the tier
(per AGENTS.md), not replacement. No spike.

## 6. Unicode and case tables (ps_unicode.c 1,871 + ps_case.c 1,982)

Lines and files we own: `src/pyscriptc/runtime/ps_unicode.c`, `ps_case.c`; ps_case.c is
generated by `scripts/gen_case_tables.py` from the CPython 3.14 oracle.

What tests pin: divergence 3 pins exact behavior: full Unicode 16.0.0 case mappings (current
generated tables), code-point-counted widths, CPython repr escape rules, `is*` predicates with
named `NotImplementedError` gaps, Latin-1-only unicodedata with `ValueError` on out-of-range
normalization. `tests/test_str*` and the unicode probes.

Candidates, verified today: utf8proc (JuliaStrings/utf8proc), MIT license, latest v2.12.0
2026-09-25 shipping Unicode 18, 1,307 stars, active. ICU: too heavy for our binary budget
(UNVERIFIED size, but tens of MB of data is well known).

The real maintenance cost is the generator plus Unicode-version bumps, which we inherit from
the CPython oracle anyway (CPython 3.15 will bump to a newer Unicode and our regeneration
follows automatically). utf8proc buys normalization (NFC/NFD that today raises `ValueError`)
and full-category properties, which is a feature, not a parity fix; buying it early also
moves `str` off our UTF-8-plus-ASCII-flag model unless we only use its property functions.
Size: utf8proc is roughly 300 KB of tables UNVERIFIED, against our current generated tables
which cost ~0 marginal size because we already ship them.

Verdict: keep now; buy later with a named trigger: zoneinfo/unicodedata expansion (the "S4"
full-tables gate in the inventory) or a requirement for normalization outside Latin-1. If it
fires, utf8proc is the candidate and ICU is rejected on size. Effort then: M (property-table
plumbing only, no string-model change).

## 7. ps_fmt.c (1,107 lines) and the float-printing question

Lines and files we own: `src/pyscriptc/runtime/ps_fmt.c`, 1,107 lines; adjacent: ps_rt.c's
`ps_f64_str`, a shortest-round-trip search over libc snprintf with strtod verification.

What tests pin: `tests/test_basic_format.py`, `test_format_specs.py`, plus surface probes:
`%`-interpolation and `str.format` byte-exact against CPython, including float repr and
widths counted in code points (divergence 3).

Candidates: no library implements CPython's format-spec mini-language; that stays ours.
But the float-printing core is buyable and is the actual subtle part (the inventory itself
flags it: "results depend on libc quality").

- Ryu (ulfjack/ryu), Apache-2.0 (with Boost options per file headers; repo metadata says
  Apache-2.0), tagged v2.0, pushed 2026-02-09, 1,358 stars. Stable, mature, C and C++ variants;
  the C `ryu.c`/`ryu.h` are a two-file drop-in. The algorithm is what modern CPython-adjacent
  runtimes use for shortest-round-trip printing.
- dragonbox (jk-jeon/dragonbox), Apache-2.0, release 1.1.3, pushed 2025-10-03, 821 stars.
  Excellent algorithm, but C++ with policy classes; C-callable only via a wrapper. For a C
  runtime, Ryu's C variant is the better fit.

Why this matters: CPython's float repr is David Gay-based shortest repr via `_Py_dg_dtoa`,
independent of libc. Our snprintf-loop approach can differ from CPython 3.14 on edge floats
depending on the libc we link, which means a parity bug that only reproduces on some libcs.
Ryu removes the libc from the equation and is deterministic across platforms. Size: ryu.c is
~1,000 lines UNVERIFIED (I did not download it); net binary delta near zero or slightly
negative because we delete the search loop.

Integration effort: S. Replace the body of `ps_f64_str` with Ryu plus the shortest-exponent
formatting CPython expects ( Ryu gives shortest digits; the exponent-vs-fixed-point choice
rules are ours already and stay).

Verdict: buy now. This is the highest value-per-effort item in the document.

## 8. ps_http.c (822 lines) with the TLS seam (491 lines + vendored mbedTLS 3.6.7)

Lines and files we own: `src/pyscriptc/runtime/ps_http.c` 822 lines on master; the seam and
mbedTLS adapter (491 lines owned) and mbedTLS 3.6.7 vendored live on `work/netnative-tls`,
not yet on master.

What tests pin: `tests/test_http*`, net receipt, and the branch `test_https_tls.py`.

Candidates: libcurl (MIT-derived license, large) and plain OpenSSL instead of mbedTLS
(Apache-2.0, verified in the inventory). libcurl buys redirect/auth/cookie logic we do not
need in a deliberately small subset, at a size cost that dwarfs ps_http.c itself (libcurl
static is on the order of 1-2 MB UNVERIFIED). mbedTLS is already the right TLS choice:
Apache-2.0/GPL-2 dual, compact, static-link and wasi-friendly, and already vendored through
the hash-pinned adapter pattern. Note mbedTLS upstream context: 3.6.x is the maintained LTS
branch; 4.x restructuring is upstream churn to watch at pin bumps (UNVERIFIED in detail).

Verdict: keep. The seam already delegates the do-not-own domain (TLS) correctly; ps_http.c
itself is 822 lines of sockets that no library replaces without a size regression. Trigger to
revisit: if urllib surface grows (chunked upload, proxy auth), consider a thin libcurl adapter
behind the packaging-adapter pattern rather than growing ps_http.c past ~1,500 lines.

## 9. Loop stack (ps_loop 851, ps_poller 716, ps_select 295)

Do not redo: the full assessment is `/tmp/loops-assessment.out.md` (2026-10-29). Its verdict
stands: keep ps_poller now; buy libuv later, Windows-only first, if Windows networking becomes
a committed tier (effort S to wrap the adapter seam, M/L for libuv everywhere); revisit libxev
at 18 months only if it ships tagged releases plus a Windows backend. libuv 1.53.0 released
2026-09-24, BSD/MIT/Apache mix, verified in this session's GitHub metadata too (27,288 stars,
pushed 2026-10-28). libev is dormant (last tarball 2020), libxev has no tagged releases and no
Windows backend, wepoll is dormant with a nonstandard license. The proposed spike (wepoll
Windows adapter under 250 lines, tests green) and the libuv size/throughput pass criteria
(delta under ~150 KB, within 10% throughput) carry over unchanged.

Verdict: keep now; buy later (libuv, trigger: committed Windows sockets tier).

## 10. ps_path.c (1,433 lines)

Lines and files we own: `src/pyscriptc/runtime/ps_path.c`, 1,433 lines; plus `ps_osmore.c`
1,031 for tempfile/shutil/resources (not in scope here but same verdict).

What tests pin: `tests/test_pathlib*`, `test_stdlib_pathlib.py`, `test_r33_pathlib.py`, and
os.path probes; Windows path semantics (drive letters, separators, case-insensitivity edges)
are pinned wherever the probes cover them.

Candidates: none. There is no C library implementing Python's posixpath/ntpath split,
`pathlib.PurePath` algebra, or fnmatch. Portability libraries (APR, glib) are larger and
semantically wrong (glib is LGPL; APR is Apache-2.0 but a different API model and heavy).
CPython's own ntpath module is not linkable. Windows semantics must be ours.

Verdict: keep. Shrink slightly by splitting os.path from pathlib if the file grows; no spike.

## Candidates that did not make the ten, for completeness

- yyjson (ibireme/yyjson), MIT, 0.13.0 released 2026-09-08, active (pushed 2026-10-25),
  3,928 stars: a possible replacement for ps_json.c (683 lines). But divergence 5 pins
  "no non-ASCII text" and the DOM-to-PsSeq translation layer stays either way, so the win is
  only robustness on the parse edge cases. Buy later, trigger: a json probe bug stream.
  Effort M. Also relevant: CPython already bundles expat but its json is hand-rolled, so
  parity with yyjson defaults (number formats, duplicate keys) needs checking (UNVERIFIED).
- libtommath (libtom/libtommath): public-domain/Unlicense family (repo shows NOASSERTION;
  the license file is dual Unlicense/WTFPL per docs, UNVERIFIED line-by-line), v1.3.0
  released 2024-03-27 but commits as recent as 2026-04, active again. Not applicable today:
  ints are i64 with PS3002 traps by design; bigint falls to the island tier. Buy only if a
  native bigint becomes a requirement, which would be a recorded decision first.
- mpdecimal (BSD-2, 2.5.1, already inside the PBS libpython pack per runtime.lock.toml):
  same trigger as libtommath; prefer it when the trigger fires because it is already
  shipped in packs, but note native-tier linking would need its own vendor pin, not the PBS copy.
- xxHash (Cyan4973/xxHash), BSD-2-Clause, v0.8.4 released 2026-09-19, very active, 11,391
  stars: NOT applicable to hashlib (digests must byte-match CPython's MD5/SHA, so ps_hash.c
  must stay or move to libcrypto). Container hashing is FNV-1a by divergence-adjacent design;
  swapping to xxHash changes nothing pinned unless hash randomization is ever required, in
  which case SipHash is the parity-correct choice, not xxHash. Keep as-is.

## Ranked table (value over effort)

| Rank | Component | Verdict | Effort | Value | One-line reason |
|---|---|---|---|---|---|
| 1 | ps_rt.c float printing (via item 7) | buy now (Ryu) | S | High | removes libc-dependent parity risk on a byte-pinned surface |
| 2 | ps_re.c (item 2) | buy later (PCRE2) | M-L | High, gated | biggest owned correctness surface; fire on bug-stream trigger |
| 3 | loop stack (item 9) | keep now, buy later (libuv) | S now, M-L then | Medium, gated | only Windows shipping unlocks it |
| 4 | unicode/case (item 6) | keep now, buy later (utf8proc) | M then | Medium, gated | generator already absorbs bumps; utf8proc buys normalization |
| 5 | ps_json.c (extra) | buy later (yyjson) | M | Low-medium | edge-case robustness only |
| 6 | ps_http + TLS (item 8) | keep | n/a | n/a | seam already delegates TLS correctly |
| 7 | lower/ (item 1) | keep + shrink | internal | internal | structure budget, not a rewrite |
| 8 | coro.py (item 3) | keep + shrink | internal | internal | not buyable |
| 9 | emit/llvm/ (item 4) | keep | n/a | n/a | golden corpus makes ownership cheap |
| 10 | ps_island.c (item 5) | keep (boxed rules) | n/a | n/a | drop-flag governs, deletion not replacement |
| 11 | ps_path.c (item 10) | keep | n/a | n/a | Windows semantics must be ours |

## Proposed spikes for the top two

### Spike A: Ryu float printing (buy now, then land it)

2 days. Vendor `ryu.c`/`ryu.h` (v2.0, hash-pinned via the fetch_vendor.py pattern), replace
the body of `ps_f64_str` in ps_rt.c with Ryu digits plus our existing shortest-repr
formatting rules, no API change.

- Pass: every float probe and `tests/test_basic_format.py` byte-identical before/after on
  Linux glibc AND on a second libc (musl, via the zig cc or a musl container), and the
  17-digit round-trip property holds for 10,000 random f64s against
  `struct.pack` verification. Binary size delta under 20 KB (section-GC measured, `mise run
  bench:baseline` green).
- Fail: any float probe delta that is not explained by fixing a genuine libc divergence we
  were previously getting wrong (in which case document the fixed divergence and proceed),
  or a size delta above 20 KB.

### Spike B: PCRE2 behind the ps_re seam (buy-later evidence, do not land from the spike)

3 days. Build PCRE2 10.49 statically (no JIT first), implement a translation shim that maps
our pattern subset to PCRE2 with `PCRE2_TRANSLATE`-equivalent handling of the Python flags
we accept, `pcre2_set_match_limit` wired to our fuel semantics, and keep ps_re.c in place
behind a compile-time flag.

- Pass: the subset probes in `language.jsonl` and `surface.jsonl` (all `re`-touching cells)
  produce identical stdout through the PCRE2 path with zero divergence-list edits, and the
  HTTPS-hello binary grows by under 300 KB with section GC (measured, not estimated).
- Fail: any probe cell that needs a divergence edit, or a size delta above 300 KB, or match
  limit semantics that cannot reproduce our `RuntimeError`-after-fuel behavior. Record the
  numbers either way; they are the trigger evidence for the buy-later decision.

## Unverified claims index

Binary sizes for PCRE2, utf8proc, yyjson, libcurl, libuv-in-binary, ryu.c line count, mbedTLS
4.x plans, PCRE2's 5-year support policy text, libtommath license file contents, CPython-json
vs yyjson default differences. Everything else (releases, licenses, activity, archived status)
was verified live via the GitHub API on 2026-10-30.
