# Real-Python gap map and backlog

Date: 2026-10-17. Base: master `9f64427` (post-E1/E2-M1, `sys.argv` + `PsExc`
isa-matching landed). Method: ~480 one-construct probe scripts were compiled
and run on the `realpy-gap` worktree with
`uv run python -m pyscriptc compile x.py -o /tmp/x --run`, both at module scope
and inside `def main() -> int:`. Every verdict below is empirical; where a
verdict needed source confirmation it cites the fence table.

This plan covers the *language/construct* gap between today's subset and
everyday script Python. `docs/wave-c-backlog.md` already ranks the stdlib
*family* surface; the two overlap only where a family is gated on a construct
(noted per task).

## Verdict table

Exit codes observed: `0` works; `3` lowering fence (PS code); `2` checker
(mypy) rejection before lowering; `1` SpikeError/uncaught raise/assert;
`132` runtime trap (SIGILL).

### Works today (verified)

| Area | Constructs |
| --- | --- |
| Script shape | bare `print(...)` statements at module level, constant assignments, `if __name__ == '__main__'`, module `if` on `sys.platform`/`sys.version_info` tuple compares, module-level `for i in range(...)` |
| Functions | all-annotated signatures, constant defaults, kwargs at call site, `*args: int` (len/list() of it), multiple/forward/mutual-recursive calls |
| Control flow | `if/elif/else`, `while` (incl. `while True`+break, str/list/dict/int truthiness), `for` over `range` (all arg forms), `list[scalar]`, `set`, `dict.keys()`, `enumerate`/`zip`/`itertools.repeat(x,n)`/`itertools.chain`/`sys.argv` **with a single-name target**, `break`/`continue`, ternary and `and/or` values **inside functions** |
| Match | `match x: case <literal>:` chains (int/str literals) |
| Exceptions | try/except with the 7 builtin names, user `class E(Exception)`, tuple of names, multiple handlers, `raise Name("msg")`; real `KeyError`/`IndexError` are caught; unhandled raise exits 1 with message; `assert`/`assert msg` aborts |
| Classes | annotated fields, `__init__`, methods, single inheritance, `@property`/`@classmethod`/`@staticmethod` in class bodies, `getattr(p,'x')`, `@dataclass` (defaults), `enum.Enum`/`IntEnum` int members, `Path`-style classes (`p: Path`) |
| str | `+`, `[i]` (incl. negative/var), all slices incl. steps, `len`, `in`, `==`/ordering, f-strings (no specs, calls/methods inside OK), `%s`/`%d` with literal format, `strip/lstrip/rstrip/upper/lower/startswith/endswith/find/replace/split(0..1 arg)/join`, `encode()/decode()` |
| list | literals, index/item-assign/slices (all steps), `append`, `+` concat, `in`, listcomp (1 for + filter), `list(list/set)` |
| dict/set | literals, `[k]`, assign, `in`, `.keys()` (+ `for`/`list()` over it), nested `dict[str,dict]`/`dict[str,list]`; set consts + `.add` + membership + iterate |
| tuple | `tuple[T,...]` fixed-arity synthetic classes, constant index, `list[tuple]` indexing |
| builtins | `len/abs/min/max(2-arg)/sum/sorted/sorted(key=lambda)/ord/chr/round(+nd)/isinstance/getattr/hasattr/bool/all/any/print` (positional str/int/float/bool), `int(float)/int(bytes)/float(int)/str(int/float/str/Path)` |
| stdlib | `sys.argv` (list[str], index/iterate), `platform/version/executable/maxsize/exit`; `os.getcwd/listdir/getenv(1-arg)/remove/rename/makedirs/stat`; `posixpath` (via `import posixpath`, NOT `os.path`); `pathlib` (name/suffix/stem/parent/as_posix/exists/is_file/is_dir/read/write/joinpath/`==`/`str()`); `open` + `with open` all 6 modes, read/write/close; `contextlib.suppress/nullcontext`; `json.loads`→annotated dict/list/str/nested, `json.dumps` (no kwargs); `re.escape/findall/sub/split/compile`-as-const; `base64`, `hashlib`, `struct`, `hmac`, `math` (no gcd), `datetime`/`time` (inline use or `d: datetime.date` annotation), `collections.deque[int]`/`OrderedDict()`, `functools.reduce(op.add,xs)`, `operator` ops, `io` TextIO/BytesIO (via `from typing import TextIO`), `tempfile`, `shutil.copyfile/rmtree`, `fnmatch`, `warnings.warn`, `stat.S_IS*`, `errno`/`string`/`ssl`/`socket`/`ctypes` constants, `bisect`, `atexit.register`, `unicodedata` (Latin-1), island: `subprocess.check_output`, `logging`, `socket.gethostname`, `threading`, `inspect.cleandoc` |
| Misc | `del` of dead local, `pass`, aug-assign on names `+ - * // **` (int/float/str+=), negative indexing, implicit str concat, raw/triple strings, comments/semicolons, `datetime.date` as annotation |

