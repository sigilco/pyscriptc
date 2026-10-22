# What is supported

builtins: 25 🟢 · 22 🟡 · 23 🔴 | stdlib: 0 🟢 · 48 🟡 · 2 🔴

🟢 fully supported (every probed form compiles) · 🟡 partial · 🔴 not supported.

A **fence** is a coded refusal: when a construct is outside the supported subset, the compiler stops with a `PSxxxx` diagnostic (`pyscriptc explain PSxxxx`, `pyscriptc coverage FILE` lists every fence in a file).

Tiers [T0 to T3](../spec/refused-features.md): T0 refuse forever, T1 island only, T2 build-time desugar, T3 warning lane.

**Fence codes are per construct, not per module.** There is no per-module code table. `PS1xxx` annotations and types, `PS2001` a name missing from an otherwise lowered module (`math.asin`), `PS2002` an unlowered construct or an import outside the closed table, `PS2003` unlowered call arguments (keywords, arity), `PS2004` operators. `PS2101` (queued module) exists but no module is queued today. `PS4xxx` (refused by design) is a spec in [refused-features.md](../spec/refused-features.md) and not yet emitted. The fences column below is measured: the codes the compiler really emits for the unsupported forms listed in that row.

The census in `tests/test_census_self.py` counts something else: it compiles pyscriptc's own source (61 compiler modules, 2052 fences) to track self-hosting, not stdlib support.

The unsupported column lists probed gaps. Any other name of a 🟡 module also fences with `PS2001`. A module that is not in the closed import table fences with `PS2002` on the import.

Targets CPython 3.14. Deliberate differences from CPython: [divergences.md](../../../docs/reference/divergences.md). Library results: [compat-top100.md](compat-top100.md).

## Builtins

| builtin | status | not supported | fences |
| --- | --- | --- | --- |
| `bytes`, `bytearray`, `memoryview`, `callable`, `complex`, `dict`, `format`, `tuple`, `type`, `object`, `super`, `vars`, `dir`, `globals`, `locals`, `exec`, `issubclass`, `ascii`, `setattr`, `delattr`, `slice`, `hash`, `__import__` | 🔴 | all (`dict` displays and `{}` work; `dict(...)` does not) | PS2002 |
| `BaseException` and subclasses | 🟡 | ExceptionGroup | PS2003 |
| `eval` | 🟡 | anything but a constant expression | PS2002 |
| `float` | 🟡 | float(bool) | PS2003 |
| `frozenset` | 🟡 | non-constant argument | PS2002 |
| `id` | 🟡 | id of int | PS2003 |
| `input` | 🟡 | prompt argument | PS2003 |
| `int` | 🟡 | base argument, int(bool) | PS2003 |
| `isinstance` | 🟡 | tuple of types | PS2001 |
| `list` | 🟡 | list(str) | PS2002 |
| `min`, `max` | 🟡 | key= a function name | PS2003 |
| `open` | 🟡 | encoding= and other keywords | PS2003 |
| `print` | 🟡 | file= other than sys.stdout or sys.stderr | PS2002 |
| `sorted` | 🟡 | key= a function name, sorted(dict) | PS2003 |
| `str` | 🟡 | str of a user class | PS2003 |
| `zip` | 🟡 | more than 2 lists | PS2003 |
| `abs`, `all`, `any`, `bin`, `chr`, `divmod`, `enumerate`, `getattr`, `hasattr`, `hex`, `iter`, `len`, `map`, `next`, `oct`, `ord`, `pow`, `range`, `repr`, `reversed`, `round`, `sum`, `bool`, `set` | 🟢 |  |  |
| `filter` | 🟢 |  |  |

## Standard library

