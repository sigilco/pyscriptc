# A `with` block in a generator: `__exit__` on suspend (2026-10-24)

Decision (issue #433, G1): when a generator suspends inside a `with` block, the
context manager's `__exit__` does **not** run at the suspension point. The
generator resumes inside the `with` body, and `__exit__` runs once, when the
`with` body completes (or the exception leaves it). This is CPython 3.14's
behaviour, measured against the CPython oracle:

```python
class Ctx:
    def __enter__(self): print("enter"); return 1
    def __exit__(self, *a): print("exit"); return False

def g():
    with Ctx():
        yield 1
        yield 2

for v in g():
    print(v)
```

CPython prints `enter`, `1`, `2`, `exit`. The `__exit__` runs after the last
`yield` is resumed and the body falls out of the `with`.

Why the suspension must not run `__exit__`: the `with` block is still live
when the generator suspends. CPython keeps the context manager on the
generator's frame and unwinds it only when the frame leaves the block. Running
`__exit__` at the suspension point would close the resource the resumed body
still uses, and the resume would then run the body outside the block.

How pyscriptc lowers it: every `with` the lowerer supports already desugars
into a `try` before the emitter sees it (`_with_cm` in `lower/_stmt.py`
builds the try, `open` and `as_file` build their own). The generator guard
transform in `coro.py` re-enters that try on the resume and the emitter's
suspension flow code (2) skips the `finally` half of the desugared try, so the
`__exit__` call does not run at the suspension point. The resume continues
inside the try body and the `__exit__` call runs when the body falls through
or raises. The suspension therefore behaves like the `finally`-guarded
suspension that issue #164 already decided for `try`/`finally`.

The cost accepted: the frame must keep the context manager alive across the
suspension (it is an ordinary local of the generator body, so the copy-out at
the suspension keeps it), and a generator abandoned inside a `with` never runs
`__exit__` (CPython runs it through `GeneratorExit`, which this subset does
not lower; the same gap is already recorded for a pending `finally` in
`docs/reference/divergences.md` item 25).

Alternative rejected: running `__exit__` at the suspension point and
re-entering the `with` on the resume. It needs a second `__enter__` call (or a
re-entry of the manager), which is observable (`__enter__` would print twice),
and it does not match CPython.

Exception path (also CPython, also covered by the try desugar): a `raise`
inside the `with` body after a resume runs `__exit__` before the exception
reaches the outer `try`. CPython prints `enter`, `1`, `exit`, `caught boom`.

Abandonment (no `GC`/`GeneratorExit` in this subset, same gap as divergence
25): CPython runs `__exit__` when the generator is collected
(`enter`, `1`, `exit`, `after` for a generator dropped after one `next()`).
The compiled program prints `enter`, `1`, `after`; the divergence is the
already-recorded "a pending cleanup does not run on abandonment", extended to
the `with` half by this stage.
