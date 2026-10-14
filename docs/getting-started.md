# Getting started

Requirements: CPython 3.14, [uv](https://docs.astral.sh/uv/), and a clang that can assemble textual LLVM IR.

```bash
uv sync
uv run pyscriptc compile examples/invoice.py --run --call charge --args '[10, 2, 50]' -o /tmp/charge
```

That prints `600`. The same function under CPython returns `600`.

## The command

```bash
uv run pyscriptc compile FILE --run --call FUNC --args '[...]' -o /tmp/name
```

- `FILE` is one Python file. Imports come from the closed standard-library table. A second file of yours is not imported.
- `--call` is the function `main` calls. The binary prints that return value. There is no `print` in the subset.
- `--args` is a Python list literal (`ast.literal_eval`). Elements can be `int`, `float`, `bool`, or `str`. A `list`, a `dict`, or `bytes` is built inside the function.
- The printed return is an `int`, a `float` (`%.17g`), or a `str`. A `bool` return cannot be printed.
- `--emit ir` writes JSON. `--emit llvm` writes textual LLVM. `--emit bin` writes the binary and does not run it.
- `--skip-check` skips mypy. The default preflight is mypy.

## When it stops

| Exit | Cause |
| --- | --- |
| 0 | The file lowered, and the binary's own status if you used `--run` |
| 2 | mypy rejected an annotation |
| 3 | The subset refused the file. The message names a `PS` code |
| 4 | The integer or the CPython stamp does not fit this backend |

A fence is the whole file. Fix the named construct, or keep that program on CPython.

## What you get

A script that never calls the island links the small runtime and does not link libpython. `examples/hostname.py` calls `socket.gethostname`, so that binary links the CPython that compiled it and initializes it on the call.
