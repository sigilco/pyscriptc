"""The pyscriptc-arrow adapter's probe: the Arrow C Data / C Stream data plane.

The adapter is not part of the plain CPython differential (the oracle has no
`pyscriptc_arrow` module), so the differential lives in
tests/test_ffi_arrow_adapter.py and tests/test_ffi_arrow_pilot.py: the same
sequence runs on the host thunk linked against the runtime and on PyArrow,
and the values byte-compare over the Arrow IPC stream wire form in both
directions.

This probe is the subset-facing half that does byte-compare against CPython:
the plain list that feeds `import_array` prints identically on both sides
(the input side), and the decoded records print the same text the PyArrow
leg asserts (the output side, pinned in the two test files' expected
strings). The precision fence (`array_get` widens to double, so an int64
above 2**53 loses integer precision) is recorded in the divergence ledger,
entry 64, and is not probed here.
"""

values: list[int] = [11, 22, 33, 40, -5]
for v in values:
    print(v)
print(sum(values))
