---
title: CPython divergence audit
description: A seam-by-seam audit of where pyscriptc output diverges from CPython 3.14 byte-for-byte, an assessment of whether the repo knows about its divergences, and a version-alignment policy.
---

# CPython divergence audit (2026-10-24)

Assessment, not a code change. The compiler is untouched. The str code-point model is out of scope: a separate session owns the Latin-1 byte model fix (see the note at `/Users/josocjoq/.local/state/maki/projects/pyscriptc-4db3966c744d6379/memories/utf8-contract-question.md`). Where a finding below is caused by the Latin-1 model it is marked **str-model** and left to that session.

## Method

Every seam was probed by compiling a single-construct program with the worktree's own `pyscriptc` (`uv run pyscriptc compile FILE --emit bin`), running the native binary and CPython 3.14.9 (`~/.local/share/uv/python/cpython-3.14.9-macos-aarch64-none/bin/python3`) on the same source, and byte-comparing stdout, stderr and the exit code. A program that does not compile is recorded as a **fence** (a refusal), which is not a divergence by the repo's own contract. A program that compiles and produces different bytes is a **silent divergence** unless it is listed in `docs/reference/divergences.md`.

Probe sources live in `/tmp/maki/audit/p*` (not committed). The visible compat2 corpus (`benchmarks/compat2/corpus/src`, `benchmarks/compat2/probes/*.jsonl`) and `CALIBRATION.json` were read for context. The scorer evaluation set was never read.

Roughly 330 probe programs were compiled and run. About 190 lowered cleanly, about 130 fenced, and the rest are the findings below.

## Findings

Severity is judged by blast radius: **S1** means a program that lowers compiles to wrong bytes with no diagnostic, **S2** means a lowered program raises or exits differently from CPython, **S3** means the construct is fenced (a refusal, already honest) but the fence message or the documented story is wrong or missing.

