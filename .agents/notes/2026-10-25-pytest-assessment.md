# pytest assessment: can pytest itself be compiled with pyscriptc?

Date: 2026-10-25. Worktree `~/.worktrees/pyscriptc-pytestassess`, branch `work/pytestassess`, assessment only, no source changes besides this note.

Setup: `uv venv /tmp/pytestassess/venv` on CPython 3.14.9 plus `uv pip install pytest` installed pytest 9.1.1 with iniconfig 2.3.1, packaging 26.3, pluggy 1.7.0 and pygments 2.21.0, and every `uv run pyscriptc` below ran from the worktree with `PYTHONPATH=/tmp/pytestassess/venv/lib/python3.14/site-packages` so the compiler and the binary could see site-packages.

Verdict: `_pytest` does not compile natively (78 files, 2017 fences, 46.6 percent of definitions blocked), but `pytest.main` already runs end to end under the island floor, because `pytest` is island-routed at import level and the entire test session, including assertion rewriting, executes inside the embedded CPython; what breaks first is the driver program, not pytest.

Lowering outcome per module on `assess .../site-packages/_pytest`: 78 files as 2 fully lowered (`__init__.py`, `_py/__init__.py`), 56 partly, 20 refused.

The 20 refused files with fence counts: `mark/__init__.py` 30, `faulthandler.py` 23, `assertion/_compare_any.py` 18, `deprecated.py` 18, `unraisableexception.py` 18, `warnings.py` 16, `threadexception.py` 14, `setuponly.py` 12, `assertion/_compare_set.py` 10, `assertion/_guards.py` 10, `setupplan.py` 8, `_code/__init__.py` 4, `freeze_support.py` 4, `terminalprogress.py` 4, `_version.py` 3, `assertion/_compare_mapping.py` 3, `assertion/_compare_sequence.py` 3, `_io/__init__.py` 1, `assertion/highlight.py` 1, `tracemalloc.py` 1.

Definition shares from the same run: 2287 definitions, 1146 native (50.1 percent), 5 island (0.2 percent), 1066 blocked (46.6 percent), and 100 imports as 32 native, 52 fenced, 16 island, so 16 percent import-level island and `requiresLibpython` true.

The densest partly-lowered files are `fixtures.py` 121 fences, `_py/path.py` 109, `pathlib.py` 93, `_code/code.py` 87, `config/__init__.py` 76, `pytester.py` 69, `python.py` 65, `main.py` 63 and `capture.py` 62.

`assess .../site-packages/pytest` (the shim package) reports 2 files as 1 fully (`__main__.py`) and 1 refused, `__init__.py` with 89 fences, 100 percent blocked and 100 percent import-level island, and it names one fence that cannot be routed to the island at all: `__init__.py:96 PS2002 __pytestPDB is not a local or parameter`.

Fences by root cause, ranked (2017 total): untyped import 583, then unsupported construct 1429, then reflection 5.

Inside untyped import, 450 fences are the intra-pytest export cascade (`_pytest.x.y.Z is not exported by this package`, always `name is missing`, meaning Z did not lower so the re-export does not exist) and 133 are 47 distinct modules outside the closed import table, led by `types` 23, `pluggy` 11, `traceback` 10, `pytest` 9, `pprint` 6, `platform` 5, `doctest` 5, `ast` 4, `importlib.util` 4, `collections.abc` 4, `importlib.machinery` 4 and `iniconfig` 4.

Inside unsupported construct, ranked by count: PS1106 annotation names an unlowered type 348, PS1001 missing or uninferred annotation 184, PS1104 `object`/`Any` annotation 124, PS2001 unlowered stdlib call or field access 116, PS1201 decorators 101, PS1103 generic alias or `Literal` or `NoReturn` annotation 86, PS2002 class base not lowered 65, PS1204 statement in a class body 62, PS1101 plus PS1102 unions 57, PS2003 keyword arguments 22, and 253 assorted PS2002 constructs (enum member 13, `class StashKey is not a value` 11, user decorators 10, `raise names a lowered exception` 9, name-resolution on module globals, starred expressions, attribute augmented assignment) plus 13 fences on small codes (PS1003, PS2004, PS1202, PS1004, PS4201, PS1105).

The 5 reflection fences are by-design refusals: `type()` at `junitxml.py:332` and `outcomes.py:23`, a metaclass at `nodes.py:119`, and `**kwargs` at `_py/path.py:640` and `_py/path.py:773`.

Plane map from `assess --json`: module top level native with 0 top-level fences, 2087 function definitions as 1144 native (54.8 percent) and 943 island (45.2 percent), with 10 definitions demoted because a caller is island (`_diff_text_block`, `_format_text_block_lines`, `_truncate_by_char_count`, `_get_prog_name`, `is_fs_root`, `_is_setup_py`, `_is_main_py`, `get_extended_length_path_str`, `backquote`, `_format_trimmed`).

Native share under the island floor: with `--fallback=cpython` the 1066 blocked definitions stop being blocked and become island functions, so the floor moves the tree from "50.1 percent native and 46.6 percent refused" to "54.8 percent native functions and 45.2 percent island functions", while the 16 island imports ( `__future__`, `collections.abc`, `colorama`, `grp`, `iniconfig`, `keyword`, `numpy`, `pdb`, `pickle`, `pkgutil`, `pluggy`, `pwd`, `py`, `pygments`, `pytest`, `time`) keep libpython linked.

