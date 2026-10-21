# Changelog

## [0.2.1] - 2026-10-21

Release binaries work. The 0.2.0 Nuitka binaries failed their smoke test because the default checker (pyrefly, a native executable) was not inside them.

- Fix: the release binaries bundle the pyrefly executable and use it by default; `PYREFLY_BIN` still overrides.
- `pyscriptc doctor [--json]` reports the checker (path, version, resolution source), the C toolchain, cache dir, target default and install shape.
- `PYSC_TARGET`, `PYSC_CC` and `PYSC_LINKER` set the matching flag defaults; `--linker`, `--emit asm,obj` and `--out-dir` are new.
- Telemetry v1 (`compile.finished`, `coverage.reported`): anonymous, opt out with `PYSC_NO_TELEMETRY=1` or `pyscriptc telemetry disable`. See `site/guide/telemetry.md`.
- GitHub Release notes are now built from the matching CHANGELOG.md section.
- Nightly and manual cut-release workflows (`docs/release-process.md`).

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