| # | Seam | Evidence (probe and byte diff) | Silent or documented | Severity | Fix cost |
| --- | --- | --- | --- | --- | --- |
| D1 | `list.sort()` is a no-op on a module-scope list | `xs: list[int] = [3, 1, 2]` then `xs.sort()` then `print(xs[0])`: native `3`, CPython `1`. Also `sort(reverse=True)` on ints, floats, `str` and `bytes` elements, and `list.sort` inside a function that receives the list as a parameter (`f(xs)` where `f` does `xs.sort()`: native `3`, CPython `1`). **Root cause**: same as D2. The sort call re-emits the list constructor into a fresh sequence, sorts that, and discards it. The emitted LLVM (`--emit llvm`) shows two separate `ps_seq_new` allocations where CPython has one list. The sort runtime itself (`ps_seq_sort_i64`, `src/pyscriptc/runtime/ps_more.c:138`) is correct. | **Silent.** Not in `docs/reference/divergences.md`, not in `refused-features.md`, not in the supported table. The compat2 probe `builtins.list.sort` calibrates as `fence`, so the scorer never sees it. | S1 | M (follows D2) |
| D2 | Module-scope mutable containers have value semantics | `xs: list[int] = [1, 2, 3]` then `ys: list[int] = xs` then `ys[0] = 99`: native `1 1`, CPython `99 99`. Also: a module-scope `list` mutated through `xs[0] = 99` inside a function that receives it as a parameter (native `1`, CPython `99`); a `list` inside a module-scope `dict` mutated through `d["k"][0] = 99`; a `dict` inside a module-scope `list` mutated through `rows[0]["a"] = 99`; a module-scope `dict` mutated through a function that receives it. Function-local aliasing is correct (`ys[0] = 99` then `xs[0]` gives `99` in both). **Root cause**: a module-scope `list` or `dict` is stored as a *constant* (`_store_constant`, `src/pyscriptc/lower/__init__.py:1117`), not as a global slot, so every read re-emits the constructor. The emitted LLVM for the alias case contains three separate `ps_seq_new` calls where CPython has one list. The threading/`queue`/`asyncio` records are the exception: they take a real global slot (`_bind_threading_global`, `src/pyscriptc/lower/__init__.py:1135`) precisely because "a stored constant would re-emit the constructor at every read, so the record's identity would not hold". The same reasoning applies to a list, and it was not applied. | **Silent.** Not documented anywhere. The compat2 corpus contains no module-scope container aliasing, so the corpus cannot catch it. | S1 | L |
| D3 | `sum()` over floats is a naive left fold, CPython's is compensated | `sum([0.1, 0.2, 0.3])`: native `0.6000000000000001`, CPython `0.6`. `sum([0.1] * 10)`: native `0.9999999999999999`, CPython `1.0`. `sum([1e16, 1.0, -1e16])`: native `0.0`, CPython `1.0`. `sum([1.0, 1e100, 1.0, -1e100])`: native `0.0`, CPython `2.0`. The runtime's `ps_sum_f64` (`src/pyscriptc/runtime/ps_help.c:48`) is `acc += item` with no compensation; CPython's `sum` uses Neumaier compensation for floats. | **Silent.** Not documented. The compat2 probe `builtins.sum` is `sum([True, False])` (ints), so it calibrates as `fence` and never exercises the float path. | S1 | S |
| D4 | `round(x, n)` with `n < 0` raises instead of rounding | `round(1234.0, -2)`: native `ValueError: round ndigits is out of the lowered range`, exit 1; CPython `1200.0`, exit 0. `round(15, -1)` and `round(25, -1)` also raise natively. The runtime's `ps_round_nd` (`src/pyscriptc/runtime/ps_help.c:197`) rejects `nd < 0`. | **Documented, partially.** `docs/reference/divergences.md` does not list it. The one known `wrong-output` row in the frozen `CALIBRATION.json` is `lang.cpython.float.RoundTestCase.test_previous_round_bugs`, whose probe asserts exactly `round(25.0, -1) == 20.0` and `round(95.0, -1) == 100.0`. The divergence is **known to the calibration** but is not in the numbered list and is not in `TRIAGE.md`. | S2 | S |
| D5 | `round(x, n)` with `n > 8` raises instead of rounding | `round(1.5, 9)`: native `ValueError: round ndigits is out of the lowered range`, exit 1; CPython `1.5`, exit 0. `round(x, 9)` where `x` is a runtime float raises too. The `nd > 8` bound is in `ps_round_nd`. | **Silent.** Not documented. | S2 | S |
| D6 | `round(x, n)` for `n > 0` scales before rounding, CPython rounds the exact decimal | `round(2.675, 2)`: native `2.68`, CPython `2.67`. `round(16.05, 1)`: native `16.0`, CPython `16.1`. `ps_round_nd` multiplies by `10**nd`, rounds the scaled double, then divides, so the binary error of `2.675` is amplified. CPython's `_Py_dg_dtoa`-based path rounds the exact binary value. | **Silent.** Not documented. | S2 | M |
| D7 | `assert` raises `Exception`, not `AssertionError` | `assert 1 == 2, "one is not two"`: native stderr `Exception: one is not two`; CPython `AssertionError: one is not two` (plus a traceback). Catching it: `except AssertionError` does not match natively, `except Exception` does. The lowering is explicit (`src/pyscriptc/lower/_stmt.py:1183`: "`assert cond` is `if not cond: raise Exception`"), and `tests/test_assert.py:4` says "AssertionError is not a lowered exception", so the behaviour is deliberate. | **Silent to the user.** `docs/reference/divergences.md` item 12 says only "`assert` is always evaluated; a compiled binary has no `-O`". The exception type is not listed. `refused-features.md` does not mention it. | S2 | S |
| D8 | `str` tuple display omits the quotes on its `str` elements | `t = ("a", "b")` then `print(t)`: native `(a, b)`, CPython `('a', 'b')`. A tuple of ints, floats or bools matches. `str(tuple)` on a tuple containing `str` is the divergent shape. The runtime's tuple display path does not apply `repr` to string elements. | **Silent.** Not documented. | S1 | S |
| D9 | A NUL byte inside a `str` truncates the value | `s = "a\x00b"` then `print(s)`: native `a`, CPython `a\x00b`. `len(s)` matches (`3`) because the length is the byte count, so the value is silently truncated at the NUL. The string buffer carries the full length but the print path uses a NUL-terminated copy (`strlen` at `src/pyscriptc/runtime/ps_rt.c:2135` and `:2145`). | **Silent.** Not documented. Distinct from the str-model work: this is a NUL in the middle of an otherwise ASCII string. | S1 | S |
| D10 | Text-mode reads do not apply universal newline translation | A file containing `a\rb` read with `open(p, "r")`: native `a\rb`, CPython `a\nb` (CPython translates `\r` and `\r\n` to `\n` in text mode; `newline=""` disables it). The runtime's file read does no translation. | **Silent.** Not documented. | S1 | M |
| D11 | `json.dumps` does not escape non-ASCII | `json.dumps({"k": "caf\u00e9"})`: native `{"k": "caf\xe9"}` (raw latin-1 byte), CPython `{"k": "caf\u00e9"}`. `json.dumps` has no `ensure_ascii` keyword in the lowered subset. | **Documented, incompletely.** `docs/reference/divergences.md` item 5 says "`json` has no non-ASCII text", which reads as "non-ASCII is rejected", but the actual behaviour is "non-ASCII is emitted raw". The bytes that come out differ from CPython's. | S1 | S (str-model) |
| D12 | A `str` literal above Latin-1 is emitted as raw bytes | `print("caf\u00e9")`: native `caf\xe9`, CPython `caf\xc3\xa9`. Same for `chr(255)` (`\xff` against `\xc3\xbf`) and `"\xe9".upper()`. | **Documented as a model, not as a byte diff.** `divergences.md` item 3 describes the Latin-1 byte model but does not state that stdout bytes differ from CPython for any non-ASCII output. This is the str-model session's job. | S1 | XL (str-model) |
| D13 | `int` overflow aborts the process instead of growing a big int | `x = 4611686018427387904` then `print(x * 2)`: native stderr `OverflowError`, exit 1; CPython `9223372036854775808`, exit 0. Same for `x + x`, `1 << 63`, `pow(3, 40)`, `10 ** 19`. The stderr text is `OverflowError` alone, not CPython's `OverflowError: int too big to convert`. | **Documented.** `divergences.md` item 1: "`int` is `i64`. Overflow aborts the process instead of growing a big int." The stderr text difference is not stated but the contract only requires the exception name. | S2 | XL |
| D14 | `//` and `%` by zero raise instead of aborting | `print(1 // 0)`: native stderr `ZeroDivisionError: division by zero`, exit 1; CPython `ZeroDivisionError: integer division or modulo by zero` (on 3.13 and earlier) or `division by zero` (on 3.14). The native message follows the running interpreter's minor through `zdi_variants()` (`src/pyscriptc/ir.py:59`). | **Partly documented, partly stale.** `divergences.md` item 2 says "`//` by zero aborts the process instead of raising `ZeroDivisionError`". That is no longer true: the native binary raises `ZeroDivisionError`. The doc is stale and the behaviour is better than advertised. | S2 | S (doc) |
| D15 | Uncaught exceptions print no traceback | `raise ValueError("bad")`: native stderr `ValueError: bad`; CPython prints a full traceback with the source line and caret. Same for `IndexError`, `KeyError`, `ZeroDivisionError` and a `RuntimeError` raised inside a function. | **Documented.** `divergences.md` item 10 of the parity contract: "stderr: for a `raises` probe, only the exception name must appear. Full traceback text is not matched." The contract covers this. | S2 | L |
| D16 | `chr()` above 127 raises `ValueError` | `chr(300)`: native `ValueError: chr is ASCII in this subset`, exit 1; CPython `'\u044c'`. `chr(255)` works natively but emits the latin-1 byte, not CPython's UTF-8. | **Silent.** Not in `divergences.md`. `refused-features.md` does not cover `chr`. | S2 | S |
| D17 | `shutil.copyfile` of a missing source raises `RuntimeError` | `shutil.copyfile("/no/such", dst)`: native `RuntimeError: copyfile`, exit 1; CPython `FileNotFoundError` with the errno and path. | **Documented.** `divergences.md` item 11 names this family ("other `shutil` and `os` failures are `RuntimeError`"). | S2 | M |
| D18 | `os.mkdir` on an existing directory does not raise | `os.mkdir(existing)`: native prints `True` and exits 0; CPython raises `FileExistsError: [Errno 17] File exists`. | **Silent.** Not documented. | S2 | S |
| D19 | `dict.setdefault` and `dict.update` do not insert | `d.setdefault("b", 2)` then `d["b"]`: native `KeyError: 'b'`, exit 1; CPython `2`. `d.update({"b": 2})` then `d["b"]`: native `KeyError: 'b'`; CPython `2`. Both calls lower and are accepted, they just do not mutate: the same re-emit as D2, so the insertion lands on a throwaway `dict`. | **Silent.** Not documented. The `dict` row of the supported table lists `dict` as red. | S1 | M (follows D2) |
| D20 | `list.insert`, `list.extend` do not grow the list | `xs: list[int] = [1, 3]` then `xs.insert(1, 2)` then `print(xs[0], xs[1], xs[2])`: native `IndexError: list index out of range`, exit 1; CPython `1 2 3`. `xs.extend([2, 3])`: native `IndexError`; CPython `1 2 3`. Same re-emit as D2. `xs.append(2)` at module scope fences with `PS2002: list.append needs a name`, so `append` is refused but `insert` and `extend` are accepted and broken. | **Silent.** Not documented. | S1 | M (follows D2) |
| D21 | `list.remove` removes the wrong element | `xs: list[int] = [1, 2, 3]` then `xs.remove(2)` then `print(xs[0], xs[1])`: native `1 2`, CPython `1 3`. The runtime's `ps_seq_remove_i64` (`src/pyscriptc/runtime/ps_more.c:230`) is correct; the call site re-emits the list, so the removal lands on a throwaway copy. | **Silent.** Not documented. | S1 | M (follows D2) |
| D22 | `list.reverse` does not reverse | `xs: list[int] = [1, 2, 3]` then `xs.reverse()` then `print(xs[0], xs[1], xs[2])`: native `1 2 3`, CPython `3 2 1`. Same re-emit as D2; `ps_seq_reverse` (`src/pyscriptc/runtime/ps_more.c:98`) is correct. | **Silent.** Not documented. | S1 | M (follows D2) |
| D23 | `dict` iteration order after a delete-and-reinsert differs | Not reproducible in the current compiler: `dict` iteration over a module-scope `dict` and the delete-and-reinsert shape both fence. Recorded because the shape is exactly the one that would expose D2's copy. | N/A (fenced today) | S3 | n/a |
| D24 | `set` iteration order is insertion order | `set` iteration is documented as a divergence (item 6). The probe could not lower a module-scope `set` mutation, so the documented behaviour is currently unreachable at module scope. | **Documented.** | S3 | n/a |
| D25 | `hash()` of `str`, `bytes`, `float`, `tuple` is not lowered | `hash("abc")` fences with `PS2002: call to hash is not lowered`. CPython randomizes `str` and `bytes` hashes per process (`PYTHONHASHSEED=random`). `hash(int)` is lowered and matches CPython. | **Fenced.** Honest. `refused-features.md` T0 lists "`hash()` of int/bool" as a refusal for reproducibility reasons, but the actual fence covers `hash` of every non-int type, which the doc does not say. | S3 | S |
| D26 | `id()` is not lowered | `id(x)` fences with `PS2003: id is not lowered`. CPython's `id` is an address. | **Fenced, documented.** `refused-features.md` T0 lists `id()` and default `repr` as "process identity is non-reproducible by construction". | S3 | n/a |
| D27 | `__del__` is not lowered, no finalizers | `class P: def __del__(self) -> None: print("bye")` fences. CPython runs the finalizer at collection. `gc` and `weakref` fence too. | **Fenced, documented.** `refused-features.md` T0: "`__del__`/`weakref`/`gc` (PS4203 for the hook, `weakref`/`gc` calls still PS2002)". | S3 | n/a |
| D28 | `setrecursionlimit` is not lowered | Fences. CPython's default limit is 1000. | **Fenced, documented.** `refused-features.md` T0. | S3 | n/a |
| D29 | `re` is a byte-oriented backtracking subset | `re.sub(r"\d", "#", "a1b2")` matches CPython byte for byte. `re.sub` with a backreference matches. The pattern-level divergences are documented as item 4. | **Documented.** | S3 | n/a |
| D30 | `json` float, int, `None`, bool, indent, `sort_keys` shapes match | `json.dumps({"x": 0.1})`, `{"n": 9007199254740993}`, `{"n": None, "t": True, "f": False}`, `indent=2`, `sort_keys=True`, `separators` (fenced), `json.loads` round trip all match or fence. | Clean on the tested shapes. | S3 | n/a |
| D31 | `math`, `datetime`, `hashlib`, `base64`, `struct`, `urllib.parse`, `random.seed`, `time` match on the tested shapes | `math.sqrt/floor/ceil/trunc/isqrt/gcd/exp/log/hypot/isinf/isnan`, `datetime.date.isoformat/weekday/strptime`, `hashlib.sha256`, `base64.b64encode`, `struct.pack(">i", 5)`, `urllib.parse.quote_plus`, `random.seed(42)` then `random.random()`, `time.time/time_ns/monotonic/perf_counter/sleep` all match byte for byte. `math.fsum` and `math.prod` are not in the lowered subset (fence). | Clean on the tested shapes. | S3 | n/a |
| D32 | File write modes, `os.path` helpers, `os.remove`, `os.mkdir`, `os.listdir`, `os.path.getsize` match | `os.path.join/basename/dirname/abspath/splitext`, `os.path.exists`, `os.remove`, `os.listdir`, `os.path.getsize`, `open(p, "w")` then read back, `open(p, "a")` append, `open(p, "rb")` binary read, `open(p, "w")` with `f.write`, `for line in f` all match. `open(p, "w", encoding=...)` fences with `PS2003: unexpected keyword argument encoding`. | Clean on the tested shapes. | S3 | n/a |
| D33 | `str` methods and format specs match on the tested shapes | `upper/lower/title/capitalize/swapcase/startswith/endswith/find/rfind/count/replace/center/ljust/rjust/expandtabs/zfill/index/isalpha/isdigit/isspace/isupper/islower/removeprefix/removesuffix/partition/casefold`, slicing with steps, negative slices, `f"{x:5d}"`, `f"{x:05d}"`, `f"{x:+d}"`, `f"{x:,}"`, `f"{x:#x}"`, `f"{x:b}"`, `f"{y:.2f}"`, `f"{y:e}"`, `f"{y:g}"`, `f"{y:.1%}"`, `%d`, `%x`, `%e`, `%g`, `%s`, `%r`, `"{}{}".format`, `str.join`, `str.split`, `str.splitlines` all match. `str.lstrip`/`str.rstrip` with an argument fence. | Clean on the tested shapes. | S3 | n/a |
| D34 | `int` and `float` repr, hex/oct/bin, comparison and `//`/`%` signs match | `repr` of `0.1`, `1.0`, `-0.0`, `1e16`, `1e15`, `5e-324`, `1.7976931348623157e308`; `hex(-255)`, `oct(-8)`, `bin(-5)`; `-7 // 2`, `7 // -2`, `-7 % 2`, `7 % -2`, `-7 % -2`; `divmod(-7, 2)`; `7.5 // 2.0`, `-7.5 // 2.0`; `round(0.5)`, `round(1.5)`, `round(2.5)`, `round(-0.5)` (all banker's rounding); `1.0 == 1`; `float("inf")`, `float("nan")`; `0.0 == -0.0` all match. | Clean on the tested shapes. | S3 | n/a |
| D35 | `str`, `bytes`, `int`, `float` comparison across types fences | `1 == "1"` fences with `PS2004: comparisons need two values of the same scalar type`; `[] == ()` fences; `True + True` fences with `PS2004: bool arithmetic is not lowered`. | **Fenced.** Honest. `refused-features.md` T3 lists bool arithmetic. | S3 | n/a |
| D36 | Generators lower only in the `Iterator[T]` / `Iterable[T]` / `Generator[T, None, None]` lanes | A generator annotated `Generator[int, int, None]` (with `send`) fences; `g.send(5)` on a just-started generator fences; `g.close()` fences; `g.throw(...)` fences; `return <value>` in a generator fences with `PS2002: return with a value in a generator is not lowered yet`; a generator expression fences. The plain `Iterator[int]` generator lowers and its `for` loop matches CPython. | **Fenced and documented** (items 19, 20, 21, 25 cover `send`, `throw`, `close`, `yield from`). The `Generator[T, S, R]` with send/return types is fenced but not in the numbered list. | S3 | M |
| D37 | `asyncio.gather` lowers but its result is not a `list` | `r = await asyncio.gather(one(), two())` fails the checker with "`tuple[int, int]` is not assignable to `list[int]`", so the annotation must be wrong, but then `r` is "not a local or parameter". `asyncio.create_task` lowers and matches; `asyncio.sleep(0)` lowers and matches. `asyncio.gather` with the result discarded lowers and matches. | **Fenced at the type level.** The ordering of interleaved `gather` tasks could not be compared because the result type fences. | S3 | M |
| D38 | `subprocess`, `socket`, `threading` calls run in an embedded CPython | `subprocess.check_output(["printf", "island-out"])` lowers and matches CPython byte for byte, including the ordering against native `print` output. `threading.Lock` as a `with` target fences; `threading.Thread` with `args=` fences with `PS2002: threading.Thread args is a tuple`. | **Documented** (item 15: island calls follow the interpreter the binary links, not the pin). | S3 | n/a |
| D39 | The compiler crashes on a bare `raise` inside an `except` handler | `try: raise ValueError("x") / except ValueError: raise` compiles and runs, but the nested form `try: try: raise ValueError("x") / except ValueError as e: raise / except ValueError as e: ...` crashes the compiler with `KeyError: '.ps_exc_1'` at `src/pyscriptc/emit/llvm/_stmts.py:180`. | **Silent** (a compiler crash, not a program divergence). Not documented. | S1 | S |
| D40 | `round(x, 9)` and `round(x, -2)` are runtime errors, not compile-time fences | Both raise `ValueError` at run time with the message `round ndigits is out of the lowered range`, which is a pyscriptc-specific string that never appears in CPython. A CPython program that uses `round(x, 9)` works and the compiled one fails. | **Silent.** | S2 | S |
| D41 | `idempotent` module-scope `dict` reads that return a container behave differently from the same read inside a function | Covered by D2; listed separately because the fix surface differs (the global slot holds a pointer, so every read must return the same pointer). | **Silent.** | S1 | L |

### The S1 list, ranked by blast radius

1. **D2** (module-scope containers have value semantics). Any program that keeps a list or dict in a module-level variable and mutates it, aliases it, or passes it to a function that mutates it, gets wrong bytes with no diagnostic. This is the shape nearly every non-trivial Python program has.
2. **D1** (`list.sort()` is a no-op on a module-scope list). A sort that silently does nothing is the worst failure shape there is: the program runs, exits 0, and prints wrong numbers.
3. **D3** (`sum()` over floats is a naive fold). Every float aggregation is off in the last bits, which then propagates into every comparison downstream.
4. **D19**, **D20**, **D21**, **D22** (dict and list mutators that lower but do not mutate). Each is a silent wrong answer, each is a small fix, and each is currently accepted by the compiler where the sibling `append` is fenced.
5. **D8**, **D9**, **D10**, **D11** (output bytes). Each is a byte-level stdout difference with no diagnostic.

**D1, D19, D20, D21 and D22 are all the same defect as D2.** A module-scope `list` or `dict` is stored as a compile-time constant and re-emitted on every read, so a mutating call operates on a throwaway copy. One fix (a module-scope container takes a real global slot, the way the threading records already do) closes all six. The remaining S1 findings (D3, D8, D9, D10, D11) have separate causes.

The fix has a shape the codebase already knows: `_bind_threading_global` (`src/pyscriptc/lower/__init__.py:1135`) exists precisely because "a stored constant would re-emit the constructor at every read, so the record's identity would not hold". A `list` and a `dict` have the same requirement. The runtime already has the slot store (`ps_global_set_ptr` / `ps_global_get_ptr`, `src/pyscriptc/runtime/ps_osmore.c:649`) and the pointer-class path already round-trips a pointer through it (`PTR_CLASSES`, `src/pyscriptc/stdmore.py:1327`). The work is to extend the pointer path to `list` and `dict`, and to make the emitted code hold one pointer per module-scope container for the life of the process.

## Awareness assessment

### What the repo knows

`docs/reference/divergences.md:6` states the contract plainly: "The empty divergence space is load-bearing: anything not listed here is claimed identical, and a difference you find that is not listed is a bug." There are 33 numbered divergences, the compiler has a fence taxonomy with `PS2001` to `PS4203`, `refused-features.md` records the tier of each refusal, and the fence census ranks the remaining gaps by set cover. The awareness for **fences** is genuinely good: the refusal message names the construct, the tier is recorded, and the supported table has a row per builtin.

The awareness for **semantic divergences inside a construct that lowers** is much thinner, and that is where every S1 finding above lives.

### The structural gap

The contract has three clauses and only one of them is enforced.

- "Anything that does not lower is a named `PSxxxx` fence, not a divergence." **Enforced.** The fence census and the supported table cover this.
- "stdout: byte-exact for every lowered call." **Not enforced anywhere.** The corpus scorer compares whitespace-stripped stdout (`benchmarks/compat2/score.py:196` uses `ran.stdout.strip() == oracle[1]`), and the doc itself admits the gap: "The corpus scorer today compares whitespace-stripped stdout; tightening that to unstripped bytes is planned work" (`docs/reference/divergences.md:10`). Whitespace stripping hides D9, D10, D11 and D8 from the scorer even if a probe exercised them.
- "anything not listed here is claimed identical." **Not enforced, and not mechanically checkable.** There is no mechanism that turns a behavioural difference into a divergence entry. The only artifact that resembles one is the single `wrong-output` row in the frozen `CALIBRATION.json`, and it has no home in the numbered list: `lang.cpython.float.RoundTestCase.test_previous_round_bugs` is exactly D4, and `TRIAGE.md` does not mention it.

Two amplifiers make this worse.

**The probe corpus does not reach the failing shapes.** The `builtins.sum` probe is `sum([True, False])`, so the float path is never exercised. The `builtins.list.sort` probe calibrates as `fence` (the probe shape is `o.sort()` on a local list inside a `def probe()`, which the compiler refuses with `PS2001`), so the module-scope no-op is never observed. The `builtins.round` probe is `round(p0)` with an `int` argument, so neither the negative nor the large `ndigits` path is exercised. Every S1 finding above sits in a shape the denominator does not contain.

**The score is a weighted average, so a wrong answer and a refusal look alike.** `benchmarks/compat2/score.py` gives `wrong-output` and `fence` different weights, and the verdict ratchet (`benchmarks/compat2/verdict.py:45`) only fires on a **new** soundness failure: a row that was already `wrong-output` stays `wrong-output` forever with no new finding. So the one known `wrong-output` row has been sitting in the frozen calibration, and the verdict cannot flag it again. There is no lane whose job is "find a divergence that is not yet listed".

### What this means

The str Latin-1 bug was found by a human looking at output. It was not found by a probe, not by the scorer, not by the census, and not by the soundness ratchet. That is the honest characterisation of the current state: the repo has a strong **refusal** discipline and a weak **divergence** discipline. The `wrong-output` outcome exists in the contract (`benchmarks/compat2/outcomes.py:11`) but has no producer that hunts for it.

The narrow conclusion: the contract says anything not listed is identical, but nothing enforces it. The mechanism that would enforce it is a **per-divergence probe**, because that is what turns "we claim this is identical" into an executable assertion. Today the numbered divergences have prose and a "Source:" pointer to a doc or a header comment, but no test that fails if the behaviour changes. A divergence that is not pinned by a probe will drift back toward "identical" the next time the runtime changes, and it will drift silently.

## Proposed mechanisms

Six mechanisms, in the order they should land. Each is filed as its own issue.

### M1. A divergence ledger with one probe per entry

A machine-readable ledger, `docs/reference/divergence-ledger.json`, holding one row per numbered divergence and one row per **known** divergence found by this audit that is not yet in the numbered list. Each row carries: the divergence id, the seam, the CPython behaviour, the pyscriptc behaviour, the probe source, the expected CPython bytes, and the current pyscriptc bytes. A test compiles and runs every row's probe against the pinned CPython and asserts the recorded byte difference. The ledger is what turns the numbered list from prose into an executable contract.

Why a probe per entry and not a shared test: a probe is the only thing that survives a runtime rewrite. The str model fix (the epic) rewrites 9.3k lines of bytewise runtime. Every byte-level divergence we hold today will move during that rewrite. Without a ledger with recorded bytes, the rewrite has no way to tell "I fixed this" from "I moved this". The ledger is the safety net for the str epic as much as it is for today's bugs.

The ledger must live outside the frozen gate set (`benchmarks/compat2/probes/*.jsonl` and friends stay untouched). It is a new artifact under `docs/reference/`, regenerated by a script, and gated by a test that fails when a row's recorded bytes no longer match the compiler's current output. That failure is the signal: either the divergence was fixed (update the row, or delete it and renumber) or it moved (update the bytes and the prose). Either way it is a reviewed change.

### M2. A differential probe lane in the nightly

A lane that compiles a corpus of small programs with the current compiler, runs each against the pinned CPython, and byte-compares stdout, stderr and the exit code. Any difference is a candidate divergence. The lane is the "producer" that M1 consumes.

The lane has two inputs. The first is a set of **semantic probes**: small programs, one construct each, that exercise the seams this audit found (numeric edge values, container mutation at module scope, output bytes, exception types, file modes). The second is the **visible corpus** (`benchmarks/compat2/corpus/src`), which already has hundreds of real programs; run them through the same byte comparison instead of the whitespace-stripped one. That is the change `docs/reference/divergences.md:10` calls "planned work", and it is the single highest-value mechanism here: it turns the existing corpus from a fence census into a divergence detector.

The lane runs nightly, not in CI, because it is expected to be red until the backlog is worked. It reports; it does not block. The blocking version lands once the backlog is empty, as a fourth mechanism (see the ratchet below).

The lane must compare **untrimmed** bytes. That single change is what makes D8, D9, D10 and D11 visible; the current stripped comparison cannot see them at all.

### M3. A rule that no new feature lands without a differential probe

A contribution rule, stated in `AGENTS.md` and in the PR template: any PR that adds a lowering, a runtime helper or a stdlib pack must add at least one differential probe that runs the new construct against the pinned CPython and byte-compares the result. A PR that lowers a construct for the first time without a probe does not merge.

This is the rule that would have caught D1, D3, D19, D20, D21 and D22 at the moment they landed. Each of those is a construct that lowered, was never probed, and was wrong. The rule makes the probe part of the definition of done rather than an optional extra.

The rule needs one escape hatch, and it must be narrow: a PR that lowers a construct may ship with the probe marked as expected-to-differ, provided the difference is entered in the divergence ledger (M1) in the same PR. That is the honest path for a construct that is known to diverge, and it means "we know this differs" is a recorded fact rather than a gap.

### M4. A diagnostic for silent-divergence classes

A compile-time diagnostic for the classes of bug where the compiler lowers a construct that cannot be correct. The classes this audit found:

- A **mutating call on a module-scope container** (`sort`, `reverse`, `insert`, `extend`, `remove`, `setdefault`, `update`). Today these either no-op or corrupt. The diagnostic names the construct and the reason.
- A **module-scope container alias** (`ys = xs` where both are module scope). The compiler knows the target is a global slot; a copy-on-alias is not representable in the current IR, so this should fence, not silently copy.
- A **float aggregate** (`sum` over a `list[float]`). A naive fold cannot match CPython's compensated sum; the diagnostic says so and names `math.fsum` as the substitute.
- A **runtime-bounded `round(x, n)`** (`n < 0` or `n > 8`). The bound is a runtime error today; it should be a compile-time fence, because the answer is knowable at compile time.

The general shape: the diagnostic fires when the compiler can prove the lowered form cannot match CPython, and it names the CPython behaviour it would have to match. This converts the "silent" in "silent divergence" into a fence, which the repo already treats as honest.

### M5. A ratchet that turns a known divergence into a ticket

The verdict already has a soundness ratchet (`benchmarks/compat2/verdict.py:45`) that fires on a **new** `wrong-output` or `crash`. It does not fire on a row that was already `wrong-output`, which is why the `round` divergence has sat in the frozen calibration unnoticed. The ratchet needs a second arm: a row that stays `wrong-output` past a fixed age becomes a finding with an issue link, so a known divergence is a tracked item rather than a frozen constant.

This also needs the inverse: when a row goes from `wrong-output` to `pass`, the finding closes, because the divergence was fixed. Today a fix is invisible, which removes the incentive to fix anything.

### M6. Tighten the corpus comparison to unstripped bytes

Make the whitespace-stripped comparison in `benchmarks/compat2/score.py:196` a second, stricter lane rather than a replacement, so the existing corpus score keeps its meaning while the byte comparison runs alongside it. The stripped lane is what the score is built on and its weight structure is frozen; the strict lane is new and additive.

The corpus already contains real programs that use `.sort()` (`benchmarks/compat2/corpus/src/wheel/wheelfile.py:174`, `dateutil/rrule.py:697` and others). Those are the programs that would have caught D1 if the comparison were byte-exact.

### Coverage map note

`benchmarks/compat2/coverage_map.py` maps probe ids to the compiler files that would have to change to make a probe pass. That is the right shape for a divergence ledger too: each ledger row should record the compiler files that implement the divergent behaviour, so a change to those files re-runs the row's probe. This is the "IR and corpus move together" idea applied to divergences rather than to hashes.

## CPython version alignment

### What we claim

| Place | Claimed | Source |
| --- | --- | --- |
| Parity contract | `CPython 3.14.x`, "Parsing, probes and the compatibility oracle follow 3.14" | `docs/reference/divergences.md:6` |
| Release process | `CPython 3.14.x`, "byte-for-byte on stdout" | `.agents/docs/release/release-process.md:79` |
| Host floor | `requires-python = ">=3.14"`, classifier `3.14` | `pyproject.toml:6`, `pyproject.toml:16` |
| Dev interpreter | `3.14` | `.python-version:1`, `mise.toml:48` |
| Agent note | `Target: CPython 3.14.x (parser, probes, frozen oracle, behavior)` | `AGENTS.md:5` |
| Supported versions doc | `CPython 3.14 is the primary reference`; the host "is also the oracle the semantics are checked against" | `docs/project/versions.md:6`, `docs/project/versions.md:10` |
| Scorer oracle pin | `ORACLE_PIN = (3, 14)` | `benchmarks/compat2/score.py:347` |
| CI | `python-version: "3.14"` in every job, plus `assert sys.version_info[:2] == (3, 14)` | `.github/workflows/ci.yml:55` and 12 more, `:75` |
| IR stamp | `sys.version.split()[0]`, i.e. the running interpreter's full version, with `assert_same_cpython` refusing a mismatch | `src/pyscriptc/ir.py:55`, `src/pyscriptc/ir.py:453` |
| IR hash baseline | `"oracle": "2/cpython-3.14.9"` | `benchmarks/compat2/probes/IR_HASHES.json:4` |
| Supported table | "Targets CPython 3.14." | `scripts/support_data.toml:22`, `.agents/docs/support/supported.md:17` |
| Island link name | `-lpython3.14` | `.agents/docs/invariants.md:29` |

### What we actually run

| Surface | Actual | Evidence |
| --- | --- | --- |
| `uv run python` in the project | **CPython 3.14.9** | `uv run python -c "import sys; print(sys.version)"` |
| `python3` on `PATH` | **CPython 3.12.15** (mise global) | `python3 --version`; `~/.local/share/mise/installs/python/3.12/bin/python3` |
| Scorer oracle, resolved live | `(.venv/bin/python, "2/cpython-3.14.9")` | `benchmarks.compat2.score.resolve_oracle(None)` |
| Cache key | `2/cpython-3.14.9` | `benchmarks/compat2/cache.py:33` |
| Docker oracle axis | 3.10.22, 3.11.17, 3.12.15, 3.13.16, **3.14.8** | `docker/Dockerfile:8` |
| Local oracle axis (uv) | 3.10.22, 3.11.17, 3.12.15, 3.13.16, **3.14.9** | `uv python list --only-installed` |
| **Probe generation oracle** | **CPython 3.12.15** | `benchmarks/compat2/probes/MANIFEST.json:34` (`"cpython": "3.12.15"`, `"cpython_pin": "3.12"`) |
| Probe set doc | "The primary oracle is CPython 3.12" | `benchmarks/compat2/probes/README.md:11` |
| Secondary probe set | CPython 3.14.8 | `benchmarks/compat2/oracle-3.14/MANIFEST.json:29` |
| Calibration oracle | CPython 3.14.8 | `benchmarks/compat2/probes/CALIBRATION.json:90` |
| Vendored CPython tests | `v3.12.15` (primary), `v3.14.8` (secondary) | `probes/cpython_tests/PIN.json:16`, `probes/cpython_tests/py314/PIN.json:16` |
| Corpus expected stdout | captured on **CPython 3.14.8** | 20 × `benchmarks/compat2/corpus/scripts/*/meta.json` |
| Typeshed source | mypy 2.4.0, `VERSIONS` covering up to 3.15 | `MANIFEST.json:231`, `src/pyscriptc/_typeshed/stdlib/VERSIONS` |
| pyrefly | 1.4.0 | `pyproject.toml:19` |
| Perf baseline (Linux) | CPython **3.15.0** | `benchmarks/baseline/reference.json:4` |
| Perf baseline (macOS arm64) | CPython **3.14.7** | `benchmarks/baseline/reference-macos-arm64.json:4` |

### Every place that disagrees

| # | Place | Claimed | Actual | Kind |
| --- | --- | --- | --- | --- |
| V1 | `docs/reference/divergences.md:6` and `.agents/docs/release/release-process.md:79` | "probes and the compatibility oracle follow 3.14" | the committed probe set was generated on **3.12.15** | **stale doc, and it contradicts a frozen gate file** |
| V2 | `AGENTS.md:5` | "Target: CPython 3.14.x (parser, **probes**, frozen oracle, behavior)" | probes oracle is 3.12.15 | **stale doc** |
| V3 | `AGENTS.md:9` | "Sweep evidence for the 3.12 corpus" | the corpus sweep evidence refers to the 3.12 probe oracle; the corpus *scripts* were captured on 3.14.8 | **ambiguous, two different 3.12s** |
| V4 | `docs/project/versions.md:10` | the host "is also the oracle the semantics are checked against" | the compat2 scorer oracle is 3.14.9 and the probe oracle is 3.12.15; the host is not the probe oracle | **stale doc** |
| V5 | `.agents/docs/loop.md:23` | "Weighted pass under the CPython 3.12 oracle. The 3.12 oracle is the target." | the scorer is pinned to 3.14 (`score.py:347`) and CI asserts 3.14 | **stale doc, the inverse of V1** |
| V6 | `.agents/docs/release/python-version-window.md:15` | 3.12 is the "**primary oracle**" | 3.14 is the scorer and CI oracle | **stale doc** |
| V7 | `.agents/docs/release/python-version-window.md:29` | `requires-python = ">=3.10"`, five classifiers (3.10 to 3.14) | `">=3.14"`, one classifier | **stale doc** |
| V8 | `.agents/docs/release/python-version-window.md:103`, `:119` | (recommends) `">=3.12"` | `">=3.14"` | **stale doc** |
| V9 | `packaging/smoke.sh:81` and `packaging/README.md:78`, `:82` | the release smoke compares against **CPython 3.12** (`uv run --no-project --python 3.12`) | the parity contract claims 3.14 | **wrong reference in a release gate** |
| V10 | `docker/Dockerfile:8` | axis 3.14 = **3.14.8** | the local uv project and axis resolve to **3.14.9** | **pin split, container against host** |
| V11 | `benchmarks/compat2/probes/MANIFEST.json:91` and `oracle-3.14/MANIFEST.json:86` | axis 3.14 = 3.14.8 | local resolution = 3.14.9 | **stale frozen gate file** |
| V12 | `benchmarks/compat2/probes/IR_HASHES.json:4` | `2/cpython-3.14.9` | the Docker oracle is 3.14.8 | **stale for the container** |
| V13 | `benchmarks/compat2/probes/CALIBRATION.json:90` and `probes/TRIAGE.md:11` | calibration oracle **3.14.8** | the probe set is 3.12.15 | **known mismatch inside the frozen gate set**; `SWEEP-triage-verdict.md:13` records that it was noticed and left |
| V14 | `benchmarks/compat2/probes/README.md:11` against `benchmarks/compat2/README.md:9` | "primary oracle is CPython 3.12" against "the CPython oracle is pinned to 3.14" | both true, of different tools | **terminology collision, undocumented** |
| V15 | `benchmarks/compat2/probes/calibrate.py:10`, `:36` | `--oracle` is a documented flag | only the running interpreter is accepted; anything else exits non-zero with `MISSING_SWITCH` | **stale docstring, missing capability** |
| V16 | `.agents/docs/spec/external-oracles.md:34` | `pyright --pythonversion 3.13` | the target is 3.14 | **stale doc** |
| V17 | `benchmarks/baseline/reference.json:4` | perf baseline on **3.15.0** | the target is 3.14 | **wrong baseline host** |
| V18 | `benchmarks/baseline/reference-macos-arm64.json:4` | perf baseline on **3.14.7** | current micro is 3.14.9 | **stale baseline host** |
| V19 | `benchmarks/compat2/version_surface.py:39` and `tests/test_compat2_probes.py` (around line 110) | the axis `3.10` to `3.14` | also defined in `benchmarks/compat2/probes/generate.py:36` | **axis duplicated in three places, no single source of truth** |
| V20 | `benchmarks/compat2/probes/versions.py:3` and `probes/engine.py:225`, `probes/fold.py:53` | "parseable by 3.10" against "parseable before 3.14" | two different floors in the same subsystem | **inconsistent wording** |
| V21 | `tests/compat2/test_target_version.py:30`, `:178` | the oracle pin test asserts the prefix `cpython-3.14.` | no micro is asserted anywhere in CI | **under-specified gate** |
| V22 | `.github/workflows/nightly.yml:72` to `:75` | (no oracle assertion) | `ci.yml:75` and `ir-hashes.yml:42` assert `(3, 14)` | **gap: the nightly score runs without an oracle-minor gate** |
| V23 | `tests/fixtures/flipcheck/achieved-baseline.json:4` | `"oracle": "cpython-3.14.7"` | the real format is `2/cpython-<x.y.z>` (`cache.py:34`) | **stale test fixture** |
| V24 | `benchmarks/compat2/corpus/pins.json` and `corpus/pin.py` | (no CPython version at all) | the corpus expected stdout was captured on 3.14.8 | **blind spot** |

Two of these are load-bearing rather than cosmetic.

**V1 and V2 are the same defect as the str bug.** The parity contract says the probes follow 3.14. They follow 3.12. The visible probe denominator, its `versions` axis and its `MANIFEST.json` were all produced under 3.12.15. Every claim the repo makes about 3.14 semantics rests on a 3.12 oracle. The probe set even calls 3.12 "the primary oracle" in its own README. Meanwhile the scorer, the CI and the IR stamp all say 3.14. The repo has two oracles and one claim.

This matters concretely. The `versions` field on every probe row records the subset of 3.10 to 3.14 where the probe returns the same result as under the oracle. Because the oracle was 3.12, a probe whose behaviour changed in 3.13 or 3.14 is recorded as valid on 3.14 only if it happened to agree with 3.12. The 3.14-specific changes are exactly where the interesting semantics live. CPython 3.14 is the release that unified the `ZeroDivisionError` message, and `src/pyscriptc/ir.py:59` already carries a variant switch for it, chosen by the running interpreter rather than by a recorded target. A probe set generated on 3.12 cannot validate that switch.

**V9 is a release gate comparing against the wrong interpreter.** `packaging/smoke.sh` compares the compiled binary's stdout with CPython **3.12**. Under the parity contract that comparison should be against 3.14. It passes today because the smoke program is `hello`-shaped, but it would not catch a 3.14-only divergence, and it silently establishes 3.12 as the reference for the release artifact.

### Blind spots: where the version is not recorded at all

1. `benchmarks/compat2/corpus/pins.json` and `corpus/pin.py` record the fetch date and the sha256 of each package, but **no CPython version**, even though the corpus's expected stdout was captured on 3.14.8.
2. `benchmarks/compat2/corpus/README.md` never names a CPython version.
3. `benchmarks/compat2/score.py:347` pins the **minor** only. Any `cpython-3.14.*` under `PYSC_ORACLE_DIR` is accepted, so a 3.14.8 to 3.14.9 swap silently invalidates every cache entry and changes the recorded stamp, with no file declaring which micro is sanctioned.
4. CI asserts the minor only (`ci.yml:75`, `ir-hashes.yml:42`). Nothing records or checks the micro.
5. `benchmarks/compat2/probes/typeshed.py:35` falls back to `sys.version_info[:2]` when `PYSC_ORACLE` is unset, so the typeshed gate follows whatever interpreter runs.
6. `benchmarks/compat2/verdict.py` and `benchmarks/compat2/headroom.py` contain no oracle reference at all; they consume score output without re-checking the oracle identity.
7. `benchmarks/scripts_corpus/oracle/` records the expected stdout with **no CPython version**; the runner is `sys.executable`, so re-recording on a different interpreter silently overwrites the oracle.
8. `src/pyscriptc/ir.py:55` derives the IR stamp from the running interpreter. It is discovered, not declared: no file in the repo says which CPython micro is the sanctioned target.

## Version-target policy proposal

### The principle

The target is a **declared, pinned** CPython version, not "whatever interpreter runs the compiler". Everything that claims parity names the same version, and the version is recorded in a file, not derived from the environment.

Today the target is CPython 3.14.x, minor-pinned. The micro is unpinned, which is the right level for now: within a minor release, CPython's documented compatibility promise is that bugfix releases do not change documented behaviour. The bugs are in the micro only when a security fix changes behaviour, which is rare and worth noticing rather than pinning against.

### When to move the minor

A new minor target is adopted **only** when all of the following hold.

1. The new minor has been released for **at least one full patch release** (that is, `N.x.1` exists). This avoids adopting a version whose first patch release is still finding its own regressions.
2. The move is **tied to a pyscriptc minor release**, not to a calendar date. A pyscriptc minor is the unit that can carry a re-recorded oracle, a re-generated corpus and a re-run divergence pass. Version moves without a release vehicle stall.
3. The new minor is still **upstream-supported** (security support, not bugfix-only).
4. The owner decides. The default is "do not move".

### What the move costs, and the gates it must pass

Moving from 3.14 to 3.15 is not a version-number edit. It touches, in order:

1. **The oracle re-record.** The probe set is regenerated against the new oracle under the existing A/B byte-identical discipline (`python -m benchmarks.compat2.irscore` twice, `cmp`). This is a frozen-gate change and needs a recorded decision per `AGENTS.md`.
2. **A full divergence re-run.** Every row of the divergence ledger (M1) is re-run against the new oracle. Every row that changes its bytes is either a fixed divergence (delete the row, renumber the list) or a new divergence (add the row, write the prose). The re-run is the gate: a minor move is not allowed to land with unexplained byte changes.
3. **The corpus re-capture.** The 20 corpus scripts' expected stdout is re-captured on the new oracle, and the corpus pins file gains the CPython version it was captured with (closing blind spot 1).
4. **The IR stamp moves with it.** `IR_HASHES.json` is regenerated against the new oracle, and the header oracle stamp moves from `2/cpython-3.14.9` to the new value in the same PR. The stamp and the corpus move together, in one commit, or the move is incomplete.
5. **The IR stamp becomes a declared target, not a derived one.** `cpython_version()` should compare the running interpreter against a **declared** target version in a file (say `pyscriptc/target.json`), and `assert_same_cpython` should refuse a mismatch against the declared target with a message naming it. Today it compares the IR against the running interpreter, so an IR stamped on 3.14.9 emits fine on 3.14.8 as long as the stamp matches the running process. The stamp should also record the *target* separately from the *host*, so a host on 3.14.9 lowering against a 3.14.x target is distinguishable from one lowering against a 3.15 target.

### What stays supported

- The **host floor** (`requires-python`) stays at `>=3.14` and moves only when the compiler itself needs a newer syntax or a newer stdlib API. It is a packaging question and does not move with the target.
- The **emitted binaries** carry no Python version dependency at all. The IR stamp is a consistency check, not a runtime requirement. A move changes the stamp and nothing about the artifacts.
- The **version axis** (`--target-version`, the scorer's per-version columns) keeps its existing shape: the axis is the set of versions where a probe is valid, and the axis grows as the target moves. The axis floor does not have to move with the target.
- The **old target** stays supported as an axis entry for at least one pyscriptc minor after the move, so a user on the previous target is not cut off.

### Micro-level hygiene, which is cheap and fixes V10, V12, V21 and V22 now

These do not need a minor move. They need a decision to record the micro.

1. **Declare the sanctioned micro.** One file names the CPython version the repo tests against, for example `3.14.9`. The Docker oracle installs that exact version, the local uv environment resolves to it, and CI asserts it (minor **and** micro), not the minor alone.
2. **Make the oracle pin micro-aware.** `score.py`'s `ORACLE_PIN` becomes a full version tuple, and `pinned_oracle()` accepts only that version. A different micro is a hard error naming the expected one, not a silent cache invalidation.
3. **Assert the micro in the nightly.** The nightly score job currently has no oracle assertion at all; it should assert the same version the CI asserts.
4. **Record the oracle in the corpus pins.** `corpus/pins.json` gains the CPython version the expected stdout was captured with, and `corpus/README.md` names it.
5. **Record the oracle in the scripts-corpus oracle files.** The recorded stdout files gain the version, so a re-record on a different interpreter is visible.
6. **One axis definition.** `generate.py:36`, `version_surface.py:39` and `tests/test_compat2_probes.py` all define the axis. Move it to one module and import it from the other two.
7. **Fix the release smoke reference.** `packaging/smoke.sh` compares against the declared target, not 3.12.

Items 1 to 5 and 7 close V9, V10, V12, V21, V22 and V24 in one PR that touches no frozen gate file. Item 6 touches `probes/` but not a frozen file.

### The probe-oracle question, which is the real one

The probe set's 3.12 oracle versus the 3.14 claim is not a stale string; it is a structural decision that was never written down. There are two coherent end states.

**Option A: the target is 3.14 and the probes follow.** The probe set is regenerated against 3.14, `MANIFEST.json` records 3.14, `probes/README.md` calls 3.14 the primary oracle, and the 3.12 set becomes a secondary axis entry. The `versions` field then means "the subset of the axis where this probe agrees with the **target**", which is the field's obvious intent and the field the scorer reads. The cost is a full probe regeneration and a frozen-gate decision.

**Option B: the target is 3.14 and the probes stay on 3.12 as a *denominator*, with a separate 3.14 conformance set.** This is what the repo has today, and it is defensible: the probe set is a large frozen denominator built to measure breadth, and regenerating it on every target move is expensive. But then the claim "probes follow 3.14" is false and must be removed from `divergences.md`, `AGENTS.md` and `release-process.md`, and the 3.12 oracle must be named as what it is.

Option A is better, because the `versions` field is only meaningful against the oracle the scorer compares to, and because the repo's own contract claims 3.14. But Option B is legitimate and cheaper, and the only thing that is not legitimate is having both claims at once, which is the current state. **The recommendation is Option A, gated on the same oracle re-record and divergence re-run as a minor move, so it can ride with the 3.15 move rather than being done twice.** Until then, the docs must stop claiming 3.14 for the probes, and the str epic's oracle re-record should be the vehicle.

## What this plan does not cover

- The str code-point model and the Latin-1 byte model. Owned by the separate session. Findings D9, D11 and D12 are noted here because they are byte-level stdout divergences, but their fix belongs there. D11's doc wording is the one piece that can be fixed here: the divergence entry should say what actually happens (raw latin-1 bytes emitted), not what the reader assumes (non-ASCII rejected).
- Compiler changes. This plan proposes mechanisms; it implements none of them.
- The frozen gate files. `benchmarks/compat2/outcomes.py`, `score.py` weights, `coverage_map.py`, `cache.py`, `calibrate.py`, `CALIBRATION.json`, `TRIAGE.md`, `probes/*.jsonl`, `probes/IR_HASHES.json` and the canonical rules are untouched here. The probe-oracle move (Option A) is the one proposal that would touch them, and it needs a recorded decision first.
- The scorer evaluation set. Never read.

## Filed issues

Each proposed mechanism and each high-severity finding is filed as its own issue, on project 6 with a `Horizon` and an `Effort`, on the `V2 compiler` track, with the milestone set by judgment (v0.8.0 for the mechanisms, v0.7.0 for the findings that are small enough to land in the current cycle).
