"""The pyscriptc_frame slice-1 subset against real pandas (issue #497, slice 1).

The pack replaces `pandas`, so this probe is spelled in pandas' own
vocabulary: it runs unchanged under the pinned CPython (against real
pandas) and unchanged under the compiled program (against the
pyscriptc-frame pack). The lane byte-compares stdout, stderr and the
exit code.

Every line below is inside the closed slice-1 subset: the dict-display
construction, the column reads and writes, the metadata (`columns`,
`shape`, `len`), head, tail and the homogeneous `to_dict`. The column
order is part of the comparison, so the display order is the contract.
"""

import pandas as pd

df = pd.DataFrame({"a": [1, 2, 3, 4], "b": [4.5, 5.5, 6.5, 7.5]})
print(len(df))
print(df.columns)
print(df.shape)
print(df["a"].tolist())
print(df["b"].tolist())
print(df.head(2)["a"].tolist())
print(df.tail(2)["b"].tolist())
df["a"] = pd.DataFrame({"x": [9, 8, 7, 6]})["x"]
print(df["a"].tolist())
print(df.columns)
small = pd.DataFrame({"k": [3, 1, 2]})
d = small.to_dict()
print(len(d))
print(d["k"])
print(small.head()["k"].tolist())
print(small.tail(99)["k"].tolist())
print(small.head(0)["k"].tolist())
