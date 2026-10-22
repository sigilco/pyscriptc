# Fence repro probes (C13)

Minimal repro cases for the real fence findings in `docs/compile-rate-essence-method.md` (C11), re-verified on master `343cad5` so E1 can fix them first and prove the fixes.

Verification command per snippet: `pyscriptc compile --emit bin --call main --skip-check -o /tmp/x.bin probe.py`, then run `/tmp/x.bin`. `PYTHONPATH=src` on the repo checkout.

Two of the four findings no longer reproduce: `list.append` and `int(bytes)` were lowered in 4b79639 and ce2b088, after C11 was written. They are recorded here for the audit trail and dropped from the actionable set.

## 1. `list.append` — stale, fixed

C11 fence: PS2001 "call of attribute is not lowered".

```python
def main() -> int:
    xs: list[int] = []
    xs.append(1)
    xs.append(2)
    return len(xs)
```

Current verdict: compiles and runs, exits 2. Fixed by 4b79639 "feat(lower): list.append inlines as sequence append". Dropped.

## 2. `int(bytes)` — stale, fixed

C11 fence: PS2003.

```python
import subprocess


def main() -> int:
    out: bytes = subprocess.check_output(["printf", "42"])
    n: int = int(out)
    return n
```

Current verdict: compiles and runs, exits 42. Fixed by ce2b088 "feat(lower): int(bytes) parses at runtime". Dropped.

## 3. Unannotated local — reproduces, fence shifted to PS2002

C11 recorded this as PS1001 annotation-before-use. The same source now fences PS2002, because an unannotated assignment never creates a local for the lowering to see.

```python
def main() -> int:
    x = 1 + 2
    return x
```

Current fence: `PS2002: x is not a local or parameter` at the `x = 1 + 2` line (and again at `return x`).

Desired lowering: infer the local's type from a lowerable RHS, or reclassify the diagnostic back to PS1001 so the message tells the author to annotate. Inference is the real fix; the diagnostic reclass is a stopgap.

## 4. `os.getenv(missing)` raises `KeyError` — reproduces, by design

```python
import os


def main() -> int:
    v: str = os.getenv("DEFINITELY_MISSING_ENV_VAR")
    return len(v)
```

Current verdict: compiles; the binary exits 1 with `KeyError: environment variable is unset`. `ps_path.c` documents KeyError as the subset stand-in for `None`. A two-arg call fences: `PS2003: os.getenv expects 1 argument(s)`.

The documented workaround lowers and runs today:

```python
import os


def main() -> int:
    try:
        v: str = os.getenv("DEFINITELY_MISSING_ENV_VAR")
        return len(v)
    except KeyError:
        return -1
```

Exits -1. Desired change, if any: accept the optional `default` argument so `os.getenv(k, d)` matches Python semantics. This is a behavior note, not a bug; the try/except contract already works.

## Ranking for E1

E1's declared scope is module-level statements, `print`, `break`/`continue`, `assert`, `del`, `finally`. None of the live findings sits inside that scope; E1 unblocks essence fidelity structurally (the `def main()` wrapper on all 45 rows) rather than through these fences.

| # | Finding | Reproduces | E1 scope | Disposition |
| --- | --- | --- | --- | --- |
| 1 | `list.append` not lowered | no (fixed 4b79639) | no | dropped, done |
| 2 | `int(bytes)` not lowered | no (fixed ce2b088) | no | dropped, done |
| 3 | unannotated local fences | yes (PS2002, was PS1001) | no | independent quick win; RHS type inference or diagnostic reclass, later epic if not picked up |
| 4 | `os.getenv(missing)` KeyError | yes, by design | no | optional `default` arg is a small stdlib-surface widening, later epic; try/except already works |

Net actionable set for E1 sequencing: nothing in this list gates E1. Finding 3 is the only real fence left and is orthogonal; it can land before, during, or after E1.
