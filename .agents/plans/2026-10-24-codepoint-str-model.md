# The code-point str model (UTF-8 storage, CPython semantics)

Status: proposed 2026-10-24. Owner decision 2026-10-24: pyscriptc targets byte-for-byte CPython compatibility, so `str` becomes the full CPython code-point model. Option (c), UTF-8 storage with byte semantics, is rejected as an end state. Epic: issue #472. Phases: S1 #473, S2 #474, S3 #475, S4 #476, S5 #477, S6 #478, S7 #479. Plan lives in `.agents/plans/2026-10-24-codepoint-str-model.md`. The owner decision is recorded in `.agents/decisions.md` (entry 2026-10-24).

## Why

The parity contract in `docs/reference/divergences.md` claims that compiled programs match CPython 3.14 on stdout for everything that lowers, and that the empty divergence space is load-bearing. Today that claim is false for every program that prints a non-ASCII character, and the failure is silent.

The measured facts, all reproduced on 2026-10-24 against `master` at `5860541`:

| Fact | Evidence |
|---|---|
| CPython `str` is a sequence of code points (PEP 393), and its text I/O is UTF-8 (PEP 540, PEP 686) | CPython 3.14.9 |
| A Latin-1 source literal prints raw Latin-1 bytes, not UTF-8 | `print("caf\u00e9")`: CPython stdout `63 61 66 c3 a9`, native stdout `63 61 66 e9` |
| `len()` on a `str` returns the byte length, not the code-point length | `src/pyscriptc/emit/llvm/_calls.py:30-33` reads `extractvalue %ps.str {v}, 0`, which is the byte count |
| Every `ps_str_*` helper is bytewise | `src/pyscriptc/runtime/ps_rt.c` (`ps_case`, `ps_str_get`, `ps_str_slice`, `ps_str_find`) walks bytes |
| Literals are encoded Latin-1 on the way out | `src/pyscriptc/emit/llvm/_print.py:20` calls `text.encode("latin1")` |
| The UTF-8 helpers only handle two-byte sequences | `ps_utf8_encode` and `ps_utf8_decode` at `src/pyscriptc/runtime/ps_rt.c:2025-2065` reject any code point above U+00FF |
| `unicodedata` covers Latin-1 only | `src/pyscriptc/runtime/ps_unicode.c:1-4` |
| A Latin-1 literal in a `def` is not fenced at all | `pyscriptc coverage` on `def f() -> str: s: str = "café"; return s` reports `0 fences (0 root)` |
| A `>255` literal reached through the restlower fold path is not fenced either | `def f() -> str: return "caf\u00e9".upper()` compiles clean and emits `c"caf\E9\00"` |

So there are two divergences today, not one, and the doc records only the first. The doc says "Strings are a Latin-1 byte model" (divergence 3), which describes the model but not the observable consequence: a program that prints `é` writes a different byte stream than CPython, with no diagnostic and no error. The user-visible question is therefore a parity-contract question, not a feature question: either the `str` model becomes CPython's, or the contract has to say that non-ASCII output is not claimed to match.

The owner decided the first. This plan is the route to it.

## End state

`str` is a sequence of Unicode code points. The storage is UTF-8 bytes. Every `str` operation that CPython defines over code points is defined over code points here: `len`, indexing, slicing, iteration, comparison, `find`/`index`/`count`, the case family, `format`, `split`/`join`/`replace`/`strip`/`partition`, `encode`/`decode`, the regular expression engine, `json`, `csv`, `tomllib`, `yaml`, `unicodedata`, and the text I/O paths. Text I/O reads and writes UTF-8 by default.

`bytes` stays a byte sequence. The `str`/`bytes` boundary is the CPython one: `str.encode()` produces bytes, `bytes.decode()` produces `str`, the two never mix implicitly, and `bytes` is not affected by any of this work except where a codec explicitly converts.

Two representations are in contention and the plan picks one.

### Representation

Option 1, UTF-8 bytes with a cached code-point count and an ASCII flag. The `str` payload grows from `{ i64, ptr }` to `{ i64, ptr, i64, i32 }` or a `{ i64, ptr }` header plus a side table. Field 0 stops being the length. It becomes either the byte length with a separate code-point count, or the code-point count with the byte length derived from the buffer.

Option 2, a code-point array (UCS-4 or UCS-2) with UTF-8 only at the I/O boundary. This is what CPython does (PEP 393 keeps a compact representation, not UTF-8), and it makes every string operation a simple array walk. It costs 4 bytes per code point for memory and bandwidth, which is a regression against the current byte model for the common ASCII case, and it means every `print` and every file write pays an encode.

Option 3, UTF-8 bytes with a cached code-point count and no ASCII flag, where the count is computed lazily and cached on first use.

The recommendation is option 1 with the ASCII flag. The reason is that the ASCII flag makes the common case free: an ASCII `str` has code-point count equal to byte count, so `len`, `get`, `slice` with a step of 1, `find`, and the case family all take the current bytewise paths with no scan and no branch. The flag is one `i32` in the payload, it is set at construction time from a single pass over the bytes, and every constructor already knows whether it is writing ASCII. The non-ASCII path scans the UTF-8 bytes once, counting the non-continuation bytes, which is the standard O(n) trick and is exact for well-formed UTF-8.

The reason to prefer option 1 over option 3 is that the count must be available without a scan on the hot paths (`len` is a read of field 0 in the emitter today, and it must stay a read). A lazily computed count turns `len` into a potential mutation of the payload, which is wrong for a value type that crosses the ABI by copy.

The reason to reject option 2 is memory and I/O bandwidth. The whole point of the current design is that a compiled program is a small native binary with a compact runtime. A UCS-4 representation multiplies the resident size of every string by four and makes `print` a conversion. That cost is paid for every program, including the 99% that never see a non-ASCII character, to serve the 1% that does. CPython can afford it because it has a compact representation with a per-kind width and a promotion path. pyscriptc has one representation, so it should have the representation that is cheap for the common case.