### Fenced or broken today (verified)

| Area | Constructs and first fence |
| --- | --- |
| Annotations | every local needs `x: T` (PS1001); bare `x: int` w/o value fences ("needs initial value"); `Optional`/union/generic-alias/`object`/`Any`/forward-ref/`'P'` self-type all PS11xx |
| Assignment | `a, b = ...`, `a = b = c`, `*rest = ...` ("only a single name"), `t[i]` non-const tuple index, slice-assign, `del` of attr/subscript/read-later |
| Loops | `for`/`while ...else`; `for` over `str`/`dict`/`d.items()`/`d.values()`/`list[list]`/`list[tuple]`/file lines/enum/`reversed`; **two-name targets** (`for i, x in enumerate`, `for k, v in d.items()`) |
| Exceptions | `finally`, `else`, `except ... as`, bare `except`, `raise ... from`, bare `raise`; only 7 builtin names (`FileNotFoundError` etc. fenced); user exc ctor takes one str (`raise E(5)` → PS1003); **`1 // 0` and int overflow TRAP (SIGILL) instead of raising** — `ZeroDivisionError`/`OverflowError` are uncatchable |
| f-strings | every format spec (`{x:.2f}`, `{x:>10}`, `{x:05d}`, `{x=}`), `!r` conversion |
| str methods (~30) | `count index title capitalize splitlines isdigit isalpha isalnum isspace isupper islower zfill ljust rjust center removeprefix removesuffix partition rpartition rsplit rfind casefold swapcase expandtabs format format_map maketrans translate`, `startswith/endswith(tuple)`, `split(..., maxsplit)`, `encode('utf-8')` |
| dict methods | `get values items pop setdefault update clear`, `del d[k]` |
| list methods | `extend insert pop sort reverse index count remove clear copy`, `+= [..]`, `* n` |
| Operators | `/` on ints, `%` on ints (only the format path exists), `s*n`, `list*n`, `**` float/neg-exp, `& | ^ << >>`, unary `+`, `is` (non-None), mixed int/float compare, `==` on list/dict/tuple/set, `and/or` across types as expr-statement, `True+True` |
| Builtins | `print` kwargs (`sep/end/file/flush`) and non-primitive args (list/dict/tuple/set/bytes/None/Path → and `str()` of those is fenced too); `input reversed map filter iter next divmod hex oct bin pow type repr hash id format globals vars callable eval setattr`, `min/max` of iterable, `min(key=)`, `sorted(reverse=)`, `sum(start)`, `range`/`enumerate` as values, `int(str)`, `float(str)`, `str(bool/list/dict/None)`, `list(str)`, `dict()`, `tuple()`, `bool(None)`, local `frozenset` (frozenset→set annotation rejected by mypy) |
| Function forms | kw-only, pos-only, `**kwargs`, `f(*xs)` starred calls, mutable defaults, **nested `def`** (FunctionDef not lowered → no closures/`nonlocal`), lambdas (unannotated binding), functions as values, user/expr decorators, generators (`yield`) |
| Class forms | multiple inheritance, class keywords, `super()`, class-body constants/defaults, `self.x += 1` (aug-assign on attr), `__str__`/`__repr__`/`__len__`/`__iter__` (methods define but dispatch calls `str(p)`/`len(p)`/`for` not lowered), `__eq__` blocked by mypy override rule, nested class, `del p.x` |
| Comprehensions | dict/set comprehensions, genexpr anywhere, nested `for`s, `enumerate`/`range` as values |
| Imports | `import` inside a function, `from os.path import join` (dotted), `from posixpath import join` (mypy attr), star imports; `os.path.*` unbound (posixpath is the workaround); `random/csv/textwrap/copy/requests/argparse` outside table |
| Stdlib holes | `os.environ`, `os.uname`, `os.getenv` unusable (mypy `str|None` vs annotation rules — see R38), `shutil.which`, `tempfile.NamedTemporaryFile`, `struct.unpack` (mypy `tuple[Any,...]` vs `list[int]`), `sys.stdout/stderr.write`, `re.search/match` results unnamable (no Match type), `re.subn`, `re` flag args, `itertools.{count,cycle,islice,product,zip_longest,accumulate,takewhile,groupby,combinations}`, `functools.{partial,lru_cache,wraps,cmp_to_key}`, `reduce` initializer, `collections.{Counter,defaultdict,namedtuple}`, `math.gcd`, `enum` `.value`/`.name`/iteration/`C(1)`, `importlib.import_module`, `asyncio.*`, `pathlib.iterdir/glob/mkdir/with_name` + `p / 'x'` |
| Statements | `...` literal, `x > 0 and print()` expr-statement, `__name__` as value |
| match | wildcard/capture/or/guard/sequence patterns |
| Module scope | `y = fstr` non-const folds only in documented cases; **BUG**: module-level `a if c else b` and `x or y` assignments crash with `KeyError '.ps_ifexp_1'`/`'.ps_boolop_1'` instead of fencing (SpikeError) |

