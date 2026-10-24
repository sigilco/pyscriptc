# The str model: UTF-8 storage with CPython code-point semantics

Status: decided 2026-10-24 (owner decision, `.agents/decisions.md`, entry "The `str` model is the CPython code-point model"). This is the S1 design record for epic #472. S1 (#473) lands the payload shape and the scaffolding; the semantics land phase by phase (S2 to S7).

`str` is a sequence of Unicode code points, stored as UTF-8 bytes. Every operation CPython defines over code points is defined over code points here. Text I/O reads and writes UTF-8 by default (PEP 540, PEP 686). `bytes` stays a byte sequence.

## The representation choice

Option 1, chosen: UTF-8 bytes with a cached code-point count and an ASCII flag.

- The common case stays free. Most programs' strings are ASCII, and for those the byte count equals the code-point count, so the bytewise paths keep working with no scan.
- `len` stays a field read. CPython's own `len` on a compact `str` is an O(1) field read, and a length scan on every call would be a complexity regression issue #386 would have to name.
- The memory profile stays at one byte per code point for ASCII and one to four bytes per code point for the rest, which is the cheapest of the candidates.

Option 2, rejected: a code-point array (UCS-4 or UCS-2) with UTF-8 only at the I/O boundary. This is what CPython does (PEP 393 keeps a compact per-kind representation, not a flat UCS-4 array), and it makes every operation an array walk, but it costs four bytes per code point for every program, including the ones that never see a non-ASCII character, and it turns every print and file write into a conversion. CPython can afford PEP 393 because it has a promotion mechanism and a GC; this runtime has neither.

Option 3, rejected: UTF-8 bytes with a lazily computed, cached code-point count and no ASCII flag. A lazy count turns `len` into a potential payload mutation, which is wrong for a value type that crosses the ABI by copy: the emitter passes `%ps.str` in registers and on the stack, and two copies of the same string would cache the count independently. The count is computed once at construction, where every constructor already walks the bytes.

## The payload

Today the payload is `%ps.str = { i64, ptr }`: a byte length and a data pointer. The widened payload is:

```
%ps.str = type { i64, ptr, i64, i32 }   ; byte length, data pointer, code-point count, flags
```

- Field 0 stays the byte length. Every byte-oriented path (write, `printf`, `bytes` interop) already reads it, and changing its meaning would touch every helper at once. Nothing else in the ABI depends on field 0 being the only length.
- Field 2 is the code-point count. For an ASCII string it equals field 0.
- Field 3 is the flags word. Bit 0 means "ASCII" (byte count equals code-point count). Bit 1 is reserved and must be zero.

The rejected payload variants:

- Variant A, a side allocation: the payload holds a pointer to a `PsStrHead` plus the data pointer, and the header lives in its own allocation. Two allocations per heap string, a double free path, and a pointer chase on every field read.
- Variant B, an in-band header at a negative offset: the data pointer points into the middle of one allocation with the header immediately before it. One allocation, but unsafe if any code path ever constructs a string from a pointer it did not allocate, and the emitter's constant strings would each need a constant header global plus a GEP.
- Variant C, chosen: carry the header inline in the payload and keep the pointer at the bytes. Every construction site sets all four fields. The emitter's constant path computes the count and the flag from the source text at compile time. The runtime's allocation paths maintain them incrementally, which is the point of the cached count. The cost is 16 more bytes per string value on the stack (the struct is copied by value in the LLVM IR) and zero extra allocations.

The invariant "a `str` crosses the C ABI as a pointer or by copy, never as a returned struct" is preserved: the struct is only ever copied into and out of memory, and the one runtime function that returns `PsBuf` by value (`ps_exc_str`) keeps doing so in both shapes. `PsBuf` itself stays 16 bytes; it is the `bytes` carrier and it does not grow.

## S1 shape without semantics

S1 changes the shape, not the behavior. In S1:

- The emitter computes the code-point count and the ASCII flag for every constant string and emits all four fields.
- Every construction site in the emitter sets all four fields.
- The runtime's string constructors fill the count and the flag with the values they can compute for free; a helper that cannot compute the count yet carries it equal to the byte count and clears the ASCII flag conservatively. Behavior is unchanged: a non-ASCII literal is still Latin-1 bytes under the current model until S2 replaces the encoders.
- `len` keeps reading field 0. The switch to field 2 happens in S2, when the count becomes the true code-point count.

This keeps the S1 diff mechanical and reviewable, and it means the emitted IR of an ASCII-only program changes only in the payload shape, never in behavior.

## Index and slice strategy

`len(s)` is a read of field 2 (S2 onward). O(1), always.

`s[i]` returns the i-th code point. For an ASCII string the index is the byte offset and the access is O(1). For a non-ASCII string the runtime scans the UTF-8 bytes from the start, counting non-continuation bytes until it reaches code point i, then copies that code point's bytes into the result. That is O(n) per index.

Iteration is a cursor walk, not repeated indexing: `for ch in s` advances a byte cursor by the current code point's width, which makes the whole loop O(n). CPython's own iterator is a cursor walk. The lowerer's existing `for` desugaring must hold this property: an index-cursor loop that calls `s[i]` per step would be O(n squared), and that shape is refused in review.

