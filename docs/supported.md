# What is supported

The subset stamp is `gates-v1`. Parsing is CPython 3.14's `ast`. A lowered call matches CPython on that call. The language around it is the list below.

## Language

- Functions with annotations on every parameter and on the return. A constant default is stored and applied when the call omits it. Keyword arguments fill those parameter names
- `int`, `float`, `bool`, `str`, `bytes`, `list[T]`, `dict[K, V]`, `set[int]` or `set[str]`, and a class you define in the same file
- `if`, `while`, `for` over a `list` of scalars, over `range`, over `enumerate` of a list, or over `zip` of two lists, assignment, `return`. `if` and `while` also accept a `str`, `bytes`, `int`, `list`, or `dict` as a condition. `enumerate` and `zip` are only the iterator of a `for`, and the target is one name holding the tuple
- `and` / `or` when both sides have the same type. The result is that value, and the right side runs only when the left side needs it
- A module-level constant: an annotated assignment, or a bare assignment of a literal, list, dict, or tuple of two or more constants. A function can read it. `if sys.version_info` compared with a tuple of ints (`>=`, `>`, `<=`, `<`, `==`, `!=`, either side) keeps only the taken branch. Any other `if` at module scope is a fence. `name = None` plus `global name` is a process slot of `str`, `int`, or `Path`; `name is None` is the unset flag
- `try` / `except` for `ValueError`, `IndexError`, `KeyError`, `OverflowError`, `ZeroDivisionError`, `RuntimeError`, and `Exception`
- `raise ValueError("message")` and the same names
- Classes with annotated fields, `__init__`, and methods. Fields are annotations with no value. A single base class is allowed; multiple inheritance fences
- `import` and `from` for the modules named below

`int` supports `+`, `-`, `*`, `//`, unary `-`, and comparisons. `//` is Python floor division. Overflow and division by zero abort the process.

`float` supports `+`, `-`, `*`, and comparisons. `nan != nan`.

`str` and `bytes` support concatenation, indexing, ordering, `in`, and slices with any step. Methods: `strip`, `lstrip`, `rstrip`, `upper`, `lower`, `startswith`, `endswith`, `find`, `rfind`, `replace`, `split`, `rsplit`, `splitlines`, `partition`, `rpartition`, `join`, `count`, `index`, `title`, `capitalize`, `casefold`, `swapcase`, `isdigit`, `isalpha`, `isalnum`, `isspace`, `isupper`, `islower`, `zfill`, `ljust`, `rjust`, `center`, `removeprefix`, `removesuffix`, `expandtabs`. `split` and `rsplit` take an optional `maxsplit`. `str.encode()` and `bytes.decode()` take no extra arguments and use UTF-8, and the result has to stay inside Latin-1. String literals are Latin-1, one byte per code point. `str(int)` and `str(float)` lower. An f-string and `%s` / `%d` lower when there is no format spec.

`len` works on `str`, `bytes`, `list`, `dict`, and `collections.deque`. Also lowered: `abs`, `min`, `max`, `sum`, `sorted` (including `key=lambda`), `ord`, `chr`, `round`, `isinstance`, `getattr`, `hasattr`, `bool`, `list`, `all`, `any`, and `print` (positional `str`/`int`/`float`/`bool`/`bytes` values, plus `sep` and `end`). `int(str)` and `float(str)` parse; `str()` of `bool` and `None` lower. `sorted(key=)` iterates `int`, `float`, `str`, or `bytes` elements. The attribute name in `getattr` and `hasattr` is a string literal. `int(float)` truncates toward zero. `float(int)` widens. `bool()` is the truthiness of `bool`, `int`, `float`, `str`, `bytes`, `list`, `dict`, and `set`, including `bool(nan)`. `all()` and `any()` use that same truthiness on a list or a set. `list()` copies a `list`, a `set`, or `*args` into a new `list`.

`list` and `dict` literals, indexing, and item assignment work. `list` methods: `append`, `extend`, `insert`, `pop`, `sort`, `reverse`, `index`, `count`, `remove`, `clear`, `copy`; `xs += [...]` and `xs * n` also lower. `dict` methods: `get`, `values`, `items`, `pop`, `setdefault`, `update`, `clear`; `del d[k]` works. Dict keys are `int` or `str`. `list[bool]` and dict values of `bool` are refused. A tuple of two or more fields is the same synthetic class as before (`tuple_N_…` with fields `_0`, `_1`, …). A constant index reads a field. Tuple equality stays a fence. `open` takes a path and a literal mode `r`, `w`, `rb`, `wb`, `a`, or `ab`.

