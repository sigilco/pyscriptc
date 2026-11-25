# SPDX-License-Identifier: Apache-2.0
"""A tuple as a dict key (issue #1368).

The shape is `dict[tuple[str, str | None], int]`, the one iniconfig 2.3.1's
`_parse.py` uses for its `_sources` map: the key is a `(section, name)`
pair and the name arm may be absent.

CPython hashes a tuple field-wise and compares it field-wise, so the probe
pins that the compiled program agrees on:

- the item store, the read and the membership test,
- a key whose optional arm is absent (`("a", None)`) equals another one,
- a key whose optional arm is present differs from the absent one,
- the two-argument `get` and its default arm,
- the KeyError message of a missing key, which is the tuple's repr.
"""


def main() -> None:
    sources: dict[tuple[str, str | None], int] = {}
    sources["alpha", "one"] = 1
    sources["alpha", None] = 2
    sources["beta", "two"] = 3

    print(sources["alpha", "one"], sources["alpha", None], sources["beta", "two"])

    print(("alpha", "one") in sources)
    print(("alpha", None) in sources)
    print(("alpha", "two") in sources)
    print(("beta", None) in sources)
    print(("gamma", "one") in sources)

    # The absent arm and the present arm are different keys.
    print(sources.get(("alpha", None), -1))
    print(sources.get(("alpha", "two"), -1))
    print(sources.get(("gamma", None), -1))

    # A second map over the same shape: the keys are compared by value, so
    # a key read out of one map finds the entry in the other.
    other: dict[tuple[str, str | None], int] = {}
    other["alpha", None] = 20
    print(other[("alpha", None)], sources[("alpha", None)])

    try:
        print(sources["gamma", "one"])
    except KeyError as err:
        print("KeyError", err)


if __name__ == "__main__":
    main()