Random access (`s[i]` with a computed i), slicing with bounds, `find` and friends pay the O(n) scan on non-ASCII strings and O(1) on ASCII ones. The scan stops at the requested index, so the average cost is half the string. A per-string offset table (4 bytes per code point, built lazily, invalidated by mutation) was considered and deferred: it adds an allocation, a thread-safety rule (the runtime has threads now), and an invalidation invariant, and no benchmark has shown the scan matters. It is the documented follow-up if one does. Issue #386 owns classifying every length scan in the runtime, and the str scans land in that ledger like the others.

## Comparison and hashing

`==` and the ordering comparisons stay `memcmp`. UTF-8 is a bijection between code-point sequences and byte sequences, and it preserves code-point order, so byte equality is code-point equality and byte order is code-point order. This is a property of the encoding, not an approximation, and it is why the comparison path needs no change. `ps_str_eq` and `ps_str_cmp` stay bytewise, with a comment in the runtime that says why.

Hashing does not chase CPython: `hash(str)` in CPython is siphash, seed-randomized per process, and the runtime never claimed parity. What must hold is that hashing stays consistent within a run so `dict` and `set` lookups keep working. The fold boundary in `restlower/_folds.py` (hash folds only on int and bool) is unchanged by this epic.

## Surrogates and the escape policy

A `str` holds code points U+0000 through U+10FFFF excluding the surrogate range U+D800 through U+DFFF. Storage is UTF-8, which cannot represent a surrogate.

- A source literal naming a surrogate (`"\ud800"`) fences with a named diagnostic, before and after the model lands.
- `surrogateescape` decoding is out of scope for the first pass and fenced with a named diagnostic. It is the one place CPython produces lone surrogates from ordinary I/O, and its gap is recorded as a numbered divergence.
- The WTF-8 style workaround (storing surrogates as a special three-byte sequence) was rejected: it is a non-standard encoding that every scan, count and comparison would have to understand, and it buys only `surrogateescape`, which is fenced anyway.

The escape policy for literals: source files are UTF-8 (PEP 263 default). A `"\uXXXX"` escape decodes at compile time to the code point it names. A `"\N{...}"` escape resolves through `unicodedata` and fences when the name is unknown. A `"\xXX"` escape in a `str` literal is the code point U+00XX (CPython's rule); in a `bytes` literal it is the byte.

## The bytes boundary

`bytes` is an immutable byte sequence, exactly as today, and it is not affected by this epic except where a codec explicitly converts. The boundary rules are CPython's:

- `str.encode("utf-8")` returns bytes; `str.encode()` with no argument is UTF-8.
- `bytes.decode("utf-8")` returns `str`; `bytes.decode()` with no argument is UTF-8.
- `str` and `bytes` do not compare equal, do not concatenate, and do not hash the same.
- `b"\xe9"` is one byte, not a code point.
- `bytes.decode("latin-1")` maps each byte to the code point of the same value; `str.encode("latin-1")` raises `UnicodeEncodeError` for any code point above U+00FF.

The runtime's current `ps_utf8_encode` and `ps_utf8_decode` are Latin-1 converters in UTF-8 clothing: they handle two-byte sequences only and reject any code point above U+00FF. S2 replaces them with a real UTF-8 codec (one to four byte sequences, overlong rejection, surrogate rejection, `UnicodeEncodeError` and `UnicodeDecodeError` where CPython raises) plus a real Latin-1 codec with the CPython boundary behavior.

## The IR and the ABI version

The IR needs no new node kind: `const` already carries the source text of a string, and the emitter is where the encoding happens. What changes is the stamp. The payload shape is part of the ABI between the compiler and the runtime, and `IR_VERSION` is the stamp that says which ABI a module speaks.

- `IR_VERSION` moves from 1 to 2 in `src/pyscriptc/ir.py`. The validator refuses a module whose `irVersion` does not match, so an old IR file cannot be fed to the new emitter and silently misread.
- The `subset` stamp stays `gates-v1`. The gate set does not change: the same programs lower before and after. The invariants rule about the subset string is about the language surface, and S1 does not touch it.
- The five `"irVersion": 1` literal sites (`lower/__init__.py`, `package.py`) move to the shared constant in the same commit, so the stamp has one definition.

Frozen gates: the payload change moves `benchmarks/compat2/probes/IR_HASHES.json` (every module's canonical form changes). The gate-change decision is recorded in `.agents/decisions.md` (entry "S1 moves the frozen v2 gates for the str payload"). The regeneration runs on Linux only, through the `ir-hashes` workflow's A/B byte-identical discipline, never by hand on macOS. `outcomes.py`, the `score.py` weights, `coverage_map.py` and `cache.py` do not change.

TODO (this PR, before merge): regenerate `IR_HASHES.json` through the `ir-hashes` workflow (`workflow_dispatch` on this branch), verify the A/B run is byte-identical (`cmp`), and copy the artifact into `benchmarks/compat2/probes/`. The baseline header also stamps the compiler ABI through the lowered modules, so it must land in the same PR as the `IR_VERSION` bump, and CI's irscore check stays red until it does.

## What does not change in S1

- The Latin-1 encoding of literals (`emit/llvm/_print.py` `string_value` keeps `encode("latin1")` for now; the UTF-8 switch is S2/S6 work with the I/O half).
- The runtime helpers' bytewise behavior.
- The fence rules: a literal outside Latin-1 still fences `PS2005` until S2 makes it lowerable, and the interim non-ASCII warning (also S1) covers the Latin-1-visible divergence in the meantime.
- `bytes` and `PsBuf` everywhere.