`with` works for `contextlib.suppress(SomeError)`, `contextlib.nullcontext()`, `with open(...) as name`, and `with importlib.resources.as_file(...) as name`. The file is closed on the way out, including a `return` and an exception. `as_file(...)` as a call is that same `Path`. `Path.__enter__` returns the path and `Path.__exit__` is a no-op, so `certifi.core.where` matches CPython from one `files` lookup and does not link libpython. `itertools.repeat` and `itertools.chain` work as the iterator of a `for`.

## Standard library

Each module below is a closed set of calls, not the whole module.

| Module | Lowered |
| --- | --- |
| `math` | `sqrt`, `fabs`, `sin`, `cos`, `tan`, `exp`, `log`, `log2`, `log10`, `hypot`, `pow`, `floor`, `ceil`, `trunc`, `isnan`, `isinf`, `pi`, `e`, `tau`, `inf`, `nan` |
| `re` | `escape`, `findall`, `sub`, `subn`, `split`, `compile`, `search`, `match`, and `fullmatch`, plus the int flags. A literal pattern with two to four groups returns a tuple of strings. Five or more groups is refused. `search`, `match`, `fullmatch`, and `Pattern`'s own methods return a `Match | None` that narrows with `is not None`; `Match` lowers `group`, `start`, `end`, and `span`, with `group` and `span` taking an optional group index. The regex flavor is the runtime's backtracking subset, documented at the top of `src/pyscriptc/runtime/ps_re.c`: `I`/`M`/`S`/`X`/`A`/`U` are honored, `L`/`T`/`DEBUG` and unknown bits raise at the call |
| `json` | `loads`, `dumps` of ints, bools, strings, floats (incl. `NaN`/`Infinity`), `Optional[T]` (JSON `null`), lists, and dicts of those. No non-ASCII text |
| `urllib.parse` | `quote`, `quote_plus`, `unquote`, `unquote_plus`, `urlencode`, `parse_qs`, `urlsplit` |
| `base64` | `b64encode`, `b64decode`, and the standard and urlsafe pairs |
| `hashlib` | `md5`, `sha1`, `sha256`, `sha384`, `sha512`, then `.hexdigest()` and `.digest()` |
| `struct` | `pack` and `unpack` of a literal format. No `@`. `unpack` is a list |
| `datetime` | `date`, `datetime`, `time`, `timedelta` (keywords `days`, `seconds`, `microseconds`), `isoformat`, `fromisoformat`, ordinals, `weekday`, addition |
| `time` | `time`, `monotonic`, `sleep`, `gmtime`, `localtime`, `strftime`, `mktime` |
| `collections` | `deque` (append, appendleft, pop, popleft) and `OrderedDict()` with no arguments, which is an empty dict |
| `functools` | `reduce(operator.add, list of int)`. An initializer stays fenced |
| `itertools` | `repeat` and `chain` as a `for` iterator; `islice` `product` `combinations` `accumulate` `zip_longest` `takewhile` `groupby` as eager iterator values |
| `operator` | `add`, `sub`, `mul`, `floordiv`, `neg`, comparisons, `concat` |
| `contextlib` | `suppress`, `nullcontext` |
| `os` | `getcwd`, `listdir`, `getenv`, `remove`, `rename`, `makedirs`, `stat` |
| `posixpath` | `join`, `basename`, `dirname`, `split`, `splitext`, `normpath`, `isabs`, `abspath`, `exists`, `isfile`, `isdir` |
| `pathlib` | `Path`, `joinpath`, read and write text and bytes, `exists`, `is_file`, `is_dir`, `name`, `suffix`, `stem`, `parent`, `as_posix` |
| `stat` | `S_ISREG`, `S_ISDIR`, `S_ISLNK`, and the `S_IF*` / `S_IRUSR` constants |
| `fnmatch` | `fnmatch`, `filter` |
| `warnings` | `warn` of a string, and the str constant `defaultaction` |
| `io` | `StringIO`, `BytesIO` |
| `sys` | `platform`, `version`, `executable`, `maxsize`, `argv` (the real process arguments as `list[str]`), `exit` |
| `inspect` | `cleandoc` (island) and the str constant `AGEN_CLOSED` |
| `threading` | `active_count`, `current_thread().name` (island), and the float `TIMEOUT_MAX` |
| `logging` | `info`, `warning` of a string (island), and the str constant `BASIC_FORMAT` |
| `subprocess` | `check_output` of a `list[str]`, returning `bytes` (island), and the int `DEVNULL` |
| `socket` | `gethostname` (island) and the address-family constants |
| `ssl` | `OPENSSL_VERSION`, `HAS_SNI` |
| `ctypes` | `sizeof` of `c_char`, `c_int`, `c_long`, `c_int64`, `c_size_t`, `c_void_p` |
| `string` | `ascii_lowercase`, `ascii_uppercase`, `ascii_letters`, `digits`, `hexdigits`, `octdigits`, `punctuation`, `whitespace`, `printable` |
| `bisect` | `bisect_left` and `bisect_right` on `list[int]` |
| `errno` | `EACCES`, `EAGAIN`, `EBUSY`, `ECHILD`, `EEXIST`, `EINTR`, `EINVAL`, `EIO`, `EISDIR`, `ENOENT`, `ENOSPC`, `ENOTDIR`, `ENOTEMPTY`, `ENOTTY`, `EPERM`, `EPIPE`, `EROFS`, `ESRCH`, `EWOULDBLOCK`, `EXDEV` |
| `importlib.resources` | `files` and `as_file`. The package argument is a string. `files` returns a `Path`. `read_text` accepts a literal encoding `utf-8`, `utf8`, `ascii`, `latin-1`, or `latin1` and still copies Latin-1 bytes |
| `hmac` | `digest`, `new(...).digest()`, and `compare_digest`. The algorithm is a literal `md5`, `sha1`, `sha256`, `sha384`, or `sha512` (or the matching `hashlib` name). Key and message are `bytes`. The hash runs in the small runtime |
| `unicodedata` | Latin-1 `category`, `combining`, `normalize`, `is_normalized`, `bidirectional`, `east_asian_width`, `mirrored`, `decimal`, `digit`, `numeric`, `decomposition`, `name`, `lookup`, and `unidata_version`. `ucd_3_2_0` is a fence. Names and lookups outside Latin-1 are not in the table |
| `tempfile` | `gettempdir`, `gettempprefix`, `mkdtemp`. `NamedTemporaryFile` and the other constructors stay fences |
| `atexit` | `register` of a no-argument function. `unregister` is a fence. The return value of `register` is not a value |
| `shutil` | `copyfile` and `rmtree`. `which` stays a fence |
| `enum` | `Enum` and `IntEnum` members that are integer constants. Other member types, `auto()`, and methods on the enum stay fences |
| `dataclasses` | `@dataclass` on a class with no base. It generates `__init__` from annotations, including a constant default. `field()` stays a fence |

