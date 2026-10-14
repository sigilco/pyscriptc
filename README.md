# pyscriptc

A compiler that lowers a small, annotated subset of Python to a typed IR and then to LLVM. Parsing is CPython’s own `ast` module. [mypy](https://mypy-lang.org/) is a preflight: its tree is not an input. Anything the subset does not prove is a fence, not a guess.

The pipeline follows [scriptc](https://github.com/vercel-labs/scriptc): a checker, a typed IR, then LLVM. The IR is not scriptc’s. `int` here is a Python int. The LLVM backend uses `i64` only when the value fits, traps on overflow, and refuses an integer literal that would have to be truncated. Calls that stay on the island (`subprocess.check_output`, `socket.gethostname`, `threading`, `inspect.cleandoc`, `logging`) initialize embedded CPython on first use. libpython is linked only when the LLVM text contains one of those calls.

The benchmark layout is scriptc’s `benchmarks/` directory. See [benchmarks/README.md](benchmarks/README.md).

The design is in [ARCHITECTURE.md](ARCHITECTURE.md). How to compile a script is in [docs/](docs/README.md). Contributor notes are in [.agents/docs/](.agents/docs/README.md). Programs that compile today, including an invoice, an access-log gate, and a URL allow-list, are in [examples/](examples/README.md).

Licensed under Apache 2.0. Copyright 2026 Joaquin Terrasa and contributors.

## Subset

The IR stamp is `gates-v1`. Lowered:

- Annotated functions whose parameters and return use `int`, `float`, `bool`, `str`, `bytes`, `list`, `dict`, or an annotated class
- `if`, `while`, `for` over a `list` or `range`, assignment, `return`
- `int` `+`, `-`, `*`, `//`, unary `-`, and comparisons
- `float` `+`, `-`, `*`, and comparisons (`NaN` follows Python: `nan != nan`)
- `str` and `bytes` concatenation, indexing, ordering, and `in`
- `len` on `str`, `bytes`, `list`, `dict`, and `collections.deque`
- `abs`, `min`, `max`, `sum`, `sorted`, `ord`, `chr`, `round`, `isinstance`, `getattr`, and `hasattr` (the attribute name is a string literal)
- `open` with a literal mode `r`, `w`, `rb`, `wb`, `a`, or `ab`
- `int(float)` (toward zero) and `float(int)`
- Latin-1 `str` and `bytes` methods: `strip`, `lstrip`, `rstrip`, `upper`, `lower`, `startswith`, `endswith`, `find`, `replace`, `split`, `join`, plus `encode()` and `decode()` with no extra arguments
- `list` and `dict` literals, indexing, and item assignment. Assignment shares the buffer
- `try` / `except` for `ValueError`, `IndexError`, `KeyError`, `OverflowError`, `ZeroDivisionError`, `RuntimeError`, `Exception`, and a user subclass of one of those
- `*args` of one element type, lowered as a `list` of that type. A `set` of `int` or `str`: construction, `add`, membership, `len`, and iteration
- Annotated classes: fields, `__init__`, methods, and a single base. Layout is the parent's fields, then the class's own fields. Method lookup walks the parent. `@property` (getter only), `@staticmethod`, and `@classmethod` on methods. A flat `@dataclass` generates `__init__` from annotations. `enum.IntEnum` and `enum.Enum` members are integer constants. A subclass of `Exception` (or another lowered exception) can be raised and caught as its own type and as the parent
- `import` / `from` against the closed table in `pyscriptc.stdlib_queue`. `math` is lowered (`sqrt`, `fabs`, `sin`, `cos`, `tan`, `exp`, `log`, `log2`, `log10`, `hypot`, `pow`, `floor`, `ceil`, `trunc`, `isnan`, `isinf`, and `pi`, `e`, `tau`, `inf`, `nan`). The codec wave is lowered too: `re` (`escape`, `findall`, `sub`, `split`, `compile`), `json` (`loads`, `dumps`), `urllib.parse` (`quote`, `quote_plus`, `unquote`, `unquote_plus`, `urlencode`, `parse_qs`, `urlsplit`), `base64` (`b64encode`, `b64decode`, and the standard and urlsafe pairs), `hashlib` (`md5`, `sha1`, `sha256`, `sha384`, `sha512`), and `struct` (`pack`, `unpack` of a literal format). The rest of the queue is lowered as a closed surface: `datetime`, `time`, `collections`, `functools`, `itertools`, `operator`, `contextlib`, `os`, `posixpath`, `pathlib`, `stat`, `fnmatch`, `warnings`, `io`, `string`, `bisect`, `errno`, and the island modules `sys`, `importlib`, `inspect`, `threading`, `logging`, `asyncio`, `subprocess`, `socket`, `ssl`, and `ctypes`. Also lowered, as closed names: `importlib.resources.files` / `as_file`, `hmac.digest` / `new` / `compare_digest` for a literal `sha256` / `sha384` / `sha512` (and `md5` / `sha1`), Latin-1 `unicodedata`, `tempfile.gettempdir` / `gettempprefix` / `mkdtemp`, `atexit.register` of a no-argument function, `shutil.copyfile` / `rmtree`, `enum.Enum` / `enum.IntEnum` as integer members, and `dataclasses.dataclass` as a flat `__init__`. Anything outside the table fences at the import (`PS2002`). A name added to the queue and left out of the lowered set would fence with `PS2101`

Fenced (no LLVM for that file):

- `and` / `or` across two types, f-string format specs, a slice step other than 1, `break`, `continue`, `finally`, `except as`, user decorators, `super()`, multiple inheritance, `**kwargs`, keyword-only and positional-only parameters, and starred calls
- String literals outside Latin-1
- `bool` arithmetic, `list[bool]`, and `dict` values of `bool`. `bool` is not promoted to `float`
- Calls such as `str.encode("ascii")`, `math.gcd`, `functools.partial`, `itertools.count`, `operator.itemgetter`, `importlib.import_module`, and `asyncio.run` that are outside the lowered surface
- `with` except `contextlib.suppress(Exc)`, `contextlib.nullcontext()`, `with open(...) as name`, and `with importlib.resources.as_file(...) as name`
- `itertools.repeat` and `itertools.chain` except as the iterator of a `for`
- `re.findall` with more than four capturing groups. A literal pattern of two to four groups returns a tuple of strings. A dynamic pattern with two or more groups raises `ValueError`
- Float JSON, JSON null, and non-ASCII JSON text
- `struct` format `@`, a format that is not a string literal, and `Q` values that do not fit in `i64`
- Keyword arguments, including `re` flags and `base64` `validate`

`//` is Python floor division, including negative operands. Division by zero and signed `i64` overflow abort the process. They do not wrap. `math.floor`, `math.ceil`, `math.trunc`, and `int(float)` raise `OverflowError` when the result does not fit in `i64`. An uncaught exception prints its name to stderr and exits 1.

A `for` target that is new in the function is stored as `0`, `0.0`, or `""` before the loop. After `for i in range(0)`, this subset reads `i` as `0`. CPython would raise `UnboundLocalError`. A target that already had a value keeps that value when the loop body never runs.

`in` on a list uses `==`. `math.nan in [math.nan]` is false here. CPython's list finds that same object by identity before it compares.

`typing` stays in mypy. It is not a runtime lowering. Island constants (`sys.platform`, `sys.version`, `sys.executable`, `sys.maxsize`, `socket.AF_INET`, `ssl.OPENSSL_VERSION`, `ssl.HAS_SNI`, `stat.S_IFREG`, `ctypes.sizeof`) are baked from the interpreter that lowers the file. `sys.exit` is libc `exit`. `subprocess.check_output` takes a `list[str]` and returns `bytes`. `os.getenv` raises `KeyError` when the variable is unset. `date` plus `timedelta` uses the normalized day count; `datetime` plus `timedelta` uses the whole delta.

## Requirements

- CPython 3.14 (the IR is stamped with the interpreter that lowered it)
- [uv](https://docs.astral.sh/uv/)
- Clang that can assemble textual LLVM IR

## Run

```bash
uv sync
uv run pyscriptc compile examples/sum_squares.py --run --call sum_squares --args '[10]' -o /tmp/sum_squares
```

`sum_squares(10)` prints `285`, the same value CPython prints. `--args` is a Python literal parsed with `ast.literal_eval`, so integers are not rounded through JSON.

Other corpus programs:

```bash
uv run pyscriptc compile examples/longer.py --run --call longer --args '["bb", "a"]' -o /tmp/longer
uv run pyscriptc compile examples/floor_div.py --run --call floor_div --args '[-3, 2]' -o /tmp/floor_div
uv run pyscriptc compile examples/gates_math.py --run --call floors -o /tmp/floors
uv run pyscriptc compile examples/gates_probe.py --run --call probe -o /tmp/probe
uv run pyscriptc compile examples/fence_method.py --emit llvm -o /tmp/fence.ll
```

`floors()` prints `3`. `probe()` prints `0`. The fence exits nonzero, names `encode`, and does not write LLVM. `examples/wave_probe.py` prints `0` when the codec wave matches CPython. `examples/rest_probe.py` prints `0` when the rest of the queue matches CPython. `examples/fence_pickle.py` exits nonzero with `PS2002` because `pickle` is outside the import table. `examples/invoice.py`, `examples/access_log.py`, `examples/request_id.py`, `examples/release_note.py`, `examples/blob_digest.py`, `examples/url_gate.py`, and `examples/hostname.py` are the scripts in [docs/examples.md](docs/examples.md). `examples/bad_annotation.py` fails in mypy before lowering. `examples/big_literal.py` lowers to IR, then LLVM emission refuses the integer because it does not fit in `i64`.

`re.compile` returns a `Pattern` with a `pattern` field. `findall`, `sub`, and `split` on that object use the field. `hashlib` returns an opaque `Hash`; `hexdigest` and `digest` are the methods that lower. `urlsplit` returns a `SplitResult` of five strings (`scheme`, `netloc`, `path`, `query`, `fragment`). `struct.unpack` is a list, so `[0]` is the first value. `json.loads` takes its type from the annotation it is assigned to, or from the return annotation. `parse_qs` splits on `&` only, which is what CPython 3.14 does. A pattern that can match empty forever, such as `()*`, stops with `RuntimeError` once the matcher spends its fuel. `re.error`, `struct.error`, and `binascii.Error` surface here as `ValueError`.

`--emit ir` writes the JSON IR. `--emit llvm` writes textual LLVM. The CPython version is a field on the module. Emitting LLVM from an IR stamped by a different version is an error: re-lower it and re-run the corpus.

## Benchmarks

```bash
uv run python -m benchmarks.baseline.run --check
uv run python -m benchmarks.runtime.run --runs=5
uv run python -m benchmarks.scaling.run
```

`benchmarks/baseline/reference.json` is the reference metric set: wall time and peak RSS for each lowered workload, against the same call under CPython. Printed figures are speedups, `Nx CPython`, the reciprocal of the stored pyscriptc/CPython ratio, so a larger number is a bigger gain. `--check` still compares the stored ratios: it fails when a time ratio grows past 2× that set, or an RSS ratio grows past 1.25×. A reference of 10.0x CPython therefore fails once the run prints less than 5.0x.

The kernel geomean is the original four loops. The stdlib rows time every module that has a lowered call, including the builtin helpers. `importlib.import_module` and `asyncio.run` are fences, so those calls are not timed. `importlib.resources`, `hmac`, and Latin-1 `unicodedata` have their own rows. Calls that embed CPython (`socket.gethostname`, `inspect.cleandoc`, `threading`, `logging`, `subprocess.check_output`) are the island group: they link libpython, and their speedup sits near 1x CPython. `benchmarks/adoption` records why pyperformance, click, typer, pip, and black are not compiled suites yet.

## Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## What this does not prove

A new CPython release is not supported yet in the product sense. The stamp and the fences are the hooks that make that possible later: unknown syntax fails closed, and the backend will not silently compile IR from an interpreter it has not been run against. Island calls embed that same interpreter, and only the binary that actually calls them links libpython.