The cost of option 1 is that a `str` is not a flat byte array anymore. Every runtime helper that takes `(int64_t n, char *p)` and treats `n` as a byte count has to be told which count it is getting. That is the bulk of the work, and it is why the phases are split by runtime file.

### The payload

The current payload is `%ps.str = { i64, ptr }`: a length and a pointer. The invariant in `.agents/docs/invariants.md` says a `str` never crosses the C ABI as a returned struct, and that stays.

The new payload is a fixed-width header in front of the bytes:

```
%ps.str = type { i64, ptr, i64, i32 }   ; byte length, data, code point count, flags
```

Field 0 stays the byte length, because every byte-oriented path (write, `printf`, `bytes` interop) already uses it and it must not change meaning. Field 2 is the code-point count. Field 3 is the flags word, with bit 0 meaning "ASCII" (byte length equals code-point count) and bit 1 reserved.

The header lives in front of the data pointer, so the data pointer points just past the header. That keeps the allocation one block and keeps the pointer arithmetic trivial. The alternative is a separate header allocation and a second pointer, which doubles the allocation count and the free path. The invariant "a `str` crosses the ABI as a pointer or by copy, never as a returned struct" is preserved: the struct is still only ever copied into and out of memory, never returned by value.

Wait: the header approach has a problem with the emitter's constant strings. Today the emitter emits `@ps.str.0 = private unnamed_addr constant [N x i8] c"..."` and the `insertvalue` builds `{ i64 N, ptr @ps.str.0 }`. With a header in front of the data, a constant string needs a constant header too, which means a second global and a GEP to reach the bytes. That is workable but it touches every constant-string site. The alternative is to keep the pointer pointing at the bytes and put the header in a parallel structure, which is worse.

The recommendation is: keep `%ps.str = { i64, ptr, i64, i32 }` with the pointer at the bytes and no in-band header, and accept that a heap-allocated `str` needs one extra word of storage somewhere. Two ways to do that:

Variant A, a side allocation. `ps_str_new` allocates the header and the bytes separately: the payload holds a pointer to a `PsStrHead { int64_t nbytes; int64_t nchars; int32_t flags; }` plus the data pointer. That is two allocations per `str` and a double free path.

Variant B, an in-band header at a negative offset. The data pointer points into the middle of one allocation, and the header sits immediately before it. The pointer is `char *` with the header at `p - 16`. This is the classic trick, it is one allocation, and it is unsafe if any code path ever constructs a `str` from a pointer it did not allocate. The emitter's constant strings would need a constant header global too.

Variant C, widen the payload to carry the header inline, and keep the pointer at the bytes. `%ps.str = { i64, ptr, i64, i32 }` where field 0 is the byte length, field 1 the bytes pointer, field 2 the code-point count, field 3 the flags. Every construction site sets all four fields. The emitter's constant path sets field 2 and field 3 at emit time, because the compiler knows the literal's length and whether it is ASCII. The runtime's allocation path sets them at construction. Nothing needs a side allocation and nothing needs a negative offset.

Variant C is the recommendation. It costs 16 more bytes per `str` value on the stack (the struct is copied by value in the LLVM IR) and zero extra allocations. The alternative, variant A, costs an allocation and a pointer chase per string. The alternative, variant B, costs an unsafe offset invariant.

The downside of variant C is that the emitter has to compute the code-point count for every constant string, which it can do exactly at emit time from the source text. For the runtime paths that build strings by concatenation, the count is maintained incrementally, which is the whole point of the cached count.

### Index and slice strategy

`len(s)` is a read of field 2. O(1), always.

`s[i]` returns the i-th code point. The current implementation is `ps_str_get`, which copies byte `i` into a one-byte buffer. The new implementation scans the UTF-8 bytes from the start, counting non-continuation bytes until it reaches code point `i`, then copies that code point's bytes into the result. That is O(n) per index.

O(n) per index is not acceptable for the hot path. `for ch in s` on a long string would be O(n^2).

Two ways out.

Option A, index by byte offset with a scan, plus a cached offset table. The `str` payload would carry a pointer to an offset table (`int32_t` per code point, plus one) that is built on first need and invalidated by any mutation. Indexing becomes O(1) after the table is built. The table costs 4 bytes per code point of memory, which is the same order as the UCS-4 option for the memory but not for the I/O. It also means a `str` that is only ever printed never builds the table, so the cost is paid only by programs that index.

Option B, keep a "start byte offset" cache for the last index used, which only helps repeated access at the same position.

Option C, make the index path O(n) but keep the total cost of iteration O(n) by iterating with a cursor rather than by index. That is, `for ch in s` lowers to a cursor walk that advances by the code point's byte width, not to repeated `s[i]`. CPython's own iterator is a cursor walk too. The remaining O(n) cases are random access (`s[i]` with a computed `i`), `s[i:j]`, and `find`.

The recommendation is option C plus option A for the cases that need it. The cursor walk covers iteration, which is the common case. Random access and slicing build the offset table lazily, once per string, and then cost O(1) per access and O(1) per code point for a slice. The table is per-`str`, cached, and dropped when the string dies.

The offset table must not change the payload size, because the payload crosses the ABI as a copy. It goes in the allocation behind the data pointer, reachable through a small side structure, or in a separate table keyed by the data pointer, which is worse. The clean answer is: the allocation for a heap `str` is `header + bytes + optional table`, the payload points at the bytes, and a separate `PsStrHead` pointer is reachable. But that reintroduces variant A.

There is a simpler answer that fits the existing code. The payload already carries a pointer. Let the pointer point at the header, and let the header carry the bytes pointer. Then:

```
PsStrHead { int64_t nbytes; int64_t nchars; int32_t flags; int32_t *offsets; char *data; }
```

and the payload is `{ ptr head }`, or `{ i64, ptr }` where field 0 is a tag. That is one allocation for the header and one for the bytes, and the offsets table is a third when it is needed.