`importlib` and `asyncio` import. `importlib.import_module` and `asyncio.run` are fences. `import typing`, `import typing_extensions`, and `from __future__ import annotations` are erased. A call such as `typing.cast` or `get_type_hints` is a fence. `List[str]`, `Dict[str, int]`, and `Set[int]` or `Set[str]` unwrap to `list`, `dict`, and `set`. `Optional` and `X | Y` stay fences.

Island calls link libpython. The constants do not.

## Finding every fence

`pyscriptc coverage FILE|DIR [--json]` lowers a file or a package tree and reports every fence, not only the first. Each entry has the PS code, a code frame, a feature family, the definition that holds it, and a refactor hint. The `chain` list ranks families by how many definitions they alone block. `compile` is unchanged and still fails closed on any fence.

`pyscriptc explain PSxxxx` describes a code. Annotation fences have their own codes:

| Code | Meaning |
| --- | --- |
| PS1101 | union annotation (`A | B`, `Union[A, B]`) |
| PS1102 | `Optional[T]` |
| PS1103 | generic alias such as `Mapping[K, V]` or `Callable[...]` |
| PS1104 | dynamic or structural type (`object`, `Any`, `Protocol`, `TypeVar`) |
| PS1105 | forward reference (a quoted annotation) |
| PS1106 | a name that is not a lowered type |