## Cross-cutting blockers

Five root causes produce most of the fenced column:

1. **No first-class functions/iterators.** Lambdas, closures, `map/filter/iter/next`, genexprs, `d.items()`, `re.Match` and every "returns an iterator" API sit behind one missing concept: a lowered callable/iterator value. Fixes R22–R24/R30 unlock ~40% of the fenced list.
2. **No Optional/union value.** `os.getenv`, `re.search`, `dict.get`, JSON null and `except ... as e` all need a represented `T | None`. That is the tagged-union epic (R26), not a table entry.
3. **Method-call table depth.** `PS2001 call of attribute is not lowered` covers ~50 distinct methods on str/list/dict/set/Path/Deque. Each is cheap (sig row + `ps_rt.c` helper) but there are many — R1–R3 batch the high-frequency ones.
4. **Checker (mypy) fences.** Several constructs are CPython-legal and sometimes even lowerable but die at exit 2: `os.getenv` into any annotation, `struct.unpack` into `list[int]`, `t._0` field access, `__eq__(self, other: P)`, `frozenset(...) -> set[int]`, generator return types, `x = None` on typed locals. Needs annotation-alias decisions, not IR work (R38).
5. **Traps vs exceptions.** Real `KeyError`/`IndexError` route through `PsExc`, but div-by-zero and int overflow `abort()` at the IR level — they can never reach a handler until the trap path raises through `ps_raise` (R10).

## Backlog

Ordered by (frequency in real scripts) × (implementation cost). S = under a
day, M = 1–3 days, L ≈ a week. Families come from `coverage.py`.

