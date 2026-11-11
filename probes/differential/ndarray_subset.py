"""The pyscriptc_ndarray M1 subset against real numpy (issue #496, M1).

The pack replaces `numpy`, so this probe is spelled in numpy's own
vocabulary: it runs unchanged under the pinned CPython (against real
numpy) and unchanged under the compiled program (against the
pyscriptc-ndarray pack). The lane byte-compares stdout, stderr and the
exit code.

Every line below is inside the closed M1 subset: the creation ops, the
elementwise arithmetic between two arrays and between an array and a
scalar, the comparisons, the whole-array reductions, reshape, the
concatenate/stack pair and the list conversion. The float formatting of
`tolist()` is part of the comparison, so a repr difference fails the
lane.
"""

import numpy as np

a = np.array([1.0, 2.0, 3.0, 4.0], dtype="float64")
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
print(np.concatenate([a, np.array([5.0])]).tolist())
print(np.stack([a, a]).tolist())