Construct fences: PS1201 decorators, PS1202 parameter forms, PS1203 class keywords or multiple inheritance, PS1204 class body statements, PS1205 `except ... as` and bare `except`, PS1206 `with ... as`.

`python -m benchmarks.compat2.census` runs `coverage` over the pinned top100 sources and ranks families by set cover.

Fence counts per compiler module are in `benchmarks/compat2/census-self.json`, generated by:

```
uv run python -m benchmarks.compat2.census_self --json benchmarks/compat2/census-self.json
```

The file's `generated` field is the UTC calendar date of the run and changes when the command is rerun on a later UTC day. `src` is `src/pyscriptc`. `mode` is `parse-only` (directory compile and `coverage`, no LLVM). `lines`, `modules`, `fences`, `definitions`, and `fencedDefinitions` are package totals; per-module `fences`, `fencedDefinitions`, `definitions`, `rootFamilies`, and `ranking` match the top100 census shape.

## Fences you will hit first

These refuse the whole file:

- Keyword arguments on a call whose parameters are not named (`math.sqrt(x=1)`)
- `if x:` when `x` is a `float`, a class, or `None`
- f-string format specs, and `%` conversions other than `%s` and `%d`
- `str.encode("ascii")` and any other extra argument on `encode` or `decode`
- User decorators, `**kwargs`, keyword-only and positional-only parameters, starred calls, `super()`, and multiple inheritance
- `with ... as` except `with open(...) as name` and `with importlib.resources.as_file(...) as name`
- A relative import in a single file. A directory compile resolves `from .mod import name`, `from . import name`, and an absolute import of that same package (`from pkg.mod import name`) for a function, a module-level constant, or a class the defining module emits, including a subclass of a class exported by another module. `__main__.py` is skipped and the compile says so: it is the `python -m` script, not an importable module. An `argparse` import in any other file stays a fence. When a class or function does not lower, the first fence is the one inside that definition, ahead of "is not exported". Two user classes with the same name and different fields stay a fence. Identical synthetic classes are skipped
- Any import outside the table (`requests`, `argparse`, …). `enum` and `dataclasses` are in the table for `Enum` / `IntEnum` and `@dataclass`
- Lambdas, a tuple of fewer than two fields, a mixed or nested `tuple[T, ...]`, a non-constant tuple index, tuple equality, a `set` whose elements are not `int` or `str`, and a generator expression
- String literals outside Latin-1
- `finally`, `except ... as`

`date + timedelta` uses the normalized day count. `datetime + timedelta` uses the whole delta. After `for i in range(0)`, a new target reads as `0` rather than raising `UnboundLocalError`.

`class C(object)` and `class C(object,)` are the same class as `class C`. `object.__init__` is not emitted. A name bound to `object` stays a real base. Multiple bases stay a fence.

`dict.keys()` is a `list` of the keys in insertion order. A constant dict folds to that list; a runtime dict copies its keys. `dict.values()` and `dict.items()` are the list of values and the list of `(key, value)` pairs in that same order.

A module assignment whose value is an f-string of `str` constants, such as `f"2014 {__author__}"`, is that one `str` constant. A format spec or a non-constant interpolation stays fenced.

`IO[bytes]` and `BinaryIO` are the `BytesIO` handle `open` returns for a binary mode. `IO[str]` and `TextIO` are the `TextIO` handle it returns for text. `IO` alone, `IO[Any]`, and any other parameter stay fenced.

`tuple[int, ...]` and the same form for any element type already lowered, including `tuple[str, ...]`, are the `list` of that element. `struct.unpack` already returns that list. The value is a list: length, index, and the sum of the ints match CPython, and the repr and the type name do not. A fixed tuple such as `tuple[int, int]` stays a synthetic class. `tuple[int, str, ...]` and a nested ellipsis stay fenced.

A module `if TYPE_CHECKING:` is omitted when that name is the `typing` or `typing_extensions` import. CPython treats the name as false, so the branch contributes no statements and an else is kept. `if not TYPE_CHECKING:` keeps the body. A user assignment of the same name stays a module `if`.