| # | Task | Family | Acceptance script compiles+runs | Size | Deps |
| --- | --- | --- | --- | --- | --- |
| R1 | dict core: `get` (1–2 args), `values`, `items` (list-of-tuple form), `pop`, `setdefault`, `update`, `clear`, `del d[k]` | unlowered-call / field-lookup | `d.get('k',0); d.pop('k'); d.setdefault('k',1); for it in d.items(): ...` | M | — |
| R2 | list core: `extend insert pop sort(reverse,key-fn?) index count remove clear copy`, `xs += [..]`, `xs * n` | unlowered-call / operator | `xs.extend([2]); xs.sort(); xs.remove(1); xs += [3]` | M | — |
| R3 | str methods tier-2: `count index splitlines rsplit partition rpartition rfind isdigit isalpha isalnum isspace isupper islower title capitalize casefold swapcase zfill ljust rjust center removeprefix removesuffix expandtabs`, `startswith/endswith(tuple)`, `split(sep, maxsplit)`, `encode('utf-8')` | unlowered-call | each: `print('a b'.count('a'))` style probes | M | — |
| R4 | `print` kwargs `sep`/`end` + `print` of bytes | keyword-args | `print('a','b',sep='-',end='!')` | S | — |
| R5 | scalar casts: `int(str)` (incl. base?), `float(str)`, `str(bool/None/bytes-decode)` | call-arity | `x: int = int('42'); y: float = float('1.5')` | S | — |
| R6 | unpacking assignment: `a, b = pair`, `a = b = c`, `for i, x in enumerate(xs)`, `for k, v in d.items()`, starred `first, *rest` | construct-other | `for i, x in enumerate(['a','b']): print(i, x)` | M | R1 (items), R2 |
| R7 | for-iterable widening: `str`, `dict` keys default, `list[list[T]]`, `list[tuple]`, `bytes`, file lines | construct-other | `for c in 'ab': ...; for row in grid: ...` | M | R1 |
| R8 | exception binding forms: `except E as e` (+ `str(e)`), bare `except`, `raise`, `raise ... from`, custom exc ctor args beyond one str | except-as / type-mismatch | `except ValueError as e: print(e)` | M | — |
| R9 | `try/else` + `try/finally` | construct-other | `try: ... finally: cleanup()` | M | R8 |
| R10 | catchable runtime errors: `//`/`%`/overflow raise through `PsExc` instead of trapping; add `FileNotFoundError TypeError AttributeError StopIteration RuntimeError` names | construct-other (semantic/runtime) | `try: 1//0 except ZeroDivisionError: ...` prints caught | M | E2 M2–M4 |
| R11 | f-string format spec subset: width/align/precision/type (`{x:>10}`, `{x:.2f}`, `{x:05d}`), `{x=}` debug, `!r`/`!s` | construct-other | `f'{pi:.2f}'` → `3.14` | L | — |
| R12 | numeric tower: `/` true division, int `%`, mixed int/float compare+arith, `**` float/neg, unary `+`, bitwise ops | operator | `print(7/2); print(7%3); print(1 < 2.5)` | M | — |
| R13 | callables as values pack 1: named-lambda annotation + `f = lambda x: ...`, function values passed as args | local-inference / unlowered-call | `f = sorted(..., key=lambda i: -i)` already works; `g = f; g(2)` | L | — |
| R14 | `for...else`, `while...else` | construct-other | loop-else prints on no-break | S | — |
| R15 | match patterns: wildcard `_`, capture, `|` or, guard `if`, `None` | construct-other | `match x: case _: ...` | M | — |
| R16 | `str.format` + full `%` spec set | unlowered-call | `'{}-{}'.format(1,'a')` | M | — |
| R17 | set algebra: `| & - ^` + `discard/remove/union/intersection` | operator / unlowered-call | `a \| b` on two `set[int]` | S | — |
| R18 | container equality + `is`: `list==`, `dict==`, `tuple==`, `set==`, `x is y` identity for classes | operator | `print([1]==[1])` | M | — |
| R19 | slice assignment `xs[0:1] = [..]` + `del` of attr/subscript/read-later | construct-other | `xs[0:1]=[9]` | S | — |
| R20 | builtin pack: `divmod hex oct bin pow repr sorted(reverse=) sum(..,start) min/max(iterable|key=)` | call-arity / unlowered-call | `print(divmod(7,3))` (needs R6 for unpack) | M | R6, R16(repr path) |
| R21 | `input()`, `sys.stdout/stderr.write`, `print(..., file=)` | stdlib:sys / keyword-args | `sys.stderr.write('x')` | S | — |
| R22 | nested defs + closure read/write + `nonlocal` + `global` module-slot write | construct-other | `def outer(): x=1; def inner(): return x` | L | — |
| R23 | callable values pack 2: functions/lambdas stored in vars/dicts, `functools.partial`, user decorators | unlowered-call | `h = f; h(2)` | L | R22 |
| R24 | iterator protocol: `iter/next`, `map/filter/reversed` as values, `range/enumerate` as values, genexprs | construct-other | `it = iter(xs); next(it)` | L | R22 |
| R25 | dunder dispatch: `__str__/__repr__/__eq__/__len__/__iter__/__enter__/__exit__`, `super()`, multiple inheritance, `with` on custom CMs | unlowered-call / class-keywords / with-as | `str(p)`; `len(p)`; `with CM() as c:` | L | R23 |
| R26 | `Optional[T]` / union value representation (tagged union) | union-types / optional-types | `def f(x: int|None)` | L | — |
| R27 | `re.search`/`re.match` → named `Match` (or `Match|None`), `re.Pattern` locals, flag args, `re.subn` | unlowered-type-name / stdlib:re | `m = re.search('(a)', 'a'); m.group(1)` | M | R26 |
| R28 | JSON floats + null | stdlib:json | `json.loads('1.5')`, `json.loads('null')` | M | R26 (null), R12 |
| R29 | `collections.Counter`/`defaultdict`/`namedtuple` | stdlib:collections | `Counter('aab')['a']` | M | R6, R26 |
| R30 | `itertools` rest: `islice product combinations accumulate zip_longest takewhile groupby` (+ decide on infinite `count/cycle`) | stdlib:itertools | `list(islice(xs, 2))`-equivalent for-loop | M | R24 |
| R31 | import forms: `import` inside functions, `from os.path import join`, `os.path` alias to posixpath table, star-import policy | import-table / module-statements | `def f(): import os` | S | — |
| R32 | `os.environ`, `os.getenv(k, dflt)` (+ 1-arg after R26), `os.uname`, `shutil.which`, `tempfile.NamedTemporaryFile` | stdlib:os / stdlib:shutil / stdlib:tempfile | `os.getenv('HOME','x')` | S | R26 for 1-arg getenv |
| R33 | `pathlib` rest: `iterdir/glob/mkdir/with_name`, `p / 'x'` | stdlib:pathlib / operator | `for p in Path(d).iterdir()` | M | R24 |
| R34 | missing modules (per wave-c ranking): `random`, `csv`, `textwrap`, `copy`, `argparse` | import-table / queued-stdlib | `random.seed(1); random.randint(0,1)` | M (argparse L) | wave-c order |
| R35 | BUG: module-scope `a if c else b` / `x or y` crash `KeyError '.ps_ifexp_1'` — fence or lower, don't SpikeError | module-assign (bug) | module-level ternary either compiles or fences cleanly | S | — |
| R36 | bare annotated declarations `x: int` (definitely-assigned later), `__name__` value, `x = None` on typed local (with R26) | local-inference / name-resolution | `x: int\nx = 5` | S | R26 for None half |
| R37 | str/list identity semantics check: `x is y` on objects, `id()` | operator | `a is b` | S | R18 |
| R38 | checker-alignment pack: `os.getenv` Optional annotation, `struct.unpack`→`list[int]` alias, `tuple._N` fields under mypy, `__eq__(other: object)`-compatible signature, `frozenset`→`set[int]`, `from posixpath import` | type-mismatch (checker) | each repro compiles without `--skip-check` | M | R26 partial |
| R39 | comprehension widening: dict/set comprehensions, nested `for`s | construct-other | `{i: i*2 for i in xs}` | M | R6 |
| R40 | `with` generality: multi-item `with`, `with ... as` on file-class receivers beyond open | with-as | `with open(a) as f, open(b) as g:` | S | R25 for custom CMs |

