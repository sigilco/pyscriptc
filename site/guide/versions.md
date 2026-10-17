---
title: Version policy
description: Which Python versions pyscriptc targets.
---

pyscriptc targets the Python versions still supported upstream: **3.11 and newer**, with **3.12** as the primary reference.
Python 3.10 reached end of life in October 2026 and is not supported.

Two different version axes matter:

- **Compiler host** — the CPython that runs pyscriptc itself (`>=3.12`, enforced in `pyproject.toml`). This is also the oracle the semantics are checked against.
- **Emitted binaries** — native executables with no Python version dependency at all.

The details and the rationale are in [`docs/python-version-window.md`](https://github.com/sigilco/pyscriptc/blob/main/docs/python-version-window.md).
