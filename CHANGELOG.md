# Changelog

## [0.2.0] - 2026-10-21

Installs work. Every install path from 0.1.0 failed on the default checker, and there was no GitHub Release.

- Fix: the pyrefly checker is found in the installed environment instead of by bare PATH lookup, so `uv tool install pyscriptc` then `pyscriptc compile` works. `PYREFLY_BIN` still overrides.
- Fix: pyrefly checks against the running interpreter's site-packages, not the host Python's.
- Fix: the pysa facts adapter matches reports by their `source_path`, so it works on real packages instead of failing on every non top-level file.
- `compile --run -- ARGS` forwards arguments to the produced binary.
- `compile --print diagnostics` prints the coverage JSON report (exit 3 when there are fences).
- `coverage --fail-on blockers|any` exits 1 when fences exceed the threshold.
- CI: a wheel smoke job builds the wheel, installs it in a fresh venv with a stripped PATH, and compiles a fixture on every PR that touches the checker, CLI or packaging.
- README: install instructions point at PyPI and GitHub Releases.

## [0.1.0] - 2026-10-17

Initial release.

- `pyscriptc` CLI (`compile`, `coverage`, `explain`): lowers an annotated Python subset to typed IR, then to LLVM, and links emitted programs against the bundled C runtime.
- CPython island: calls outside the subset (`subprocess`, `socket`, `threading`, `inspect`, `logging`, `asyncio`, `ssl`, `ctypes`) embed libpython only when the emitted code uses them.
- Closed stdlib surface: `math`, `re`, `json`, `urllib.parse`, `base64`, `hashlib`, `hmac`, `struct`, `datetime`, `collections`, `itertools`, `os`, `pathlib`, `tempfile`, `atexit`, `shutil`, `enum`, `dataclasses`. Anything outside the table fences instead of guessing.
- `try`/`except` for builtin exceptions and user subclasses; `sys.argv` as `list[str]`.