### Dependency graph (text)

```
R26 ──> R27, R28, R29, R32, R36, R38
R22 ──> R23 ──> R24 ──> R25, R30, R33
R1  ──> R6 ──> R7, R20, R39
R8  ──> R9
R10 ──> E2 epic ordering (wave plan)
R16 ──> R20 (repr shares machinery)
```

### Sequencing notes

- **R1–R5 are the "first batch"**: highest real-script frequency, all
  table/lowering-local work, zero architecture. Together they unblock the
  modal `word_freq`-style script shape (parse → accumulate → print).
- **R6 is the single highest-leverage construct change**: `for i, x in
  enumerate(...)` / `for k, v in d.items()` / `a, b = f()` are all one feature
  (tuple unpack targets) that three idioms share.
- **R10 belongs inside E2's milestone plan** (M2–M4 already own the 70-name
  table); the trap→raise path is the only genuinely new machinery.
- **R13/R22–R25 are one architecture** (closure cells + callable values +
  dispatch); split as listed so each lands green, but design them together.
- **R26 is the known epic** (tagged unions) — it gates `getenv(1-arg)`, JSON
  null, `re.Match`, `dict.get` without default, and `except as` ergonomics.
- **R35 is a real bug found by the sweep** (module-level ternary/boolop
  SpikeError), filed as its own S task — it is not a fence-family gap.
- Checker-only gaps (R38) may be cheapest as annotation aliases
  (`tuple[int, ...]` for unpack, `Match`/`Pattern` names) — decide per item
  whether to teach mypy or teach the lowerer.