| module | status | not supported | fences |
| --- | --- | --- | --- |
| `typing`, `typing_extensions` | 🔴 | annotation-only: imports are free, values are checked, there is no runtime surface |  |
| `argparse` | 🟡 | subparsers, nargs=, choices= | PS2003 |
| `asyncio` | 🟡 | Queue, Event, Lock, create_task, wait_for, get_event_loop, sleep outside a coroutine | PS1106, PS2001, PS2002 |
| `atexit` | 🟡 | register of a function with arguments, unregister | PS2001 |
| `base64` | 🟡 | b32, b16, a85, encodebytes | PS2001 |
| `bisect` | 🟡 | insort, bisect, key=, list[str] | PS2001 |
| `collections` | 🟡 | ChainMap | PS1106 |
| `contextlib` | 🟡 | closing, redirect_stdout, ExitStack, contextmanager | PS2001 |
| `copy` | 🟡 | copy of a user class | PS2001 |
| `csv` | 🟡 | DictReader, writer on a non-file | PS2001 |
| `ctypes` | 🟡 | CDLL, c_int(...), sizeof of other types | PS2001 |
| `dataclasses` | 🟡 | field(), asdict, frozen=, order= and other options | PS1201, PS2001 |
| `datetime` | 🟡 | timezone and tzinfo, utcnow, datetime.strftime | PS2001 |
| `enum` | 🟡 | Flag, str-valued members, methods on the enum | PS2002 |
| `errno` | 🟡 | errorcode | PS2001 |
| `fnmatch` | 🟡 | fnmatchcase, translate | PS2001 |
| `functools` | 🟡 | reduce with a lambda, reduce with an initializer, partial, wraps, lru_cache, cmp_to_key, cache, total_ordering | PS2001, PS2002 |
| `hashlib` | 🟡 | blake2, sha3, sha224, new, pbkdf2_hmac, incremental update (no-argument constructor) | PS2001, PS2003 |
| `hmac` | 🟡 | hexdigest, digestmod= | PS2001 |
| `importlib.resources` | 🟡 | read_text, is_resource | PS2001 |
| `importlib` | 🟡 | import_module, reload | PS2001 |
| `inspect` | 🟡 | getsource, isclass, signature, iscoroutinefunction | PS2001 |
| `io` | 🟡 | TextIOWrapper, BufferedReader, io.open | PS1106 |
| `itertools` | 🟡 | count, cycle (infinite), permutations, pairwise, batched, tee, dropwhile, compress | PS2001 |
| `json` | 🟡 | load, dump (files), ensure_ascii=False, JSONDecodeError | PS1106, PS2001, PS2003 |
| `logging` | 🟡 | basicConfig(level=...), format args (`logging.info(fmt, a)`), exception, handlers (StreamHandler) | PS2001, PS2003 |
| `math` | 🟡 | asin, sinh, degrees, fsum, factorial, comb, isqrt, copysign, fmod, cbrt, log with a base | PS2001, PS2003 |
| `operator` | 🟡 | itemgetter, attrgetter, methodcaller, and_, not_, getitem | PS2001 |
| `os` | 🟡 | walk, scandir, system, urandom, cpu_count, kill, symlink, rmdir, getuid, keyword arguments (makedirs exist_ok) | PS2001, PS2003 |
| `pathlib` | 🟡 | rglob, absolute, rmdir, with_suffix, relative_to, resolve to str, PurePath | PS2001 |
| `posixpath` | 🟡 | commonpath, ismount, lexists | PS2001 |
| `random` | 🟡 | sample, gauss, Random, choices(k=) | PS2001, PS2003 |
| `re` | 🟡 | finditer, 5+ groups, sub with a function | PS1001, PS2001, PS2002 |
| `shutil` | 🟡 | make_archive, disk_usage, chown | PS2001 |
| `socket` | 🟡 | socket(), gethostbyname | PS2001 |
| `ssl` | 🟡 | contexts (create_default_context) | PS2001 |
| `stat` | 🟡 | S_IMODE, S_ISFIFO, S_ISSOCK, filemode | PS2001 |
| `string` | 🟡 | capwords, Template | PS2001 |
| `struct` | 🟡 | calcsize, unpack_from, non-literal format | PS2001 |
| `subprocess` | 🟡 | Popen, getoutput, text=True | PS1003, PS2001 |
| `sys` | 🟡 | stdin, path, modules, getsizeof, getrecursionlimit, stdout.flush | PS2001 |
| `tempfile` | 🟡 | mkstemp, TemporaryDirectory | PS2001 |
| `textwrap` | 🟡 | TextWrapper | PS2001 |
| `threading` | 🟡 | main_thread, Thread target is a callable, Timer, Lock in `with` | PS1003, PS1106, PS2001, PS2002 |
| `time` | 🟡 | perf_counter, time_ns, ctime, process_time | PS2001 |
| `tomllib` | 🟡 | load (file object), keyword arguments | PS2001, PS2003 |
| `unicodedata` | 🟡 | ucd_3_2_0 (also: Latin-1 only) | PS2001 |
| `urllib.parse` | 🟡 | urlparse, urljoin, urlunsplit, parse_qsl | PS2001 |
| `warnings` | 🟡 | warn with a category, simplefilter | PS2001 |

Regenerate with `uv run python scripts/gen_support_table.py`; `tests/test_support_docs.py` fails when this file drifts from the compiler. Scripts that stay inside the supported set: [examples](../../../docs/examples/programs.md).
