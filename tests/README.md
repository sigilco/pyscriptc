# Tests in pyscriptc

This directory contains the automated test suite for the pyscriptc compiler and runtime.

## Running Tests

Run the full suite (excluding `linux_container` tests) with xdist worker bounds:

```bash
uv run pytest -n auto -m "not linux_container"
```

To run a specific test file or module:

```bash
uv run pytest tests/test_data_scaling.py
uv run pytest tests/test_differential_hypothesis.py
```

## Test Structure

- `tests/test_data_scaling.py`: Complexity guards for runtime data operations (#388). Measures execution times across scale sizes (N and 4N) to assert that operations stay near their expected algorithmic complexity bound (e.g., O(1) amortized list append and O(1) hash map operations stay below exponent 1.4).
- `tests/test_differential_hypothesis.py`: Property-based differential testing pilot using Hypothesis. Generates randomized integer arithmetic, bitwise operations, and string manipulations, asserting byte-identical output between compiled native binaries and the CPython 3.14 oracle.
- `tests/compat2/`: Compatibility scorer v2 suite. Frozen gates live here (see `AGENTS.md`).
- `tests/test_threading_*.py`: Concurrency and synchronization tests. Tests marked `linux_container` require Clang TSan/ASan environment.
