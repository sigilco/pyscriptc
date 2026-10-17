"""Score gates-v1 against a frozen slice of the language, builtins, stdlib, and top 100.

    python -m benchmarks.compat.score --out benchmarks/compat/iterations/001.json

A name is lowered only when a probe compiles and matches CPython. A fence is
an explicit refusal (exit 3 or a PS diagnostic in the IR). A probe that runs
but disagrees is partial. Anything else is absent.

Percents are lowered / denominator. Partials are reported and do not count.
Denominators live in denominators.json and stay fixed across iterations.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DENOM = json.loads((HERE / "denominators.json").read_text(encoding="utf-8"))

# Bodies are indented as the function body. They must return int and stay
# inside the subset when the feature is lowered.
CALLS: dict[str, str] = {
    "math.sqrt": "return int(math.sqrt(9.0))",
    "math.fabs": "return int(math.fabs(-2.0))",
    "math.sin": "return int(math.sin(0.0))",
    "math.cos": "return int(math.cos(0.0))",
    "math.tan": "return int(math.tan(0.0))",
    "math.exp": "return int(math.exp(0.0))",
    "math.log": "return int(math.log(1.0))",
    "math.log2": "return int(math.log2(1.0))",
    "math.log10": "return int(math.log10(1.0))",
    "math.hypot": "return int(math.hypot(3.0, 4.0))",
    "math.pow": "return int(math.pow(2.0, 3.0))",
    "math.floor": "return math.floor(1.5)",
    "math.ceil": "return math.ceil(1.5)",
    "math.trunc": "return math.trunc(-1.5)",
    "math.isnan": "if math.isnan(math.nan):\n        return 1\n    return 0",
    "math.isinf": "if math.isinf(math.inf):\n        return 1\n    return 0",
    "re.escape": "return len(re.escape('a.b'))",
    "re.findall": "return len(re.findall('a', 'aba'))",
    "re.sub": "return len(re.sub('a', 'b', 'a'))",
    "re.split": "return len(re.split('b', 'abc'))",
    "re.compile": "return len(re.compile('ab').pattern)",
    "json.loads": "data: dict[str, int] = json.loads('{\"a\": 1}')\n    return data['a']",
    "json.dumps": "return len(json.dumps(1))",
    "urllib.parse.quote": "return len(urllib.parse.quote('a b'))",
    "urllib.parse.quote_plus": "return len(urllib.parse.quote_plus('a b'))",
    "urllib.parse.unquote": "return len(urllib.parse.unquote('a%20b'))",
    "urllib.parse.unquote_plus": "return len(urllib.parse.unquote_plus('a+b'))",
    "urllib.parse.urlencode": "return len(urllib.parse.urlencode({'a': 'b'}))",
    "urllib.parse.parse_qs": "return len(urllib.parse.parse_qs('a=b')['a'])",
    "urllib.parse.urlsplit": "return len(urllib.parse.urlsplit('http://h/a').scheme)",
    "base64.b64encode": "return len(base64.b64encode(b'ab'))",
    "base64.b64decode": "return len(base64.b64decode(b'YWI='))",
    "base64.standard_b64encode": "return len(base64.standard_b64encode(b'ab'))",
    "base64.standard_b64decode": "return len(base64.standard_b64decode(b'YWI='))",
    "base64.urlsafe_b64encode": "return len(base64.urlsafe_b64encode(b'ab'))",
    "base64.urlsafe_b64decode": "return len(base64.urlsafe_b64decode(b'YWI='))",
    "hashlib.md5": "return len(hashlib.md5(b'a').hexdigest())",
    "hashlib.sha1": "return len(hashlib.sha1(b'a').hexdigest())",
    "hashlib.sha256": "return len(hashlib.sha256(b'a').hexdigest())",
    "hashlib.sha384": "return len(hashlib.sha384(b'a').hexdigest())",
    "hashlib.sha512": "return len(hashlib.sha512(b'a').hexdigest())",
    "struct.pack": "return len(struct.pack('<H', 1))",
    "struct.unpack": "return struct.unpack('<H', struct.pack('<H', 7))[0]",
    "datetime.date": "return datetime.date(2020, 1, 2).day",
    "datetime.datetime": "return datetime.datetime(2020, 1, 2, 3, 4, 5).hour",
    "datetime.time": "return datetime.time(1, 2, 3).second",
    "datetime.timedelta": "return datetime.timedelta(days=2).days",
    "time.time": "if time.time() > 0.0:\n        return 1\n    return 0",
    "time.monotonic": "if time.monotonic() > 0.0:\n        return 1\n    return 0",
    "time.gmtime": "if time.gmtime(0).tm_year == 1970:\n        return 1\n    return 0",
    "time.localtime": "if time.localtime(0).tm_year > 1960:\n        return 1\n    return 0",
    "time.strftime": "return len(time.strftime('%Y', time.gmtime(0)))",
    "collections.deque": "box: collections.deque[int] = collections.deque()\n    box.append(3)\n    return box.pop()",
    "collections.OrderedDict": "return len(collections.OrderedDict())",
    "functools.reduce": "return functools.reduce(operator.add, [1, 2, 3])",
    "operator.add": "return operator.add(2, 3)",
    "operator.sub": "return operator.sub(5, 1)",
    "operator.mul": "return operator.mul(2, 4)",
    "operator.floordiv": "return operator.floordiv(7, 2)",
    "operator.neg": "return operator.neg(-4)",
    "operator.eq": "if operator.eq(1, 1):\n        return 1\n    return 0",
    "operator.concat": "return len(operator.concat('a', 'b'))",
    "os.getcwd": "if len(os.getcwd()) > 0:\n        return 1\n    return 0",
    "os.listdir": "if len(os.listdir('/tmp')) >= 0:\n        return 1\n    return 0",
    "os.getenv": "if len(os.getenv('PATH')) > 0:\n        return 1\n    return 0",
    "posixpath.basename": "return len(posixpath.basename('/a/b'))",
    "posixpath.dirname": "return len(posixpath.dirname('/a/b'))",
    "posixpath.join": "return len(posixpath.join('/a', 'b'))",
    "posixpath.split": "return len(posixpath.split('/a/b'))",
    "posixpath.splitext": "return len(posixpath.splitext('a.txt'))",
    "posixpath.normpath": "return len(posixpath.normpath('/a/../b'))",
    "posixpath.isabs": "if posixpath.isabs('/a'):\n        return 1\n    return 0",
    "posixpath.abspath": "if len(posixpath.abspath('a')) > 0:\n        return 1\n    return 0",
    "posixpath.exists": "if posixpath.exists('/tmp'):\n        return 1\n    return 0",
    "posixpath.isfile": "if posixpath.isfile('/tmp'):\n        return 1\n    return 0",
    "posixpath.isdir": "if posixpath.isdir('/tmp'):\n        return 1\n    return 0",
    "pathlib.Path": "return len(pathlib.Path('/tmp/a.txt').name)",
    "stat.S_ISREG": "if stat.S_ISREG(stat.S_IFREG):\n        return 1\n    return 0",
    "stat.S_ISDIR": "if stat.S_ISDIR(stat.S_IFDIR):\n        return 1\n    return 0",
    "stat.S_ISLNK": "if stat.S_ISLNK(stat.S_IFLNK):\n        return 1\n    return 0",
    "fnmatch.fnmatch": "if fnmatch.fnmatch('a.py', '*.py'):\n        return 1\n    return 0",
    "fnmatch.filter": "return len(fnmatch.filter(['a.py', 'b'], '*.py'))",
    "io.StringIO": "buf: io.StringIO = io.StringIO()\n    buf.write('ab')\n    return len(buf.getvalue())",
    "io.BytesIO": "buf: io.BytesIO = io.BytesIO()\n    buf.write(b'ab')\n    return len(buf.getvalue())",
    "bisect.bisect_left": "return bisect.bisect_left([1, 3, 5], 3)",
    "bisect.bisect_right": "return bisect.bisect_right([1, 3, 5], 3)",
    "ctypes.sizeof": "return ctypes.sizeof(ctypes.c_int)",
    "hmac.digest": "return len(hmac.digest(b'key', b'msg', 'sha256')) + hmac.digest(b'key', b'msg', 'sha256')[0]",
    "hmac.new": "return len(hmac.new(b'key', b'msg', 'sha256').digest())",
    "hmac.compare_digest": "if hmac.compare_digest(b'ab', b'ab'):\n        return 1\n    return 0",
    "unicodedata.category": "return len(unicodedata.category('A'))",
    "unicodedata.combining": "return unicodedata.combining('A')",
    "unicodedata.normalize": "return len(unicodedata.normalize('NFKC', '\\u00b2'))",
    "unicodedata.is_normalized": "if unicodedata.is_normalized('NFC', 'A'):\n        return 1\n    return 0",
    "unicodedata.bidirectional": "return len(unicodedata.bidirectional('A'))",
    "unicodedata.east_asian_width": "return len(unicodedata.east_asian_width('A'))",
    "unicodedata.mirrored": "if unicodedata.mirrored('('):\n        return 1\n    return 0",
    "unicodedata.decimal": "return unicodedata.decimal('4')",
    "unicodedata.digit": "return unicodedata.digit('4')",
    "unicodedata.numeric": "return int(unicodedata.numeric('4'))",
    "unicodedata.decomposition": "return len(unicodedata.decomposition('\\u00e9'))",
    "unicodedata.name": "return len(unicodedata.name('A'))",
    "unicodedata.lookup": "return len(unicodedata.lookup('LATIN CAPITAL LETTER A'))",
    "importlib.resources.files": (
        "text: str = importlib.resources.files('pyscriptc').joinpath('runtime').joinpath('ps_rt.h').read_text()\n"
        "    if text[0:2] == '/*':\n        return 1\n    return 0"
    ),
    "importlib.resources.as_file": (
        "shown: str = ''\n"
        "    with importlib.resources.as_file(\n"
        "        importlib.resources.files('pyscriptc').joinpath('runtime').joinpath('ps_rt.h')\n"
        "    ) as handle:\n"
        "        shown = str(handle)\n"
        "    if shown[0:1] == '/':\n        return 1\n    return 0"
    ),
    "tempfile.gettempdir": "return len(tempfile.gettempdir())",
    "tempfile.gettempprefix": "return len(tempfile.gettempprefix())",
    "tempfile.mkdtemp": "made: str = tempfile.mkdtemp()\nif len(made) > 0:\n        return 1\n    return 0",
    "shutil.copyfile": (
        "src: str = '/tmp/ps-compat-copy-src.txt'\n"
        "    dst: str = '/tmp/ps-compat-copy-dst.txt'\n"
        "    with open(src, 'w') as handle:\n"
        "        handle.write('hi')\n"
        "    copied: str = shutil.copyfile(src, dst)\n"
        "    with open(dst, 'r') as handle:\n"
        "        body: str = handle.read()\n"
        "    if copied == dst and body == 'hi':\n        return 1\n    return 0"
    ),
    "shutil.rmtree": (
        "tree: str = '/tmp/ps-compat-rmtree'\n"
        "    if posixpath.isdir(tree):\n"
        "        shutil.rmtree(tree)\n"
        "    os.makedirs(tree + '/sub')\n"
        "    with open(tree + '/a.txt', 'w') as handle:\n"
        "        handle.write('x')\n"
        "    shutil.rmtree(tree)\n"
        "    if posixpath.exists(tree):\n        return 0\n    return 1"
    ),
    "atexit.register": "atexit.register(p_atexit_cleanup)\n    return 1",
}

LANGUAGE: list[tuple[str, str, str]] = [
    ("lex-integer", "2.4.4 Integer literals", "return 10 + 2"),
    ("lex-float", "2.4.5 Floating-point literals", "return int(1.5 * 2.0)"),
    ("lex-string", "2.4.1 String literals", "return len('ab' + 'c')"),
    ("lex-bytes", "2.4.2 Bytes literals", "return len(b'ab')"),
    ("lex-latin1", "2.4.1 Latin-1 literal", "return len('\\u00e9')"),
    ("lex-non-latin1", "2.4.1 Non-Latin-1 literal", "return len('\\u0100')"),
    ("lex-imaginary", "2.4.6 Imaginary literals", "return 1"),
    ("expr-floor", "6.7 Floor division", "return (-3) // 2"),
    ("expr-pow", "6.5 Power", "return 2 ** 3"),
    ("expr-bit", "6.8 Bitwise", "return 1 & 1"),
    ("expr-and", "6.11 Boolean and, same type", "if 'a' and 'b':\n        return 1\n    return 0"),
    ("expr-or-mixed", "6.11 Boolean or across types", "if 1 or 'a':\n        return 1\n    return 0"),
    ("expr-compare-chain", "6.10 Chained comparisons", "if 1 < 2 < 3:\n        return 1\n    return 0"),
    ("expr-ternary", "6.12 Conditional expression", "return 1 if True else 0"),
    ("expr-lambda", "6.13 Lambdas", "return (lambda x: x)(1)"),
    ("expr-fstring", "2.4.1 Plain f-string", "return len(f'{1}')"),
    ("expr-fstring-spec", "2.4.1 f-string format spec", "return len(f'{1:02d}')"),
    ("expr-percent", "6.7 Percent formatting", "return len('%s-%d' % ('a', 2))"),
    ("expr-slice", "6.3.3 Slice step 1", "return len('abcd'[1:])"),
    ("expr-slice-step", "6.3.3 Slice step other than 1", "return len('abcd'[::2])"),
    (
        "expr-list-comp",
        "6.2.8 List comprehension",
        "xs: list[int] = [i + 1 for i in range(3)]\n    return xs[2]",
    ),
    (
        "expr-list-comp-if",
        "6.2.8 List comprehension filter",
        "xs: list[int] = [i for i in range(4) if i > 1]\n    return xs[0]",
    ),
    ("expr-set", "6.2.6 Set display", "return len({1, 2})"),
    ("expr-tuple", "6.14 Tuple of two fields", "pair: tuple[int, int] = (1, 2)\n    return pair[1]"),
    ("expr-star", "6.3.4 Starred call", "return min(*[1, 2])"),
    ("stmt-assign", "7.2 Assignment", "x: int = 1\n    x = x + 2\n    return x"),
    ("stmt-aug", "7.2 Augmented assignment", "x: int = 1\n    x += 2\n    return x"),
    ("stmt-ann", "7.2 Annotated assignment", "x: int = 4\n    return x"),
    ("stmt-assert", "7.3 assert", "assert 1 == 1\n    return 1"),
    ("stmt-pass", "7.4 pass", "pass\n    return 1"),
    ("stmt-del", "7.5 del", "x: int = 1\n    del x\n    return 1"),
    (
        "stmt-raise",
        "7.8 raise",
        "try:\n        raise ValueError('x')\n    except ValueError:\n        return 1\n    return 0",
    ),
    ("stmt-break", "7.9 break", "i: int = 0\n    while i < 3:\n        break\n    return i"),
    (
        "stmt-continue",
        "7.10 continue",
        "i: int = 0\n    while i < 1:\n        i = i + 1\n        continue\n    return i",
    ),
    ("stmt-import", "7.11 import", "return math.floor(1.5)"),
    ("stmt-global", "7.12 global", "global missing_name\n    return 1"),
    ("stmt-for", "8.3 for", "total: int = 0\n    for i in range(4):\n        total = total + i\n    return total"),
    ("stmt-empty-for", "8.3 empty for target", "for i in range(0):\n        return 1\n    return i"),
    ("stmt-while", "8.2 while", "i: int = 0\n    while i < 3:\n        i = i + 1\n    return i"),
    ("stmt-if", "8.1 if", "if 1 < 2:\n        return 1\n    return 0"),
    ("stmt-try", "8.4 try", "try:\n        x: int = 1\n    except ValueError:\n        return 0\n    return x"),
    (
        "stmt-with-open",
        "8.5 with open",
        "with open('/tmp/ps-compat-with.txt', 'w') as handle:\n        handle.write('ab')\n    return 1",
    ),
    ("stmt-match", "8.6 match", "x: int = 1\n    match x:\n        case 1:\n            return 1\n    return 0"),
    ("stmt-func", "8.7 Function definition", "return 1"),
    ("stmt-decorator", "8.7 Decorator", "return 1"),
    ("stmt-class", "8.8 Class", "return Point(1, 2).x"),
    ("stmt-inherit", "8.8 Single inheritance", "return 1"),
    ("stmt-async", "8.9 async def", "return 1"),
    ("stmt-yield", "7.7 yield", "return 1"),
    ("data-list", "3.2 List", "xs: list[int] = [1, 2]\n    xs[0] = 4\n    return xs[0] + len(xs)"),
    ("data-dict", "3.2 Dict", "xs: dict[str, int] = {'a': 2}\n    return xs['a']"),
    ("data-bool", "3.2 Boolean", "if True:\n        return 1\n    return 0"),
    ("data-none", "3.2 None", "return 1"),
    ("builtin-len", "3.3.1 len", "return len('abcd')"),
    ("builtin-abs", "Builtins abs", "return abs(-3)"),
    (
        "model-nan-in",
        "3.2 List membership of nan",
        "xs: list[float] = [math.nan]\nif math.nan in xs:\n        return 1\n    return 0",
    ),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score subset compatibility and write JSON.")
    parser.add_argument("--out", help="write the snapshot JSON here")
    parser.add_argument("--self-check", action="store_true", help="check frozen denominators, do not compile")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    if args.self_check:
        problems = self_check()
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 1
        print(
            f"denominators ok builtins={len(DENOM['builtins'])} stdlib={len(DENOM['stdlib_modules'])} "
            f"surface={len(DENOM['stdlib_surface'])} top100={len(DENOM['top100'])}"
        )
        return 0
    report = score(args.workers)
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _print_summary(report)
    return 0


def self_check() -> list[str]:
    problems: list[str] = []
    live_builtins = sorted(n for n in vars(__import__("builtins")) if n.isidentifier() and not n.startswith("_"))
    if live_builtins != DENOM["builtins"]:
        problems.append("builtin denominator drifted from this interpreter")
    if len(DENOM["stdlib_modules"]) != 194:
        problems.append("stdlib module denominator is not 194")
    if len(DENOM["top100"]) != 100:
        problems.append("top 100 denominator is not 100")
    if DENOM["cpython"].split(".")[:2] != ["3", "14"]:
        problems.append("denominator was not frozen on CPython 3.14")
    return problems


def score(workers: int) -> dict[str, Any]:
    language = _score_language()
    builtins = _score_builtins()
    surface_rows, module_import = _score_stdlib(workers)
    modules = _module_rows(module_import, surface_rows)
    top = _score_top100()
    scores = {
        "language": _tally(language),
        "builtins": _tally(builtins),
        "stdlib_modules": _tally(modules),
        "stdlib_surface": _tally(surface_rows),
        "top100": _tally(top, compat_status={"lowered", "subset"}),
    }
    return {
        "iteration_note": "lowered / frozen denominator. partial does not count toward the percent.",
        "cpython": sys.version.split()[0],
        "denominators": {
            "language": scores["language"]["total"],
            "builtins": scores["builtins"]["total"],
            "stdlib_modules": scores["stdlib_modules"]["total"],
            "stdlib_surface": scores["stdlib_surface"]["total"],
            "top100": scores["top100"]["total"],
            "source": "benchmarks/compat/denominators.json",
            "top100_last_update": DENOM["top100_last_update"],
        },
        "scores": scores,
        "language": language,
        "builtins": builtins,
        "stdlib_modules": modules,
        "stdlib_surface": surface_rows,
        "top100": top,
        "band": _score_band(),
        "top100_pins": _pins(),
        "next": (
            "certifi.core.where counts. dict.keys() is the key list in insertion order. "
            "trove-classifiers is partial: sorted_classifiers, classifiers, deprecated_classifiers, "
            "and all_classifiers compiled, and no function was called. "
            "jmespath stays fenced: Options.__init__ parameter dict_cls needs an annotation. "
            'Next: packaging\'s f-string f"2014 {__author__}" is the str constant a module assignment already stores. '
            "Folding it moves packaging's first fence and does not make packaging count; the next fence is IO. "
            "Leave dict.values(), dict.items(), Optional, keyword-only parameters, abc, Generic, "
            "and unannotated parameters fenced. "
            "Leave http.client, user decorators, functools.partial, super(), and upstream requests fenced. "
            "The desugar pass, one intrinsic table, match on ast.stmt/ast.expr, and IR nodes as dataclasses stay open."
        ),
    }


def _score_language() -> list[dict[str, Any]]:
    header = (
        "import math\n\n"
        "class Point:\n"
        "    x: int\n"
        "    y: int\n"
        "    def __init__(self, x: int, y: int) -> None:\n"
        "        self.x = x\n"
        "        self.y = y\n"
    )
    probes = []
    chunks = [header]
    for ident, section, body in LANGUAGE:
        fid = "lang_" + ident.replace("-", "_")
        src = _function(fid, body)
        if ident == "stmt-decorator":
            src = "@staticmethod\ndef lang_stmt_decorator() -> int:\n    return 1\n"
        elif ident == "stmt-inherit":
            src = (
                "class Child(Point):\n"
                "    z: int\n"
                "    def __init__(self, z: int) -> None:\n"
                "        self.z = z\n"
                "\n"
                "def lang_stmt_inherit() -> int:\n"
                "    return Child(1).z\n"
            )
        elif ident == "stmt-async":
            src = "async def lang_stmt_async() -> int:\n    return 1\n"
        elif ident == "stmt-yield":
            src = "def lang_stmt_yield() -> int:\n    yield 1\n    return 1\n"
        elif ident == "lex-imaginary":
            src = "def lang_lex_imaginary() -> int:\n    return int(1j.real)\n"
        if ident == "data-none":
            src = (
                "def lang_note() -> None:\n"
                "    return None\n"
                "\n"
                "def lang_data_none() -> int:\n"
                "    lang_note()\n"
                "    return 1\n"
            )
        probes.append({"id": ident, "section": section, "fid": fid, "source": src, "kind": "call"})
        chunks.append(src)
    return _finish_group("language", "\n".join(chunks), probes, header)


def _score_builtins() -> list[dict[str, Any]]:
    probes = []
    chunks: list[str] = [
        "class Box:\n    n: int\n    def __init__(self, n: int) -> None:\n        self.n = n\n",
        "def note() -> None:\n    return None\n",
    ]
    specials = {
        "True": ("value", "if True:\n        return 1\n    return 0"),
        "False": ("value", "if False:\n        return 0\n    return 1"),
        "None": ("value", "note()\nreturn 1"),
        "abs": ("call", "return abs(-3)"),
        "len": ("call", "return len('ab')"),
        "min": ("call", "return min(2, 5)"),
        "max": ("call", "return max(2, 5)"),
        "sum": ("call", "xs: list[int] = [1, 2, 3]\n    return sum(xs)"),
        "sorted": ("call", "xs: list[int] = [3, 1]\n    ys: list[int] = sorted(xs)\n    return ys[0]"),
        "ord": ("call", "return ord('A')"),
        "chr": ("call", "if chr(65) == 'A':\n        return 1\n    return 0"),
        "round": ("call", "return round(1.6)"),
        "isinstance": ("call", "if isinstance(1, int):\n        return 1\n    return 0"),
        "getattr": ("call", "return getattr(Box(4), 'n')"),
        "hasattr": ("call", "if hasattr(Box(1), 'n'):\n        return 1\n    return 0"),
        "int": ("call", "return int(2.9)"),
        "float": ("call", "return int(float(2))"),
        "str": ("call", "return len(str(12))"),
        "bool": ("call", "n: int = 0\n    if bool(1):\n        n = 1\n    if bool(0):\n        return 0\n    return n"),
        "list": ("call", "xs: list[int] = list([1, 2])\n    return xs[0]"),
        "all": ("call", "xs: list[int] = [1, 0]\n    if all(xs):\n        return 0\n    return 1"),
        "any": ("call", "xs: list[int] = [0, 2]\n    if any(xs):\n        return 1\n    return 0"),
        "enumerate": (
            "call",
            "xs: list[int] = [10, 20]\n"
            "    last: int = 0\n"
            "    for pair in enumerate(xs):\n"
            "        last = pair[0]\n"
            "    return last",
        ),
        "zip": (
            "call",
            "xs: list[int] = [3, 4]\n"
            "    ys: list[int] = [5]\n"
            "    total: int = 0\n"
            "    for pair in zip(xs, ys):\n"
            "        total = total + pair[0] + pair[1]\n"
            "    return total",
        ),
        "dict": ("call", "xs: dict[str, int] = {'a': 1}\n    return xs['a']"),
        "tuple": ("call", "pair: tuple[int, int] = (1, 2)\n    return pair[0]"),
        "range": ("call", "total: int = 0\n    for i in range(3):\n        total = total + i\n    return total"),
        "open": (
            "call",
            "with open('/tmp/ps-compat-open.txt', 'w') as handle:\n        handle.write('z')\n    return 1",
        ),
    }
    for name in DENOM["builtins"]:
        fid = "b_" + name
        kind, body = specials.get(name, ("mention", f"{name}\n    return 1"))
        src = _function(fid, body)
        probes.append({"id": name, "section": "builtin", "fid": fid, "source": src, "kind": kind})
        chunks.append(src)
    header = chunks[0] + chunks[1]
    return _finish_group("builtins", "\n".join(chunks), probes, header)


def _score_stdlib(workers: int) -> tuple[list[dict[str, Any]], dict[str, str]]:
    grouped: dict[str, list[str]] = {}
    for qual in DENOM["stdlib_surface"]:
        module, _name = qual.rsplit(".", 1)
        grouped.setdefault(module, []).append(qual)
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(lambda item: _score_surface_module(*item), grouped.items()):
            rows.extend(part)
    order = {qual: index for index, qual in enumerate(DENOM["stdlib_surface"])}
    rows.sort(key=lambda row: order[row["id"]])
    imports = _import_status(workers)
    return rows, imports


def _score_surface_module(module: str, quals: list[str]) -> list[dict[str, Any]]:
    try:
        mod = importlib.import_module(module)
    except Exception as exc:
        return [{"id": qual, "section": module, "status": "absent", "detail": type(exc).__name__} for qual in quals]
    header = f"import {module}\n"
    if module == "functools":
        header += "import operator\n"
    if module == "shutil":
        header += "import os\nimport posixpath\n"
    probes = []
    chunks = [header]
    for qual in quals:
        name = qual.rsplit(".", 1)[1]
        obj = getattr(mod, name, None)
        fid = "p_" + qual.replace(".", "_")
        kind, body = _surface_body(qual, obj)
        if qual == "atexit.register":
            src = (
                "def p_atexit_cleanup() -> None:\n"
                "    return\n"
                "\n"
                "def p_atexit_register() -> int:\n"
                "    atexit.register(p_atexit_cleanup)\n"
                "    return 1\n"
            )
        else:
            src = _function(fid, body)
        probes.append({"id": qual, "section": module, "fid": fid, "source": src, "kind": kind})
        chunks.append(src)
    return _finish_group(module, "\n".join(chunks), probes, header)


def _surface_body(qual: str, obj: Any) -> tuple[str, str]:
    if qual == "sys.exit":
        # libc exit would kill the scorer. Compiling the call is the check.
        return "emit", "sys.exit(0)\n    return 0"
    if qual in CALLS:
        return "call", CALLS[qual]
    if isinstance(obj, bool):
        return "value", f"if {qual}:\n        return 1\n    return 0"
    if isinstance(obj, int) and not isinstance(obj, bool) and abs(obj) < 2**62:
        return "value", f"return {qual}"
    if isinstance(obj, float):
        return "value", f"value: float = {qual}\n    if value != value:\n        return 2\n    return 1"
    if isinstance(obj, str) and len(obj) <= 500 and all(ord(ch) < 256 for ch in obj):
        return "value", (
            f"text: str = {qual}\n    if len(text) == 0:\n        return 0\n    return len(text) * 1000 + ord(text[0])"
        )
    return "mention", f"{qual}\n    return 1"


def _import_status(workers: int) -> dict[str, str]:
    def one(name: str) -> tuple[str, str]:
        source = f"import {name}\n\ndef f() -> int:\n    return 1\n"
        ir, code = _compile_ir(name, source)
        if code == 3 or (ir and ir.get("fences")):
            return name, "fenced"
        if code == 0 and ir and ir.get("functions"):
            return name, "imported"
        return name, "absent"

    found: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for name, status in pool.map(one, DENOM["stdlib_modules"]):
            found[name] = status
    return found


def _module_rows(imported: dict[str, str], surface: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lowered_roots: set[str] = set()
    for row in surface:
        if row["status"] == "lowered":
            lowered_roots.add(row["id"].split(".", 1)[0])
    rows = []
    for name in DENOM["stdlib_modules"]:
        state = imported.get(name, "absent")
        if state == "fenced":
            status = "fenced"
        elif state == "absent":
            status = "absent"
        elif name in lowered_roots:
            status = "lowered"
        else:
            status = "partial"
        rows.append({"id": name, "section": "stdlib", "status": status})
    return rows


TOP_SRC = HERE / "top100" / "src"
PINS_PATH = HERE / "top100" / "pins.json"


def _pins() -> dict[str, Any]:
    if not PINS_PATH.is_file():
        return {}
    data = json.loads(PINS_PATH.read_text(encoding="utf-8"))
    packages = data.get("packages", data)
    return packages if isinstance(packages, dict) else {}


def _score_top100() -> list[dict[str, Any]]:
    pins = _pins()
    native = DENOM["top100_native"]
    rows = []
    for item in DENOM["top100"]:
        name = item["project"]
        if name in native:
            rows.append(
                {
                    "id": name,
                    "section": "top100",
                    "status": "native-only",
                    "detail": native[name],
                    "rank": item["rank"],
                }
            )
            continue
        pin = pins.get(name)
        if not pin:
            rows.append(
                {
                    "id": name,
                    "section": "top100",
                    "status": "unattempted",
                    "detail": "no pinned source snapshot",
                    "rank": item["rank"],
                }
            )
            continue
        row = classify_tree(name, TOP_SRC / str(pin["import_name"]))
        row["section"] = "top100"
        row["rank"] = item["rank"]
        row["version"] = pin.get("version")
        rows.append(row)
    return rows


def _score_band() -> list[dict[str, Any]]:
    """Pinned packages that are not in the frozen top-100 denominator."""

    known = {item["project"] for item in DENOM["top100"]}
    rows = []
    for name, pin in _pins().items():
        if name in known:
            continue
        row = classify_tree(name, TOP_SRC / str(pin["import_name"]))
        row["section"] = "band"
        row["version"] = pin.get("version")
        rows.append(row)
    return rows


def classify_tree(name: str, tree: Path) -> dict[str, Any]:
    """Compile one real package tree. A subset requires a called function."""

    row: dict[str, Any] = {
        "id": name,
        "section": "top100",
        "status": "unattempted",
        "detail": "no pinned source snapshot",
    }
    if not tree.is_dir():
        return row
    ir, code, err = _compile_path(name, tree)
    if code == 0 and ir is not None and not ir.get("fences"):
        functions = list(ir.get("functions") or [])
        if not functions:
            return {**row, "status": "partial", "detail": "constants compiled; no function was called"}
        called = _call_real_function(name, tree, functions)
        if called is None:
            return {**row, "status": "partial", "detail": "compiled; no function with a closed signature was called"}
        status, detail = called
        return {**row, "status": status, "detail": detail}
    if ir and ir.get("fences"):
        fence = ir["fences"][0]
        return {**row, "status": "fenced", "detail": f"{fence['code']}: {fence['message']}"}
    if code == 3:
        return {**row, "status": "fenced", "detail": _first_ps(err) or "compile refused"}
    text = (err or f"compile exit {code}").strip().splitlines()
    return {**row, "status": "error", "detail": (text[-1] if text else f"compile exit {code}")[:300]}


def _compile_path(label: str, path: Path) -> tuple[dict[str, Any] | None, int, str]:
    folder = Path("/tmp/pyscriptc-compat")
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in label)[:80]
    ir_path = folder / f"{safe}.ir.json"
    if ir_path.exists():
        ir_path.unlink()
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(path), "--emit", "ir", "--skip-check", "-o", str(ir_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    err = proc.stderr or proc.stdout or ""
    if not ir_path.exists():
        return None, proc.returncode, err
    try:
        return json.loads(ir_path.read_text(encoding="utf-8")), proc.returncode, err
    except json.JSONDecodeError:
        return None, proc.returncode, err


def _first_ps(text: str) -> str:
    for line in text.splitlines():
        index = line.find("PS")
        if index >= 0 and line[index : index + 2] == "PS":
            return line[index:].strip()
    return ""


def _call_real_function(name: str, tree: Path, functions: list[dict[str, Any]]) -> tuple[str, str] | None:
    chosen: tuple[dict[str, Any], list[Any]] | None = None
    for fn in functions:
        args = _closed_args(fn)
        if args is None:
            continue
        if chosen is None or len(args) < len(chosen[1]):
            chosen = (fn, args)
    if chosen is None:
        return None
    fn, args = chosen
    native = _run_path(name + "-call", tree, fn["name"], args)
    cpython = _cpython_path(tree, fn["name"], args)
    detail = f"called {fn['name']}({args!r})"
    if native[0] == "ok" and cpython[0] == "ok" and native[1] == cpython[1]:
        return "subset", detail
    if native[0] == "ok" and cpython[0] == "ok":
        return "partial", f"{detail} compiled {native[1]!r} CPython {cpython[1]!r}"
    return "partial", f"{detail} {native[0]}: {native[1][:180]}"


def _closed_args(fn: dict[str, Any]) -> list[Any] | None:
    if fn["returnType"]["kind"] not in {"int", "str", "bool"}:
        return None
    args: list[Any] = []
    for param in fn["params"]:
        if param.get("vararg"):
            return None
        kind = param["type"]["kind"]
        if kind == "int":
            args.append(1)
        elif kind == "str":
            args.append("a")
        elif kind == "bool":
            args.append(True)
        else:
            return None
    return args


def _tree_env(tree: Path) -> dict[str, str]:
    """The package parent is on sys.path, the same place CPython imports it from."""

    env = os.environ.copy()
    parent = str(tree.resolve().parent)
    prior = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = parent if not prior else parent + os.pathsep + prior
    return env


def _run_path(label: str, path: Path, call: str, args: list[Any]) -> tuple[str, str]:
    folder = Path("/tmp/pyscriptc-compat")
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in label)[:80]
    binary = folder / safe
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--run",
            "--skip-check",
            "--call",
            call,
            "--args",
            repr(args),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        env=_tree_env(path) if path.is_dir() else None,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 3:
        return "fence", _first_ps(proc.stderr) or proc.stderr.strip()
    if proc.returncode != 0:
        return "error", (proc.stderr or proc.stdout).strip()
    return "ok", proc.stdout.strip()


def _cpython_path(tree: Path, qual: str, args: list[Any]) -> tuple[str, str]:
    module, _, func = qual.rpartition(".")
    if not module or not func:
        return "error", "function name is not dotted"
    source = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {str(tree.parent)!r})\n"
        f"mod = importlib.import_module({module!r})\n"
        f"value = getattr(mod, {func!r})(*{args!r})\n"
        "print(value)\n"
    )
    proc = subprocess.run([sys.executable, "-c", source], cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return "error", (proc.stderr or proc.stdout).strip().splitlines()[-1][:180]
    return "ok", proc.stdout.strip()


def _finish_group(label: str, source: str, probes: list[dict[str, Any]], header: str = "") -> list[dict[str, Any]]:
    ir, code = _compile_ir(label, source)
    if ir is None:
        return [{**probe, "status": "absent", "detail": f"compile exit {code}"} for probe in probes]
    lowered = {fn["name"] for fn in ir.get("functions", [])}
    rows = []
    runnable = []
    for probe in probes:
        fid = probe["fid"]
        if fid not in lowered:
            rows.append({**_public(probe), "status": "fenced"})
            continue
        if probe["kind"] == "emit":
            rows.append({**_public(probe), "status": "lowered", "detail": "compiles; not executed"})
            continue
        if probe["kind"] == "mention":
            rows.append({**_public(probe), "status": "partial", "detail": "name is accepted and not value-checked"})
            continue
        ran, value = _cpython_call(header + probe["source"], fid)
        if ran == "raise":
            native = _run_native(label + "-" + fid, header + probe["source"], fid)
            status = "partial" if native[0] == "ok" else "fenced"
            rows.append({**_public(probe), "status": status, "detail": value})
            continue
        probe["cpython"] = value
        runnable.append(probe)
        rows.append({**_public(probe), "status": "pending"})
    if runnable:
        agreed = _bundle(label, runnable, header)
        for row in rows:
            if row["status"] != "pending":
                continue
            row["status"] = "lowered" if agreed.get(row["id"], False) else "partial"
    return rows


def _bundle(label: str, probes: list[dict[str, Any]], header: str) -> dict[str, bool]:
    if len(probes) == 1:
        return {probes[0]["id"]: _one_matches(label, probes[0], header)}
    source, fid = _bundle_source(probes, header)
    native = _run_native(label + "-bundle", source, fid)
    expect = _cpython_call(source, fid)
    if native[0] == "ok" and expect[0] == "ok" and native[1] == expect[1]:
        return {probe["id"]: True for probe in probes}
    mid = len(probes) // 2
    left = _bundle(label + "a", probes[:mid], header)
    right = _bundle(label + "b", probes[mid:], header)
    return {**left, **right}


def _one_matches(label: str, probe: dict[str, Any], header: str) -> bool:
    native = _run_native(label + "-" + probe["fid"], header + probe["source"], probe["fid"])
    return native[0] == "ok" and native[1] == probe.get("cpython")


def _bundle_source(probes: list[dict[str, Any]], header: str) -> tuple[str, str]:
    parts = [header, *[probe["source"] for probe in probes]]
    lines = ["def bundle() -> int:", "    total: int = 0"]
    for probe in probes:
        lines.append(f"    total = total + {probe['fid']}()")
    lines.append("    return total")
    return "\n".join([*parts, *lines, ""]), "bundle"


def _function(fid: str, body: str) -> str:
    """Indent a body. Continuation lines may already include one function-level indent."""
    lines: list[str] = []
    for line in body.strip("\n").split("\n"):
        if line.startswith("    "):
            line = line[4:]
        lines.append(("    " + line) if line else "")
    return f"def {fid}() -> int:\n" + "\n".join(lines) + "\n"


def _compile_ir(label: str, source: str) -> tuple[dict[str, Any] | None, int]:
    folder = Path("/tmp/pyscriptc-compat")
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in label)[:80]
    path = folder / f"{safe}.py"
    ir_path = folder / f"{safe}.ir.json"
    if ir_path.exists():
        ir_path.unlink()
    path.write_text(source, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--emit",
            "ir",
            "--skip-check",
            "-o",
            str(ir_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in {0, 3} or not ir_path.exists():
        return None, proc.returncode
    try:
        return json.loads(ir_path.read_text(encoding="utf-8")), proc.returncode
    except json.JSONDecodeError:
        return None, proc.returncode


def _run_native(label: str, source: str, fid: str) -> tuple[str, str]:
    folder = Path("/tmp/pyscriptc-compat")
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() else "_" for ch in label)[:80]
    path = folder / f"{safe}.py"
    binary = folder / safe
    path.write_text(source, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyscriptc",
            "compile",
            str(path),
            "--run",
            "--skip-check",
            "--call",
            fid,
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 3:
        return "fence", proc.stderr.strip()
    if proc.returncode != 0:
        return "error", (proc.stderr or proc.stdout).strip()
    return "ok", proc.stdout.strip()


def _cpython_call(source: str, fid: str) -> tuple[str, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return "raise", exc.__class__.__name__
    namespace: dict[str, Any] = {}
    try:
        exec(compile(tree, "<compat>", "exec"), namespace)
        value = namespace[fid]()
    except Exception as exc:
        return "raise", exc.__class__.__name__
    return "ok", str(value)


def _public(probe: dict[str, Any]) -> dict[str, Any]:
    return {"id": probe["id"], "section": probe["section"]}


def _tally(rows: list[dict[str, Any]], compat_status: set[str] | None = None) -> dict[str, Any]:
    compat = compat_status or {"lowered"}
    counts = {"lowered": 0, "partial": 0, "fenced": 0, "absent": 0, "native-only": 0, "subset": 0}
    for row in rows:
        status = row["status"]
        if status not in counts:
            counts[status] = 0
        counts[status] += 1
    total = len(rows)
    lowered = sum(counts.get(name, 0) for name in compat)
    percent = 0.0 if total == 0 else 100.0 * lowered / total
    return {"total": total, "percent": percent, **counts, "lowered_or_subset": lowered}


def _print_summary(report: dict[str, Any]) -> None:
    print(f"pyscriptc compat  CPython {report['cpython']}")
    for key in ("language", "builtins", "stdlib_modules", "stdlib_surface", "top100"):
        score = report["scores"][key]
        print(
            f"  {key:16} {score['percent']:6.2f}%  "
            f"lowered {score.get('lowered', 0)}/{score['total']}  "
            f"partial {score.get('partial', 0)}  fenced {score.get('fenced', 0)}  "
            f"absent {score.get('absent', 0)}  native {score.get('native-only', 0)}  "
            f"subset {score.get('subset', 0)}  unattempted {score.get('unattempted', 0)}"
        )
    print(report["next"])


if __name__ == "__main__":
    sys.exit(main())