This is getting heavy for a payload that crosses the ABI on every call. Let me reconsider.

What does the offset table actually buy? It buys O(1) random access and O(1) slice bounds. What does it cost? 4 bytes per code point of memory, one more allocation, one more invalidation rule, and a cache that must be thread-safe (the runtime has threads now, see the threading divergences) or per-string immutable.

An alternative that costs nothing: accept O(n) random access, but make it a *scan* that stops at the requested index, so the average cost is half the string, and make iteration a cursor walk so it is O(n) total. Then document the complexity, the same way issue #386 ("audit: classify every length scan in the runtime against CPython complexity") requires for every other length scan in the runtime.

The decision here should be recorded, not assumed, and the plan should pick the honest middle. The recommendation: cursor-based iteration (O(n) total, matches CPython's own iterator), and O(n) scan for random access and slicing, with the offset table as a documented follow-up if a benchmark shows it matters. That keeps the payload at `{ i64, ptr, i64, i32 }` with no side allocation, keeps the memory profile at 1 byte per code point for ASCII and 1 to 4 bytes per code point for the rest, and keeps the ABI change to one struct widening.

The scan for `s[i]` can be made cheap in the common case: if the ASCII flag is set, the index is the byte offset and the access is O(1). Only non-ASCII strings pay the scan, and only for random access. Most programs that use non-ASCII text use `len`, `in`, `startswith`, formatting, or iteration, all of which are O(n) or better without random access.

### Comparison and hashing

`==` between two `str` values compares the code points. For UTF-8, code-point-sequence equality is byte equality, because UTF-8 is a bijection between code-point sequences and byte sequences. So `==` stays a `memcmp`, which is what it is today. `<` between two `str` values compares code points, which for UTF-8 is also byte order, because UTF-8 preserves code-point order. So `ps_str_cmp` stays a `memcmp` with a length tiebreak.

That is a real property of UTF-8 and it is worth stating in the plan, because it means the comparison path needs no change at all. It also means the sort order of `str` matches CPython's code-point order for free.

Hashing must match CPython's `hash(str)`, which is siphash over the code points in a specific way, and is seed-randomized. The current runtime does not claim CPython hash parity for `str` (the fold comment in `restlower/_folds.py` says `hash` folds only on int and bool because str hashing is seed-randomized). Hash parity is therefore not required and is not in scope; what is in scope is that hashing stays *consistent* within a run and that `dict`/`set` lookups keep working. The `PsMap` hash index added in #382 caches string hashes, so the hash function must remain consistent for the life of the map. No change needed.

### Surrogates, lone surrogates, and escape policy

CPython's `str` can hold lone surrogates (`"\ud800"`). They come from `surrogateescape` decoding of undecodable bytes, from `os.fsdecode`, and from explicit `"\ud800"` literals. CPython's `str` cannot hold a lone surrogate in a UTF-8 encoded file without `surrogatepass`, and `"\ud800".encode("utf-8")` raises `UnicodeEncodeError`.

The policy for pyscriptc:

Storage is UTF-8, which cannot represent a surrogate. The two options are to keep a separate encoding for surrogates inside the UTF-8 stream (the WTF-8 style, which CPython does not use) or to refuse.

The recommendation: a `str` holds code points U+0000 to U+10FFFF, excluding the surrogate range U+D800 to U+DFFF. A source literal with a surrogate escape (`"\ud800"`) is a `PS2005` fence until S4 lands, and after S4 it stays a fence with a named diagnostic ("lone surrogates are not lowered"). Text decoded with `surrogateescape` maps undecodable bytes to U+DC80 to U+DCFF, which is exactly the surrogate range, so `surrogateescape` decoding is out of scope for the first pass and is fenced with a named diagnostic rather than silently mis-decoded.

This is a divergence from CPython, and it must be recorded as a numbered divergence. It is a narrow one: it only bites programs that read undecodable bytes as text.

An alternative would be to store surrogates as a special three-byte sequence in the UTF-8 stream, which is what some implementations do, and to be byte-compatible with CPython's `surrogatepass` codec. That buys `surrogateescape` support. It costs a non-standard encoding that has to be handled in every scan, every count, every comparison. The recommendation is to not do it in the first pass, to fence `surrogateescape`, and to note the follow-up.

The escape policy for the literal side: source files are UTF-8 (PEP 263 default). The compiler reads the source as UTF-8 and decodes to code points. A `"\uXXXX"` escape in the source is decoded at compile time to the code point it names. A `"\xXX"` escape stays a byte value in the `bytes` literal space and is a `UnicodeEncodeError` in a `str` literal above U+00FF, matching CPython. A `"\N{...}"` escape resolves through `unicodedata` and fences if the name is unknown.

### The bytes boundary

`bytes` is an immutable byte sequence, exactly as today. It is not affected by the code-point model.

The boundary rules are CPython's:

- `str.encode("utf-8")` returns bytes. `str.encode()` with no argument is UTF-8 in CPython 3, and it is UTF-8 here.
- `bytes.decode("utf-8")` returns `str`. `bytes.decode()` with no argument is UTF-8.
- `str` and `bytes` do not compare equal, do not concatenate, and do not hash the same.
- `b"..."` literals hold raw bytes; `b"\xe9"` is one byte, not a code point.
- `bytes.decode("latin-1")` maps each byte to the code point of the same value, which is the current storage model lifted into a codec. This is what the runtime's `ps_utf8_decode` currently does for two-byte sequences and it must become a real Latin-1 codec.
- `str.encode("latin-1")` raises `UnicodeEncodeError` for any code point above U+00FF, matching CPython.

The current `ps_utf8_encode` and `ps_utf8_decode` are misnamed: they encode Latin-1 to UTF-8 and decode UTF-8 to Latin-1, and they only handle two-byte sequences. They get replaced by a real UTF-8 codec that handles one, two, three and four byte sequences, plus a Latin-1 codec.

### The IR and the ABI

The IR itself does not need a new node kind. `const` already carries a `str` value, and the IR value is the source text of the string, which is a sequence of code points. The IR change is in what the const carries: today the IR keeps the Python `str` and the emitter encodes it Latin-1 at emit time. The change is that the emitter encodes it UTF-8 and computes the code-point count and the ASCII flag.

The IR change that is *not* optional is the `irVersion` bump. `src/pyscriptc/ir.py:15` holds `IR_VERSION = 1`, and `src/pyscriptc/ir.py:341` refuses a module whose `irVersion` does not match. The `str` payload shape changes, so the ABI between the compiler and the runtime changes, and the IR version is the stamp that says so. The bump to `IR_VERSION = 2` lands in S1.

The `subset` stamp stays `gates-v1`. The gate set does not change: the same programs lower, they just have different string semantics. The `.agents/docs/invariants.md` rule "The subset string stays `gates-v1` until a deliberate revision of the language" is about the language, and the language surface is unchanged.

There is a second, smaller ABI change that S1 has to land: the payload widening itself. `%ps.str` goes from `{ i64, ptr }` to `{ i64, ptr, i64, i32 }`. That is an emitter change (`src/pyscriptc/emit/llvm/_core.py:218` reads field 0, `_print.py:58`, `_calls.py:33`, `_stdlib.py` reads many) and a runtime change (`PsBuf` stays 16 bytes, but a new `PsStr` header type appears in `ps_rt.h`).

The invariants file says "PsBuf must stay 16 bytes" and "`str` and `bytes` cross the C ABI as `%ps.str = { i64, ptr }`". Both lines need updating in the same PR as the payload change.

### The frozen gates

`benchmarks/compat2/probes/IR_HASHES.json` is a frozen v2 gate (tag `gates-v2`, 2026-10-16). Changing the IR changes the hashes. The rules, from `AGENTS.md` and `.agents/notes/2026-10-22-ir-hashes-regen.md`:

- A change to any frozen gate file needs a recorded decision first. The decision for this work is the owner decision in `.agents/decisions.md` plus the per-phase decision notes.
- The regeneration is Linux-only. `.github/workflows/ir-hashes.yml` generates the baseline on `ubuntu-24.04` with CPython 3.14.x. The baseline is never regenerated on macOS: `canonical.py` folds host constants, and on macOS even the baseline's own commit differs by 458 hashes.
- The A/B discipline: run `python -m benchmarks.compat2.irscore` twice, `cmp` the two outputs, and only land the result if they are byte-identical.

The other frozen gates that move with this work:

- `benchmarks/compat2/probes/*.jsonl`. The probe sources are UTF-8 text and the probe expectations are CPython results. When the string model lands, a probe that prints or folds a non-ASCII string changes its expected value (the byte count becomes the code-point count). The probes are regenerated with `HEAVY=1 docker/run.sh uv run python -m benchmarks.compat2.probes.generate --oracle 3.14`, again on Linux only.
- `benchmarks/compat2/CALIBRATION.json` and `TRIAGE.md`. The calibration cells move when the probe outcomes move.
- `benchmarks/compat2/outcomes.py` and `score.py` weights. These do not change: the outcome vocabulary and the weights stay. The S7 receipt records that they did not change.

The compat2 corpus movement: 47 of the 663 visible corpus `.py` files contain non-ASCII characters (markupsafe, pyparsing, referencing among them). Their oracle expectations may move when stdout bytes change, because a program that printed Latin-1 bytes now prints UTF-8 bytes. The corpus oracle is regenerated in S7 and the delta is recorded as a census delta.

The holdout corpus stays closed to generator agents. The S7 work is done by an agent with the holdout guards in place, or by the owner.

### The interim honesty step

Until the model lands, a compiled program that prints non-ASCII bytes diverges from CPython silently. The cheapest honest fix is to make that divergence visible, so the contract in `docs/reference/divergences.md` stays true while the work is in flight.

The step is a compile-time diagnostic: when a program can emit a non-ASCII byte on stdout, the compiler says so. It is a warning, not a fence: the program still compiles and still runs, because a fence would break working programs for a difference that is documented and intended to be temporary.

The mechanism is a source-level scan at lower time: a `str` literal whose code points are not all below U+0080, or a `str` value whose bytes are known to be non-ASCII at emit time, raises a warning naming the line and the characters. The runtime side (a string that becomes non-ASCII through decoding) cannot be known at compile time, so the runtime side gets a separate note in the divergence doc.

The warning lands as a `PS2xxx`-family advisory with a distinct exit code, or as a stderr note from `compile` that does not change the exit code. The repo's `compile` currently exits 3 on a fence and 0 on success, so a warning must not change the exit code. The mechanism that fits is a diagnostic that prints to stderr with the `warning:` prefix and no exit-code change.

This step lands in the docs PR that opens this work, so the divergence doc is honest from the first commit of the epic, and the warning itself lands in S1.

## The masking bug

The memory note recorded a suspicion: "a `>255` literal in a `def` whose other fences are all DERIVED (not root) is not reported". The suspicion is partly wrong and the real bug is worse. Measured on 2026-10-24:

| Case | Result |
|---|---|
| `def f() -> str: s: str = "price: \u20ac5"; return s` | `PS2005` is reported, plus a `PS2002` name-resolution fence as a side effect. The `PS2005` fence aborts the statement, so `s` never enters `self.locals`, and the later `return s` raises a second, misleading fence |
| `def f() -> str: return "caf\u00e9".upper()` | **0 fences.** The restlower fold path (`restlower/_folds.py:73`) returns `None` for a non-Latin-1 string, the call falls through to a different path, and the literal is never fenced. The emitted IR carries `{'kind': 'const', 'value': 'café'}` and the emitter encodes it Latin-1 into `c"caf\E9\00"` |
| `def f() -> str: s: str = "café"; return s` (raw non-ASCII in the source) | **0 fences.** `é` is U+00E9, which is below U+0100, so it is inside the Latin-1 range and is not fenced. It is emitted as a Latin-1 byte. CPython emits UTF-8. This is the silent stdout divergence, and it is the case that matters most |
| `def f(x: int) -> int: y: int = x + "oops"; s: str = "price: \u20ac5"; return y` | all three fences reported: `PS2004`, `PS2005`, `PS2002`. The statement-level fence collection in `lower/__init__.py:842` does continue past a failed statement |

So there are three distinct defects, and the third is the one that matters:

1. The `PS2005` fence aborts the enclosing statement, which leaves the assigned name undeclared and produces a cascading `PS2002` "is not a local or parameter". The user sees two fences for one problem, and the second one points at the wrong thing.
2. The restlower fold path (`restlower/_folds.py:73`) silently drops a non-Latin-1 literal instead of fencing it. A `>255` literal reached through that path is not reported at all. This is the "masking" the memory note suspected, and it is a real bug, but it is narrower than suspected: it is the fold path, not the derived-fence logic.
3. A Latin-1 literal is not fenced and produces a byte stream that differs from CPython. This is the silent stdout divergence, and it is the one the interim step must fix.

The fix for (1) is to make the `PS2005` check happen before the statement's other lowering, so the name still binds. The fix for (2) is to make the fold path raise the fence instead of returning `None` for a non-Latin-1 `str` const. The fix for (3) is the interim warning.

The blast radius of the S1 fix must be measured before it lands: the corpus has 117 visible files with a `>255` literal, and only 18 report `PS2005`. Fixing (2) will make more of them report, which moves the compat2 fence counts. The census delta is the S1 receipt.

## Phases

Every phase is independently landable and leaves the tree green: `mise run check` (lint, format-check, compat2 unit selection), `mise run test` (the full suite) and the CI checks (`betterleaks`, `ruff and compat2`, `zig cc emitted-IR smoke`, `wheel install smoke`).

### S1: the decision record, the IR and ABI bump, and the masking fix (issue #473, milestone v0.7.0, effort M)

Land the recorded decision, bump `IR_VERSION` to 2, widen `%ps.str`, and fix the fence defects.

Work:

- The decision note: `.agents/docs/spec/str-model.md` (new), with the representation choice, the payload shape, the index strategy, the surrogate policy and the bytes boundary, written down. This is the design doc the plan points at, and it is where the alternatives and the rejected ones live.
- `IR_VERSION = 2` in `src/pyscriptc/ir.py`, with the emitter and the validator updated together.
- The `%ps.str` payload widening to `{ i64, ptr, i64, i32 }`: byte length, data pointer, code-point count, flags.
- The emitter computes the code-point count and the ASCII flag for every constant string, and every construction site sets all four fields.
- The runtime gains a `PsStr` header helper in `ps_rt.h` and the constructors that compute the count once.
- The masking fix: the `PS2005` check moves before the statement's binding so the name still binds (defect 1), and the restlower fold path fences instead of dropping (defect 2).
- The interim warning: a program that can emit non-ASCII bytes gets a compile-time warning naming the line (defect 3, the interim honesty step).
- The invariants file update: the `str` payload line and the `PsBuf` line.

Receipts:

- `pyscriptc compile` on a program with a Latin-1 literal warns; the same program still compiles and runs, exit code unchanged.
- `pyscriptc coverage` on the restlower-fold case now reports `PS2005`.
- The `PS2005` case reports one fence, not two.
- The compat2 census delta: the fence counts move, and the delta is recorded.
- `IR_HASHES.json` regenerated on Linux with the A/B discipline, in the same PR, after the recorded decision.

Tests:

- A unit test for the payload: the emitter emits the four fields, the count matches the code-point count, the flag matches ASCII-ness.
- A unit test for the fence fixes: the three cases in the table above.
- A unit test for the warning: the warning fires on a Latin-1 literal and does not change the exit code.

Dependencies: none. This is the first phase and it blocks everything else.

Collision risk: the emitter widening touches `emit/llvm/_core.py`, `_print.py`, `_calls.py` and `_stdlib.py`. The in-flight lanes (generators, OOP) touch `lower/`, `coro.py` and the class path, not the emitter payload. The overlap is in `emit/llvm/_calls.py`, where the OOP lane adds protocol dispatch. S1 should land first and the OOP lane should rebase onto it.

### S2: the core runtime (issue #474, milestone v0.8.0, effort XL)

The `str` primitives in `src/pyscriptc/runtime/ps_rt.c` (2186 lines) and the shared helpers.

Work:

- The UTF-8 codec: `ps_utf8_encode` and `ps_utf8_decode` become real codecs that handle one, two, three and four byte sequences, reject overlong forms, reject surrogates, reject code points above U+10FFFF, and raise `UnicodeEncodeError` / `UnicodeDecodeError` where CPython does.
- The Latin-1 codec, kept as a named pair so `encode("latin-1")` and `decode("latin-1")` have the CPython boundary behavior (the encoder raises above U+00FF).
- The code-point count: a scan that counts non-continuation bytes, plus a constructor that sets it.
- `ps_str_get`: the code-point index, not the byte index.
- `ps_str_slice`: code-point bounds, with a step of 1 for now (the step of 2 is a separate follow-up, matching the existing "slices with a step of 1" scope).
- `ps_str_find`, `ps_str_rfind`, `ps_str_count`, `ps_str_index`: code-point offsets.
- `ps_str_split`, `ps_str_join`, `ps_str_splitlines`, `ps_str_split_m`: code-point aware.
- The case family (`ps_case` and the `upper`/`lower`/`title`/`capitalize`/`casefold`/`swapcase` family): ASCII fast path, and a Unicode case mapping table for the rest. CPython's `str.upper()` uses full case mapping, including the multi-character expansions (`ß` to `SS`). A full table is a data problem: it comes from `UnicodeData.txt` and `SpecialCasing.txt` in CPython 3.14.
- The comparison path stays `memcmp` (UTF-8 preserves code-point order), so no change beyond a comment that says why.
- `ps_str_eq` and `ps_str_cmp` stay bytewise, with the reasoning documented.
- The `ps_str_startswith` / `ps_str_endswith` family: code-point aware.
- `ps_str_replace`, `ps_str_strip` and friends: code-point aware at the boundaries.
- `ps_str_zfill`, `ps_str_justify`: width in code points.
- `ps_str_expandtabs`, `ps_str_removeprefix`, `ps_str_removesuffix`, `ps_str_partition`, `ps_str_rpartition`: code-point aware.

Receipts:

- A parity probe set: for every `str` method in the subset, a probe with a non-ASCII operand that compares against CPython 3.14 byte-for-byte. The probe set is the S2 receipt and it is the artifact S7 calibrates.
- `len("café")` is 4, `s[3]` is `é`, `s[1:3]` is `af`, `s.upper()` is `CAFÉ`, `"ß".upper()` is `SS`.
- The IR hash delta is recorded.

Tests:

- One test per `str` method with a non-ASCII operand, compared against a CPython-produced expectation.
- Codec tests: the four-byte round trip, the overlong rejection, the surrogate rejection, the `UnicodeDecodeError` shape.

Dependencies: S1.

Collision risk: **high**, and this is the phase to watch. `ps_rt.c` is the file the boxing ledger and the threading lanes also touch. The rule for this plan: the S2 branch owns `ps_rt.c` for the duration of S2, and any other lane that needs `ps_rt.c` lands after S2 or merges with an explicit rebase. `ps_rt.c` is not touched by the generators lane or the OOP lane in the dispatch queue as it stands, but the map/set index work (#382) that just landed did touch it, so the precedent is set.

### S3: `ps_more` and `ps_fmt` (issue #475, milestone v0.8.0, effort L)

`src/pyscriptc/runtime/ps_more.c` (1073 lines) and `src/pyscriptc/runtime/ps_fmt.c` (1039 lines).

Work:

- `ps_fmt.c`: the `str.format` engine. The width and precision in a format spec are code-point counts, and the padding is applied in code points. The `{}` field parsing stays as it is; the width arithmetic changes.
- `ps_more.c`: the `str` methods that live there (`center`, `ljust`, `rjust`, `count`, `format_map`, `isalnum` and the `is*` predicates, `maketrans`, `translate`, `encode`, `format`, `format_map`), plus the `bytes` methods that are not in scope.
- The `is*` predicates: `isdigit`, `isalpha`, `isalnum`, `isspace`, `isupper`, `islower`, `isnumeric`, `isdecimal`, `isprintable`, `iscapitalize`, `istitle`, `isascii`. The current ones are bytewise. The Unicode ones need a category table, which is `ps_unicode.c`'s data (S4). S3 either lands the ASCII-correct versions plus a fence for the Unicode-only cases, or waits for S4. The recommendation: S3 lands the ASCII-correct versions and the fences, and S4 lifts the fences.
- `ps_repr_str`: the `repr` of a `str`, which CPython escapes as `'\xe9'` for Latin-1 and `'\u20ac'` for a code point above U+00FF, with the exact escape rules of CPython's `repr`.

Receipts:

- A parity probe set for `format`, the `is*` predicates and `repr`.
- `f"{'café':>8}"` matches CPython, `repr("café")` is `"'caf\\xe9'"`, `repr("€")` is `"'\\u20ac'"`.

Tests:

- Format spec width and alignment with non-ASCII operands.
- The `repr` escape table, compared against CPython for a set of code points.

Dependencies: S2.

### S4: `ps_re` and `ps_unicode` (issue #476, milestone v0.8.0, effort XL)

`src/pyscriptc/runtime/ps_re.c` (1356 lines) and `src/pyscriptc/runtime/ps_unicode.c` (1521 lines).

Work:

- `ps_unicode.c`: the `unicodedata` tables move from Latin-1 to the full range. The current file holds a 256-entry category table. The full CPython 3.14 tables are large (`UnicodeData.txt` is about 2 MB of source). The decision is whether to ship a compact table (categories and case mappings only, packed) or to embed the full data. The recommendation: ship the packed tables that the runtime actually needs (general category, simple case mapping, full case mapping, and the normalization-relevant properties) as generated C tables, and generate them from the CPython 3.14 data with a script that records the Unicode version. The generator is a new file under `scripts/`, and its output is committed. The `unidata_version` stays 16.0.0 (CPython 3.14 ships Unicode 16.0.0), and the divergence doc records it.
- `ps_re.c`: the regex engine works on code points. The pattern compiler turns a pattern into code points, the matcher compares code points, `.` matches one code point (not one byte), `\w`, `\d` and `\s` use the Unicode definitions, and the group offsets are code-point offsets. The engine is a byte-oriented backtracking subset today (divergence 4), and the engine's core is a byte loop. Making it code-point based is a rewrite of the inner loop, not a patch. The byte-offset results that `find` returns become code-point offsets.
- `ps_unicode.c` also carries the normalization functions. CPython's `unicodedata.normalize` implements NFC/NFD/NFKC/NFKD. The current implementation raises `ValueError` for any normalization that would leave Latin-1. The new implementation needs the canonical decomposition and composition tables. That is a large data problem and it is the single biggest item in the epic. The recommendation: land NFC/NFD/NFKC/NFKD for the full range through the same generated-table mechanism, in S4, because a `str` model that cannot normalize is a new divergence the moment a program normalizes a non-ASCII string.

Receipts:

- A parity probe set for `unicodedata` (category, name, numeric value, normalization) and for `re` with non-ASCII patterns and subjects.
- `re.findall(r"\w+", "café")` matches CPython.
- `unicodedata.normalize("NFC", "é")` matches CPython.

Tests:

- The category table against CPython for a sample of code points across the planes.
- The normalization round trips.
- The regex engine against CPython for a set of patterns with non-ASCII input, including `.`, `\w`, `\d`, `\s`, and a case-insensitive match.

Dependencies: S2 (the codec and the code-point primitives), S3 (the `is*` predicates that use the tables).

Collision risk: `ps_unicode.c` is a self-contained data file and nobody else touches it. `ps_re.c` is touched by the regex work only.

### S5: the codecs and the data formats (issue #477, milestone v0.9.0, effort L)

`ps_json.c` (683), `ps_csv.c` (160), `ps_toml.c` (791), `ps_yaml.c` (1006).

Work:

- `ps_json.c`: the JSON string writer and reader. CPython's `json.dumps` escapes non-ASCII by default (`ensure_ascii=True`), so a `str` with a code point above U+007F becomes a `\uXXXX` escape. The current implementation has no non-ASCII text at all (divergence 5). The new implementation escapes per CPython's rules, with the surrogate pair form for code points above U+FFFF. The reader decodes the escapes back to code points.
- `ps_csv.c`: the CSV writer and reader. The field separator handling is bytewise today. With a code-point model, a `str` field is a code-point sequence and the CSV quoting rules apply to it. The CSV module in CPython works on `str`, so the file I/O in the module is text mode with the newline handling CPython has.
- `ps_toml.c` and `ps_yaml.c`: the parsers read a document and produce `str` values. The parsers work on bytes today; they have to work on code points. The TOML basic strings have the same escape rules as JSON plus the multiline forms. The YAML subset has its own quoting rules.
- The `orjson` pack (`restlower/_orjson.py` and `ps_json.c`) rides along: `orjson.dumps` never escapes non-ASCII (it emits UTF-8), which is the opposite of `json.dumps`, and that difference has to hold.

Receipts:

- A parity probe set for `json.dumps`, `json.loads`, `orjson.dumps`, `csv` and the TOML and YAML subsets, with non-ASCII payloads.
- `json.dumps("café")` is `"caf\\u00e9"` in CPython, and `json.dumps("café", ensure_ascii=False)` is `"café"` in UTF-8 bytes.
- `orjson.dumps("café")` is the raw UTF-8 bytes.

Tests:

- The JSON escape table, including the surrogate pair form.
- The CSV quoting with a non-ASCII field and an embedded newline.
- The TOML and YAML basic-string escapes.

Dependencies: S2 (the codec), S4 (the `unicodedata` data, for the YAML and TOML name escapes).

### S6: I/O, stdin, stdout, files, argv and env (issue #478, milestone v0.9.0, effort M)

`ps_io.c` (337), `ps_argv.c` (28), `ps_osmore.c`, and the emit path for `print`.

Work:

- The text I/O default is UTF-8 (PEP 540, PEP 686). `open(path, "r")` decodes UTF-8 by default; `open(path, "w")` encodes UTF-8 by default. The `encoding=` argument is either honored for a named codec or fenced with a named diagnostic.
- `print` writes UTF-8 bytes. The emitter's `string_value` encodes the literal as UTF-8 (`emit/llvm/_print.py:20`).
- `sys.stdin`, `sys.stdout` and `sys.stderr` are text streams with UTF-8. The `input()` function (`ps_input_line` in `ps_io.c:323`) reads bytes and decodes UTF-8, with the newline handling CPython has.
- `sys.argv` (`ps_argv.c`) decodes the OS argv from UTF-8. The OS gives bytes; the surrogateescape story is out of scope per the surrogate policy, so an argv that is not valid UTF-8 is a named error, not a silent replacement.
- The environment (`os.environ`) decodes UTF-8.
- The file paths: `ps_path.c` and `ps_osmore.c` operate on bytes for the OS calls, and the `str` values are encoded UTF-8 on the way down and decoded on the way up.
- The `print` of a `str` that holds a code point above U+FFFF is 4 UTF-8 bytes.

Receipts:

- A parity probe set for file read and write with non-ASCII content, for `print`, for `input()`, for `sys.argv`, and for the environment.
- A byte-level check: the compiled program's stdout for `print("café")` is `63 61 66 c3 a9`, byte-identical to CPython.

Tests:

- A file round trip with a non-ASCII payload, compared byte-for-byte with CPython.
- A `print` byte comparison.
- An `argv` test with a non-ASCII argument.

Dependencies: S2 (the codec), S5 (the JSON and CSV writers, which produce text).

### S7: the corpus, the probes, the oracle and the divergence doc (issue #479, milestone v0.9.0, effort L)

The evidence work.

Work:

- The probe regeneration: `benchmarks/compat2/probes/*.jsonl`, on Linux, with the `--oracle 3.14` set. The probe sources that carry a non-ASCII literal change their expectations.
- The IR hash regeneration: `python -m benchmarks.compat2.irscore` twice on Linux, `cmp` byte-identical, then `benchmarks/compat2/probes/IR_HASHES.json` lands.
- The calibration: `benchmarks/compat2/CALIBRATION.json` and `TRIAGE.md` move with the probe outcomes.
- The corpus oracle: the 47 visible corpus files with non-ASCII characters re-run against CPython, and the delta is recorded as a census delta.
- `docs/reference/divergences.md` item 3 is rewritten. It currently says "Strings are a Latin-1 byte model. A string literal outside Latin-1 is a fence, and `unicodedata` covers only Latin-1 code points." The rewrite says that `str` is a code-point sequence stored as UTF-8, that text I/O is UTF-8, and that the remaining differences are the ones that survive: the surrogate policy, the codecs that are not implemented, the normalization forms that are not implemented, and the `re` engine's subset nature.
- The support table (`scripts/support_data.toml` and the generated pages) moves, because the `str` surface changes.
- The `benchmarks/compat2/outcomes.py` and `score.py` weights do not change. The S7 receipt records that they did not change, which is the check that the frozen gates moved only where the model moved.

Receipts:

- The census delta for the corpus, before and after.
- The compat2 score, before and after, with the probes that moved named.
- The rewritten divergence item 3, with a link to the design doc.
- The `IR_HASHES.json` A/B proof: two runs, `cmp` clean.

Tests:

- The whole compat2 suite green, with the regenerated probes.
- A regression guard: a test that asserts the divergence doc's numbered list has no duplicate numbers and that each item links a source.

Dependencies: S2 through S6 all land before S7.

## Ordering against the in-flight work

The dispatch queue (#453) has the generators lane (G1 to G5) and the OOP lane (O1 to O6) in flight, plus the library track. The str epic touches the same files in two places.

The collision map:

| File | Str epic | In-flight lane | Rule |
|---|---|---|---|
| `src/pyscriptc/runtime/ps_rt.c` | S2 owns it | the boxing ledger and the threading lanes touch it | S2 owns `ps_rt.c` for the duration; other lanes rebase after |
| `src/pyscriptc/emit/llvm/_calls.py` | S1 (the payload) | the OOP lane adds protocol dispatch | S1 lands first; the OOP lane rebases |
| `src/pyscriptc/lower/_expr.py`, `restlower/_folds.py` | S1 (the fence fixes) | the generators lane touches `lower/` | S1 is a small, surgical change; land it early |
| `src/pyscriptc/ir.py` | S1 (the version bump) | nothing | none |
| `src/pyscriptc/runtime/ps_unicode.c` | S4 | nothing | none |
| `src/pyscriptc/runtime/ps_re.c` | S4 | nothing | none |

The ordering recommendation:

1. S1 first, and quickly. It is small, it unblocks the rest, it fixes a real fence bug, and it makes the divergence visible. It rides along any window.
2. S2 next, and it is the long pole. It owns `ps_rt.c` for its duration. Start it as soon as S1 lands.
3. S3 and S4 after S2. S3 is L and S4 is XL; they can run in parallel with each other, since they touch different files.
4. S5 after S4 (the data formats need the `unicodedata` tables for the name escapes).
5. S6 after S2 and S5.
6. S7 last, and it is evidence, not code.

The generators lane does not touch `ps_rt.c` or the emitter payload as it stands, so S1 and S2 can run alongside it. The OOP lane touches `_calls.py`, which S1 also touches; that is the one real conflict, and it resolves by S1 landing first.

The library track (the `#428` requests family) depends on programs that read and write text. The str model makes those programs correct rather than wrong, so the library track does not block on this, but a library receipt measured on the old model has to be re-measured after S7. The recommendation is to accept that and say so in the library receipts.

The milestones, per the owner's instruction and the analysis above:

| Phase | Issue | Milestone | Horizon | Effort |
|---|---|---|---|---|
| S1 | #473 | v0.7.0 | Current | M |
| S2 | #474 | v0.8.0 | Next | XL |
| S3 | #475 | v0.8.0 | Next | L |
| S4 | #476 | v0.8.0 | Next | XL |
| S5 | #477 | v0.9.0 | Later | L |
| S6 | #478 | v0.9.0 | Later | M |
| S7 | #479 | v0.9.0 | Later | L |

The analysis behind the split: S1 is a decision and a scaffolding change, it belongs in v0.7.0 with the other small items. S2, S3 and S4 are the compiler core, and v0.8.0 is the compute-two-plane minor, which is the right window for a runtime that changes the string representation of every program. S5, S6 and S7 are the data formats, the I/O and the evidence, and v0.9.0 is the library-depth minor, where the corpus receipts matter most.

## Risks

| Risk | Mitigation |
|---|---|
| The payload widening breaks the emitter and the runtime at once, and S1 is a large diff | S1 changes the payload shape but not the semantics: the count field is filled with the byte count for now, so the behavior is unchanged and the diff is mechanical. The semantic change lands in S2 |
| The `ps_rt.c` collision with another lane | S2 owns the file; the rule is written down above |
| The Unicode data tables are a large amount of generated code in the tree | the generator is committed with its output and the Unicode version is stamped; the tables are packed, not raw |
| The regex rewrite is bigger than the rest of the epic | S4 is XL and it is the phase that can slip; it does not block S5's codecs, only the `re` part of it does |
| The frozen gates move and the IR hash baseline is regenerated on the wrong platform | the A/B discipline and the Linux-only rule are in the S1 and S7 checklists; the macOS regeneration is forbidden by the note in `.agents/notes/2026-10-22-ir-hashes-regen.md` |
| The corpus oracle expectations move and the score drops instead of rising | the S7 receipt records both numbers; a drop is a finding, not a failure to hide |
| The interim warning is noisy for programs that are correct | the warning fires only when the compiled program can emit non-ASCII bytes, which is exactly the diverging case |
| The scope creeps to `bytes` methods and the whole `re` engine | the bytes boundary is written down above and the `re` engine's subset nature is already divergence 4 |

## The interim honesty step, in detail

This is the part that lands first and that makes the contract true while the epic runs.

Today: `print("café")` compiles clean, runs, and writes `63 61 66 e9` where CPython writes `63 61 66 c3 a9`. The divergence doc does not say this. The contract says everything not listed is identical, so the contract is broken.

The step has two halves.

The first half is the doc. `docs/reference/divergences.md` item 3 gets a sentence that names the observable consequence: a `str` is a Latin-1 byte sequence, and a program that prints a code point above U+007F writes bytes that differ from CPython's UTF-8 output. That sentence lands in the docs PR that opens this work, before any code moves.

The second half is the diagnostic. A compile-time warning that fires when the compiled program can emit a non-ASCII byte on stdout. The check is at lower time, on the `str` literals and on the constant folds, and it names the line and the characters. It is a warning, not a fence: the program still compiles, the exit code is unchanged, and the message says what will differ.

The diagnostic does not cover the runtime side, where a string becomes non-ASCII through decoding. That case is covered by the doc sentence, which is why the doc sentence comes first.

When S2 lands, the warning stops firing for the ASCII-only case and stays for the genuinely non-ASCII case, and the doc sentence is rewritten as part of the S7 divergence rewrite.
