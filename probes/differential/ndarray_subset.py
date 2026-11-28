# SPDX-License-Identifier: Apache-2.0
"""The pyscriptc_ndarray M1 subset against real numpy (issue #496, M1).

The pack mirrors numpy's semantics on a closed subset but is reached only
through its own import name: `import numpy` is not redirected onto it.
This probe is spelled in the pack's own vocabulary, so it runs unchanged
under the pinned CPython (against real numpy, through the `numpy` import
name) and unchanged under the compiled program (against the
pyscriptc-ndarray pack, through the `pyscriptc_ndarray` import name).
The lane byte-compares stdout, stderr and the exit code.

Every line below is inside the closed M1 subset: the creation ops, the
elementwise arithmetic between two arrays and between an array and a
scalar, the comparisons, the whole-array reductions, reshape, the
concatenate/stack pair and the list conversion. The float formatting of
`tolist()` is part of the comparison, so a repr difference fails the
lane.
"""

import pyscriptc_ndarray as np

a = np.array("float64", [1.0, 2.0, 3.0, 4.0])
print(a.tolist())
print((a + a).tolist())
print((a * 2).tolist())
print((a / 2).tolist())
print((a > 2).tolist())
print(a.sum(), a.min(), a.max(), a.mean(), a.argmax())
print(a.reshape([2, 2]).tolist())
print(np.zeros([2, 3], dtype="int32").tolist())
print(np.arange(0, 10, 3).tolist())
print(np.linspace(0.0, 1.0, 3).tolist())
print(np.full([2, 2], 3.5).tolist())
print(np.concatenate([a, np.array("float64", [5.0])]).tolist())
print(np.stack([a, a]).tolist())
# The axis form of the reductions and of argmax: the index along the
# axis, and a negative axis counted from the end.
m = np.arange(0, 6).reshape([2, 3])
print(m.argmax(axis=0).tolist())
print(m.argmin(axis=1).tolist())
print(m.sum(axis=-1).tolist())
