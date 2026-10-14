# Adoption coverage

pyscriptc is a compiler, in the same shape as [scriptc](https://github.com/vercel-labs/scriptc). A file either lowers to LLVM or it stops at a fence and stays on CPython. It is not a library you inject with `sitecustomize.py`. Suites written for that injection model (Django, FastAPI, SQLAlchemy, Celery, the OpenTelemetry contrib matrix) do not become pyscriptc benchmarks by putting the compiler on `PYTHONPATH`.

This is not 100% CPython 3.14. The IR stamp is `gates-v1`. Parsing uses CPython 3.14's `ast`, and mypy is a preflight. The behavior of a lowered call matches CPython on that call. The language around it does not.

## What lowers

29 of the 31 queued modules have a timed kernel in `benchmarks/runtime/workloads.json`. The binaries that do not call the island do not link libpython. The measured geomeans on the reference host:

| Group | Time | RSS | What the binary contains |
| --- | --- | --- | --- |
| kernel | 20.6x CPython | 6.5x CPython | the original four loops, stored in `reference.json` |
| stdlib | 12.6x CPython | 4.8x CPython | one checksum per lowered module, no libpython |
| scripting | 21.1x CPython | 5.6x CPython | `json` + `re` + `pathlib`, the pyperformance scripting slice that lowers |
| island | 1.2x CPython | 0.9x CPython | embedded CPython for `socket`, `inspect`, `threading`, `logging`, `subprocess` |

`importlib` and `asyncio` import cleanly. `import_module` and `asyncio.run` are `PS2001` and have no kernel. `importlib.resources.files` / `as_file` do lower.

Known deltas on the lowered surface, each one deliberate:

- `and` / `or` across two types, f-string format specs, a slice step other than 1, `break`, `continue`, `finally`, user decorators, starred calls, `**kwargs`, and keyword arguments on an unnamed parameter list are fences. `*args` of one element type lowers as a list
- integers that do not fit in `i64` are refused or trap; they are not Python longs
- an empty `for` target reads as `0` / `0.0` / `""` instead of `UnboundLocalError`
- `os.getenv` of an unset name raises `KeyError`
- `warnings.warn` prints `UserWarning: message` without a filename
- `re` errors, `struct.error`, and `binascii.Error` surface as `ValueError`
- literals outside Latin-1 are refused

## Suites we did not vendor

The attached survey recommends real downstream suites plus pyperf. Those suites assume the target is still CPython with an extra library loaded. Scored against this compiler:

| Suite | Verdict | First fence |
| --- | --- | --- |
| pyperformance `python_startup,pathlib,regex_v8,json_loads,json_dumps,docutils` | stand-in only | `script-slice`, `pathlib-read`, `re-scan`, and `json-roundtrip` cover the modules. The official corpora use floats, captures, and docutils, which do not lower. `python_startup` times a CPython process, not a compiled binary |
| pallets/click | stays on CPython | `PS2002` decorators, and `click` is outside the import table |
| tiangolo/typer | stays on CPython | click, plus a runtime that is built out of `typing` |
| pypa/pip | stays on CPython | `argparse`, network, and packaging imports are outside the table |
| psf/black | stays on CPython | the parser and formatter are not the lowered subset; decorators and f-strings fence first |
| django, starlette, fastapi, sqlalchemy, pydantic, celery, anyio | stays on CPython | metaclasses, async, C extensions, and imports outside the table. The survey's `sitecustomize.py` hook does not apply |

`tests/test_adoption.py` compiles the small shapes those libraries need (an outside import, a decorator, a keyword argument, `*args`, `asyncio.run`, an f-string) and expects a fence. That is the canary. Cloning the upstream suites would time CPython, not LLVM.

## What an early user can ship

A single annotated file of ints, strings, lists, dicts, and the lowered stdlib:

```bash
uv run pyscriptc compile examples/rest_probe.py --run --call probe -o /tmp/probe
```

The binary is linked with the small runtime. If the LLVM text contains `ps_island_`, the link line adds libpython from the interpreter that compiled it, and the first island call runs `Py_InitializeEx`. Benchmarks that never call the island stay at a few megabytes of RSS. That split is the distribution story: scripts in the subset ship as a small binary; everything else keeps running on CPython, and the fence names the reason.