A module assignment of a set of `str` constants or `int` constants is that set: a list of the element with the set flag, copied at each use. Iteration is insertion order, not CPython's hash order. Length and membership match. A set of any other element or a non-constant element stays fenced; a module-scope set comprehension still fences as a non-constant assignment.

`x is None` and `x is not None`, including `None` on the left, are `==` and `!=` against `None` when the other operand is `None`, `int`, `float`, `bool`, `str`, or `bytes`. A process slot's `name is None` stays the unset flag. `is` against anything else stays fenced, and a list, dict, or class cannot compare with `None`.

`a if cond else b` evaluates `cond`, then exactly one operand, and stores that value with the `if` statement `and`/`or` already uses. `cond` has to be a condition `if` already accepts, both operands have one type, and the untaken operand does not run.

`frozenset` of a list or set display of `str` or `int` constants is that set list, copied at each use. Length and membership match CPython, iteration stays insertion order, and `.add` / `.remove` stay fenced on that call and on a name bound to it. A plain `set` can still `.add`. `frozenset` of one `str` constant is the set of its characters in that order, first duplicate kept, including the empty string. A code point above 255 stays fenced, and `frozenset` of bytes stays fenced.

A module assignment `re.compile` of one `str` constant, with or without a constant flags argument, is the `Pattern` that call already returns inside a function, inlined at each use. A non-constant pattern, non-constant flags, or any other form stays fenced.

`value in (c0, c1, ...)` where every element is a `str` constant, or every element is an `int` constant, is `==` joined by `or`. An empty display is false, one element is a single `==`, and `not in` is `not` of that test. A tuple name, a list, a mixed tuple, a non-constant element, and tuple equality stay fenced.

`float("inf")` and `infinity`, any case, with an optional sign and surrounding ASCII whitespace, are the float const `math.inf` already uses; `float("-inf")` is that const negated, and `float("nan")` / `float("NaN")` are `math.nan`. A name, a numeric string, and bytes stay fenced.

A float `in` a list is that element's `==`. NaN is not a member, because NaN is not equal to itself. `1.0 in [1.0]` stays true. Int and str membership are unchanged.

A chained comparison is the compares the compiler already lowers, joined by `and`. Each middle value is stored once, and a false link does not evaluate the rest. A chain that uses `is`, or a link the existing compare rejects, stays fenced.

A list comprehension filter is the existing `if` around the list push: the filter runs once per element, and a false filter does not evaluate the element. Each `for` clause may carry `if`s, a later `for` descends inside the earlier filters, and dict (`{k: v for ...}`) and set (`{x for ...}`) comprehensions lower the same way: the dict lowers to `setitem` on an empty map, the set dedupes like a set literal. Keys, set elements, and element types keep the display limits (`int`/`str` keys and set elements, no `bool` dict values, no `list[bool]`). Async comprehensions and generator expressions stay fenced.

`break` leaves the nearest `while` or `for`: code after the loop runs, and a nested `break` leaves the outer loop running. `continue` skips the rest of the nearest loop body and returns to that loop's test; the `for` step still runs, and `continue` outside a loop stays fenced.

`assert cond` is `if not cond: raise Exception`. A true assert falls through so the next statement runs, and a `str` message is evaluated only when the assert fails. A condition `if` would reject still fences, and AssertionError is not a separate exception.

`base ** exp` for two ints is a loop of `*`. The base and the exponent each run once, and `0 ** 0` is 1. A negative exponent stays fenced, and a float or any other non-int stays fenced.

A slice step on `str`, `bytes`, or `list` keeps every step-th element. Omitted bounds stay the ends of the value for a positive step, and flip like CPython for a negative step: missing start is the last index, missing stop walks through index 0, and out-of-range bounds clamp. Step 0 raises `ValueError`. Slice assignment stays fenced.

`match subject:` is an if chain over the stored subject, one `if` per case. A literal case is `subject == literal`, `None` is `subject is None`, an or pattern joins its alternatives with `or`, and a guard `if` joins with `and`, so a guard runs only when its pattern matched. A wildcard `_` always matches, and a capture name binds the subject like a declaration — a name the match never reached reads the type's zero where CPython raises `NameError`. A sequence, a class pattern, a mapping, an as pattern, a capture inside `|`, a non-literal value, and `True` / `False` singletons stay fenced.

