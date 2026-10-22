# Divergences from CPython

pyscriptc targets CPython 3.14.x. Parsing, probes and the compatibility oracle follow 3.14. Compiled programs match CPython 3.14 on stdout for everything that lowers, except the numbered divergences below. The empty divergence space is load-bearing: anything not listed here is claimed identical, and a difference you find that is not listed is a bug.

## Parity contract

- stdout: byte-exact for every lowered call. The corpus scorer today compares whitespace-stripped stdout; tightening that to unstripped bytes is planned work in `.agents/plans/2026-10-22-cpython-version-pin.md` (W4).
- stderr: for a `raises` probe, only the exception name must appear. Full traceback text is not matched.
- exit code: an uncaught exception prints its name to stderr and exits 1, as CPython does (see the README Subset section).
- Anything that does not lower is a named `PSxxxx` fence, not a divergence. See [supported.md](supported.md) and [refused-features.md](refused-features.md).

## Numbered divergences

1. `int` is `i64`. Overflow aborts the process instead of growing a big int. Source: [supported.md](supported.md) (Language, `int` paragraph), [refused-features.md](refused-features.md) Tier C.
2. `//` by zero aborts the process instead of raising `ZeroDivisionError`. Source: same as item 1.
3. Strings are a Latin-1 byte model. A string literal outside Latin-1 is a fence, and `unicodedata` covers only Latin-1 code points. A normalization that would leave Latin-1 raises `ValueError`. Source: [supported.md](supported.md) (fence list, `unicodedata` paragraph).
4. `re` is the runtime's byte-oriented backtracking subset, not CPython's `re`. `L`, `T`, `DEBUG` and unknown flag bits raise `ValueError`, `A` and `U` are no-ops, and a pattern that can loop without consuming stops with `RuntimeError` once its fuel is spent. Five or more groups are refused. Source: header of `src/pyscriptc/runtime/ps_re.c`, `re` row of [supported.md](supported.md).
5. `json` has no non-ASCII text. Source: `json` row of [supported.md](supported.md).
6. Set and `frozenset` iteration is insertion order, not CPython's hash order. Length and membership match. Source: [supported.md](supported.md) (set constants), [refused-features.md](refused-features.md) Tier C.
7. After `for i in range(0)`, a new loop target reads as `0` instead of raising `UnboundLocalError`. Source: [supported.md](supported.md) (`date + timedelta` paragraph).
8. A subclass `__init__` that never assigns an inherited field leaves it at the zero value of its type instead of raising `AttributeError`. Source: [supported.md](supported.md) (subclass `__init__` paragraph).
9. `raise AppError("no")` for a user exception class stores a code and an optional `str` and does not call `__init__`. Source: same paragraph as item 8.
10. `@classmethod` and `@staticmethod` of the form `Class.method(...)` do not pass the class object. Source: same paragraph as item 8.
11. Some library failures surface as a different exception type: `importlib.resources.files` on a zip or missing package raises `ValueError` rather than `ModuleNotFoundError`, `shutil.copyfile` of the same path raises `RuntimeError` rather than `SameFileError`, other `shutil` and `os` failures are `RuntimeError`, and type errors on `unicodedata` calls surface as `ValueError`. Source: [supported.md](supported.md) (`unicodedata` paragraph).
12. `assert` is always evaluated; a compiled binary has no `-O`. Source: [refused-features.md](refused-features.md) Tier C.
13. `x is None` lowers as `==` against `None`. Identical for `None` because it is a singleton; every other `is` on a scalar is refused. Source: [refused-features.md](refused-features.md) Tier C.
14. `ABC`, `ABCMeta`, `Protocol`, `@abstractmethod` and `@overload` lower as plain classes and methods with no CPython runtime semantics. Source: [refused-features.md](refused-features.md) Tier C.
15. Island calls (`subprocess.check_output`, `socket.gethostname`, `threading`, `logging`) run in an embedded CPython, so those follow the interpreter the binary links, not the pin. Source: README "What it does", and the opt-in `--fallback=cpython` tier.
