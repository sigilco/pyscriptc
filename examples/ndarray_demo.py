"""The pyscriptc_ndarray surface, end to end (issue #496, M1).

A program that touches every name of the closed M1 subset, so the
coverage report of this file is the receipt that the subset lowers
without a fence:

    pyscriptc coverage examples/ndarray_demo.py

The program prints a small report; the values are the ones a CPython
reference implementation of the same subset produces.
"""

import pyscriptc_ndarray as np


def creation() -> int:
    """The creation ops and the attributes."""
    a = np.array("float64", [1.0, 2.0, 3.0, 4.0])
    z = np.zeros([2, 2], dtype="int32")
    o = np.ones([3])
    f = np.full([2, 2], 7)
    r = np.arange(0, 10, 2)
    lines = np.linspace(0.0, 1.0, 5)
    print(a.tolist())
    print(a.shape, a.ndim, a.size, a.nbytes, a.dtype)
    print(z.tolist())
    print(o.tolist())
    print(f.tolist())
    print(r.tolist())
    print(lines.tolist())
    return 0


def arithmetic() -> int:
    """The elementwise ops and the comparisons."""
    a = np.array("int32", [4, 5, 6])
    b = np.array("int32", [1, 2, 3])
    f = np.array("float64", [1.0, 4.0, 9.0])
    print((a + b).tolist())
    print((a - b).tolist())
    print((a * b).tolist())
    print((f / 2.0).tolist())
    print((a // 2).tolist())
    print((a % 3).tolist())
    print((a > b).tolist())
    return 0


def reductions() -> int:
    """The whole-array reductions and the axis ones."""
    a = np.array("float64", [1.0, 2.0, 3.0, 4.0])
    m = a.reshape([2, 2])
    print(a.sum(), a.min(), a.max(), a.mean())
    print(a.argmin(), a.argmax())
    print(m.sum(axis=0).tolist())
    print(m.argmax(axis=1).tolist())
    return 0


def shapes() -> int:
    """The shape ops, the slicing and the mask."""
    a = np.arange(0, 6).reshape([2, 3])
    print(a.transpose().tolist())
    print(a.ravel().tolist())
    print(a.astype("float64").tolist())
    print(a.copy().tolist())
    print(a[::2].tolist())
    print(a[a > 2].tolist())
    return 0


def conversions() -> int:
    """The list and bytes conversions, and the joins."""
    a = np.array("int64", [1, 2, 3])
    b = np.array("int64", [4, 5, 6])
    print(np.fromlist([7, 8], "int64").tolist())
    print(len(a.tobytes()))
    print(np.concatenate([a, b]).tolist())
    print(np.stack([a, b]).tolist())
    return 0


def run() -> int:
    creation()
    arithmetic()
    reductions()
    shapes()
    conversions()
    return 0