`del` of one local that is not read again is dropped. The function returns what it returns. `del` of an attribute, a subscript, a slice, a tuple of names, or a name that is read after the `del` stays fenced.

A module `@staticmethod` is that function, and a call passes no instance. A module `@property`, a module `@classmethod`, stacked decorators, a decorator expression, and a user decorator stay fenced.

A mixed `and` / `or` in an `if`, `while`, `assert`, or list-comprehension filter is the truthiness of each side joined by the existing bool `and` / `or`. The right side runs only when the left does not decide it. Used as a value, `and` / `or` also lower: the result is the deciding operand, matching CPython.

`functools.reduce(operator.add, xs)` of a non-empty `list[int]` is a loop of `+`. An initializer on that call stays fenced, and so do `partial`, `wraps`, and `lru_cache`. An empty list raises `ValueError`.

`collections.OrderedDict()` with no arguments is an empty dict, so `len` of it is 0. A positional iterable, keyword arguments, `move_to_end`, `popitem`, and equality with a dict stay fenced.

`re.A` and `re.ASCII` are the same int constant CPython stores for that flag (256). `re.DEBUG` is the int constant 128. `re.DOTALL` is the int constant 16. Passing a flag to `re.compile` stays fenced.

`warnings.defaultaction` is the str constant `"default"`. The harness int is `len` times 1000 plus `ord` of the first character. `warn`, `filters`, and `simplefilter` stay fenced.

`inspect.AGEN_CLOSED` is the str constant `"AGEN_CLOSED"`. The harness int is that same `len` and `ord` form. `signature`, `getsource`, and `stack` stay fenced.

`threading.TIMEOUT_MAX` is the float const `9223372036.0`. It is not NaN, so `value != value` is false and the harness returns 1. `Thread`, `Lock`, and `Event` stay fenced.

`logging.BASIC_FORMAT` is the str constant `"%(levelname)s:%(name)s:%(message)s"`. `%` and parentheses are characters. `basicConfig`, `getLogger`, and handlers stay fenced.

`asyncio.ALL_COMPLETED` is the str constant `"ALL_COMPLETED"`. `run`, `gather`, `sleep`, and `async` / `await` stay fenced.

`subprocess.DEVNULL` is the int constant `-3`. `PIPE` is `-1` and `STDOUT` is `-2`. `run`, `Popen`, and `call` stay fenced.

Int constants whose harness probe is `return module.NAME`, and whose module was already lowered, are that CPython int, including the remaining `re` flags and the int constants on `base64`, `ctypes`, `datetime`, `errno`, `inspect`, `io`, `logging`, `os`, `pathlib`, `shutil`, `socket`, `ssl`, `stat`, `subprocess`, `sys`, `tempfile`, and `time`. None of those ints fell outside signed 64-bit. Latin-1 str constants on those modules use the same `len * 1000 + ord(first)` fold as `warnings.defaultaction`, including the `os` and `posixpath` path strings, the `sys` strings, the `inspect` state names, `asyncio.FIRST_COMPLETED`, `asyncio.FIRST_EXCEPTION`, `tempfile.template`, and the `ssl` PEM header and footer. Bool constants on those modules are the CPython bool, folded by `if` to 1 or 0: the `ssl` `HAS_*` flags, the `logging` log flags, `os.supports_bytes_environ`, `posixpath.supports_unicode_filenames`, `socket.has_ipv6`, and `sys.dont_write_bytecode`. `urllib.parse.scheme_chars` stays fenced because `urllib.parse` is not a lowered module row. `ssl` contexts, sockets, and logging handlers stay fenced.

A subclass `__init__` assigns the class's own fields. An inherited field that the subclass never assigns reads as `0` (or the zero value of that type), not `AttributeError`. `raise AppError("no")` stores a code and an optional `str`. It does not call `__init__`, and `except as` stays a fence. `@classmethod` and `@staticmethod` of the form `Class.method(...)` do not pass the class object. A body that reads `cls` is an unbound name. The first parameter of a classmethod is the unannotated name `cls`. `@property` is a getter. Calling it is a fence. A `@dataclass` has no base and no written `__init__`. `field(default_factory=...)` is a fence. A constant default on an annotation is an ordinary default. `enum.Enum` members are `int` constants: member equality matches CPython, and comparing a member to its integer or doing arithmetic matches `IntEnum`, not `Enum`. `*args` is a `list`, not a `tuple`. A `set` is unique and iterates in insertion order, not hash order. `list(set)` copies that insertion order, not CPython's hash order. Subscript and item assignment on a set are fences. `bool()` of a class is a fence. `list()` of a `str`, a `dict`, or no argument is a fence.

