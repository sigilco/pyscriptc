---
title: Supported CPython versions
description: Which Python versions pyscriptc supports and why.
---

pyscriptc supports [Python versions still supported upstream](https://devguide.python.org/versions/); **CPython 3.14** is the primary reference. The parser is CPython 3.14's `ast`, and `pyproject.toml` requires `>=3.14` to run the compiler.

Two different version axes matter:

- **Compiler host** — the CPython that runs pyscriptc itself (`>=3.14`, enforced in `pyproject.toml`). This is also the oracle the semantics are checked against.
- **Emitted binaries** — native executables with no Python version dependency at all. The `cpython` IR stamp is a consistency check, not a runtime requirement.

## Why the floor is 3.14

- The front end parses with the running interpreter's `ast` module, and the lowering oracle compares behavior against that same interpreter, so supporting older hosts means maintaining a second set of parser and semantics expectations for no user benefit.
- Binaries compiled by pyscriptc do not embed Python, so users running compiled programs are unaffected by the host floor.
- The version axis only affects which interpreter runs the compiler and which CPython the lowering matches; it never affects the produced binaries.

The detailed assessment behind the window (lifecycle table, IR stamping, oracle installs, and the host-versus-target split) is in [`docs/python-version-window.md`](https://github.com/sigilco/pyscriptc/blob/master/.agents/docs/release/python-version-window.md).
