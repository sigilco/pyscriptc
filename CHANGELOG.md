# Changelog

## [0.1.0] - 2026-10-17

Initial release.

- `pyscriptc` CLI (`compile`, `coverage`, `explain`): lowers an annotated Python subset to typed IR, then to LLVM, and links emitted programs against the bundled C runtime.
- CPython island: calls outside the subset (`subprocess`, `socket`, `threading`, `inspect`, `logging`, `asyncio`, `ssl`, `ctypes`) embed libpython only when the emitted code uses them.
- Closed stdlib surface: `math`, `re`, `json`, `urllib.parse`, `base64`, `hashlib`, `hmac`, `struct`, `datetime`, `collections`, `itertools`, `os`, `pathlib`, `tempfile`, `atexit`, `shutil`, `enum`, `dataclasses`. Anything outside the table fences instead of guessing.
- `try`/`except` for builtin exceptions and user subclasses; `sys.argv` as `list[str]`.