`unicodedata` is a direct Latin-1 table (Unicode 16.0.0 for those code points), not an island call per character. A normalization that would leave Latin-1 raises `ValueError`. `name` and `lookup` only know Latin-1 names. Type errors on these calls surface as `ValueError`. `importlib.resources.files` searches the compiler's `sys.path` for a directory; a zip or a missing package raises `ValueError` rather than `ModuleNotFoundError`. `shutil.copyfile` of the same path raises `RuntimeError` rather than `SameFileError`. Other `shutil` and `os` failures are `RuntimeError`. `hmac` rejects a non-bytes key or message and an unknown algorithm at compile time. `compare_digest` of mixed types is a fence. `atexit.register` takes a no-argument function and its return value is not a value.

Scripts that stay inside this list are in [examples.md](examples.md).

## Pinned libraries

`benchmarks/compat/top100/` holds pinned sdists of the pure-Python band (versions and hashes in `pins.json`). The scorer compiles each tree and skips `__main__.py`. A library counts only when one of its own functions is called and matches CPython. An empty module, a version constant, and a file written for the test do not count. `tzdata` 2026.5 and `trove-classifiers` 2026.9.21.13 compile and have no function, so they do not count. trove-classifiers lowers `sorted_classifiers`, `classifiers`, `deprecated_classifiers`, `all_classifiers`, and `__all__`. `certifi` counts: `certifi.core.where()` is the function `certifi.where` calls, and it matches CPython when the package parent is on the compiler's `sys.path`. colorama 0.4.6 and shellingham 1.5.4 are compiled the same way and are not in the frozen top-100 list. The other rows are the fence inside the definition. Those names are not exported.

The first fence of each real compile:

| Package | Version | First fence |
| --- | --- | --- |
| certifi | 2026.7.22 | counts: `called certifi.core.where([])` |
| colorama | 0.4.6 | `PS1001: parameter autoreset needs an annotation` (`init`) |
| filelock | 4.1.1 | `PS2002: this class base is not lowered` (`AcquireReturnProxy(Generic[_LockT])`) |
| h11 | 0.16.0 | `PS2002: decorators are not lowered` (inside `Connection`) |
| idna | 3.20 | `PS2002: this class base is not lowered` (`IDNABidiError(IDNAError)`; `IDNAError` does not lower) |
| iniconfig | 2.3.1 | `PS2002: collections.abc is not in the closed import table` |
| jmespath | 1.1.0 | `PS1001: parameter dict_cls needs an annotation` (`Options.__init__`) |
| mdurl | 0.1.2 | `PS2002: encode uses a parameter form that is not lowered` (keyword-only `keep_escaped`) |
| packaging | 26.3 | `PS1001: union types are not in the IR` (`ELFFile.interpreter` returns `str | None`) |
| pathspec | 1.1.1 | `PS2002: this class base is not lowered` (`GitIgnoreSpec(PathSpec[...])`) |
| platformdirs | 4.13.0 | `PS2002: this class base is not lowered` (`PlatformDirsABC(ABC)`) |
| pyjwt | 2.15.1 | `PS1001: this annotation is not in the lowered subset` (`PyJWK.__init__` parameter `JWKDict`) |
| python-dotenv | 1.2.4 | `PS1001: optional types are not in the IR` (`dotenv_values`) |
| pytz | 2026.5 | `PS2002: os.path is not in the closed import table` |
| shellingham | 1.5.4 | `PS2002: this class base is not lowered` (`ShellDetectionFailure(EnvironmentError)`) |
| trove-classifiers | 2026.9.21.13 | constants compiled (`sorted_classifiers`, `classifiers`, `deprecated_classifiers`, `all_classifiers`); no function was called |
| tzdata | 2026.5 | constants compiled; no function was called |
| zipp | 4.1.1 | `PS2002: zipfile is not in the closed import table` |