What breaks first on the literal program `import pytest; raise SystemExit(pytest.main([test_file, "-q"]))`: without the tier it exits 3 with `PS2002 pytest is not in the closed import table` on line 1 and `PS2002 pytest is not a local or parameter` on line 3, and with `--fallback=cpython` the compiler crashes with an internal `KeyError: '_ps_imod_1'` at `src/pyscriptc/lower/_stdlib.py:849` reached from the boxed top-level probe (`src/pyscriptc/lower/__init__.py:1009` `_boxed_probe`), reproduced by `compile --fallback=cpython` as well and by the same shape written as `rc = pytest.main(...)` or `sys.exit(pytest.main(...))`; no issue in `.agents/` or `docs/` covers this KeyError.

Wrapping the same call in a lowered function (`def main() -> int: return pytest.main([...])` followed by `raise SystemExit(main())`) compiles and runs: the plane map reads `moduleTopLevel native`, `main native`, the 540 KB binary links `libpython3.14.so.1.0`, and pytest executes on the island.

Output comparison against CPython from the same working directory is byte-identical after normalizing only the duration numbers: the trivial file prints `2 passed` and exits 0 on both, and the rich file (failing assertion, class-based test, `@pytest.mark.parametrize`, `@pytest.fixture`, a sibling-module import and a `conftest.py` hook) prints `1 failed, 6 passed` and exits 1 on both, with assertion rewriting producing the same `assert (1 + 5) == 7` explanation.

So under the island nothing in pytest breaks first: decorators, class definitions, metaclasses, dynamic imports, `sys.modules`, the import hooks of `AssertionRewritingHook` and assertion rewriting all run under the host CPython island-side and are never lowered, which is exactly why the output matches; the first break is the module-top-level island call in the driver, and the second is the `import pytest` fence when the tier is off.

Two reporting inconsistencies seen while measuring: `run --report` prints `requiresLibpython: false` and `islandImports: []` for a binary whose `ldd` shows `libpython3.14.so.1.0` and whose IR contains `ps_island_import`, and `compile --debug-dir` fails the working wrapper with `PS0103 main: function returns int but a path can reach the end without a return` in irverify while the plain compile and the run both succeed.

If the goal were native pytest rather than a running pytest, the first wall is the export cascade (450 fences) plus the closed import table (133 fences), then decorators and class bases, then the annotation cascade of 348 PS1106 fences, and the only fences that are refused by design rather than merely unlowered are the 5 reflection ones.

The 5 fences whose removal would raise native share most, ranked by definitions freed counting direct sole-blocked definitions plus the annotation cascade each one causes (sole-blocked means the definition has no other fence): 1. PS1106 annotation names an unlowered type, 334 definitions blocked solely (348 fences), because 184 of those annotations name a class blocked by PS1201 and 89 name one blocked by a PS2002 class base, mapped to issue #412 (unions and generic aliases as erased annotations), issue #413 (collections.abc in annotations and TypeVar handling) and the typing items in the dispatch queue #453.

2. PS1201 decorators, 101 definitions blocked solely plus 184 cascaded annotation fences for about 285 total, with the decorators seen being `@final` 48, `@dataclasses.dataclass` 30, `@hookimpl` 28, `@fixture` 3, `@staticmethod` 3, `@dataclass` 1 and `@total_ordering` 1, mapped to no general issue (user decorators are "XXL, leave fenced" in `.agents/docs/roadmap.md` with no ticket) with the nearest tracked tickets being #444 and #452 for dataclass and field work and #231 to #234 for fastapi route decorators.

3. PS1001 missing or uninferred parameter annotation, 153 definitions blocked solely (174 involved) plus 21 cascaded through `TestReport`, mapped to issue #87 (the mypy checker bridge, E3a) and issue #258 (the pyrefly bridge lane).

4. PS2002 class base not lowered, 64 definitions blocked solely plus 89 cascaded annotation fences for about 153 total (`Item` 54, `Collector` 10, `nodes.Item`, `Function`, `Module`), mapped to issue #413 for the collections.abc and TypeVar shapes, while `abc`, multiple inheritance, `super()` and metaclasses are roadmap "leave fenced" items with no ticket and the metaclass-adjacent introspection surface sits on issues #86 and #88.

5. PS1104 `object` or `Any` annotation, 122 definitions blocked solely (123 involved), mapped to the same gap row as 1, that is issues #412, #413 and #453, which explicitly name `PS1104` as one of the four annotation and decorator codes dominating every measured tree.

Runners-up for the same ranking: PS1103 generic alias annotations at 73 sole definitions (issue #412) and PS1101 plus PS1102 unions at 43 sole definitions (issue #412).

Recommendation: focus on the annotation surface first (#412, #413 and the typing items of #453), because it is 558 fences and it feeds the 450-fence export cascade, then open a decorator ticket for `@final`, `@dataclasses.dataclass` and `@hookimpl`, which alone unlock about 285 definitions, and treat the top-level island-call KeyError as a separate small fix since it is the only thing that stops the working `pytest.main` idiom from being written naturally.
