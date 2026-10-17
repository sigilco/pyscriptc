---
title: The Python subset
description: What pyscriptc compiles today and what it refuses.
---

pyscriptc compiles a deliberately strict subset of Python.
When a construct is not supported, compilation stops with a `PSxxxx` fence that names the exact line, it never guesses.
This page is the practical summary; the full contract lives in [`docs/supported.md`](https://github.com/sigilco/pyscriptc/blob/main/docs/supported.md) in the repo.

## Everyday Python that compiles

- `print(...)`, including `sep` and `end`
- `sys.argv` as `list[str]`, `sys.platform`, `sys.exit`
- `with open(path) as f:` and `f.read()`, `f.write(...)`
- `for` over `range`, lists, dict keys, and tuple literals; `break` and `continue`
- Slices, including negative steps (`s[::-1]`)
- `sorted(xs, key=lambda x: ...)` for the common key cases
- `if` / `elif` / `else`, mixed `and` / `or` in value position
- Comprehensions over one `for` without filters
- f-strings with simple interpolations, `%s` and `%d` formatting
- `try` / `except ValueError:` and `except Exception:` (the exception model is growing milestone by milestone)
- Single-file programs and directories resolved as a package
- A broad set of `str`, `list`, `dict`, `int`, `float`, `bytes`, and `pathlib.Path` methods, plus scalar casts like `int("42")`

## What stays fenced

- `argparse`, `requests`, and most imports outside the lowered stdlib table
- `**kwargs`, user decorators, `super()`, multiple inheritance, starred calls
- `finally`, `except ... as e` (for now)
- `with` statements other than `open(...)` and `importlib.resources.as_file(...)`
- Non Latin-1 string literals
- Async, threads, dynamic `getattr` on non-literal names, `eval`/`exec`

## Checking your own code

Run `pyscriptc coverage FILE` to get a per-line fence report before compiling, or `pyscriptc explain PS2001` to look up what a fence code means and the smallest rewrite that clears it.
