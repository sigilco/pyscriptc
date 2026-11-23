# SPDX-License-Identifier: Apache-2.0
"""The synthetic ~150-module tree the inference suite measures.

The suite stays in-repo and hermetic: the tree is generated at run time
into a temporary directory, one module per step in a chain, every module
importing only its predecessor. Each module holds one unannotated
parameter feeding one annotated caller, so the engine has a pin to prove
per module and the tree's size scales linearly with `MODULE_COUNT`.

The shape mirrors the measured cost drivers from the design note
(`.agents/docs/design/inference-perf-baseline.md`): per-module AST
walking, one checker server for the whole tree, and the pin reduction,
with no stdlib import surface that could drift as the packs grow.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

# The design note prices a tree of about 150 modules; the pin engine's
# cost profile on that size is the number the suite records per release.
MODULE_COUNT = 150


def module_source(index: int) -> str:
    """The source of one module in the chain."""

    previous = f"from mod_{index - 1:03d} import step_{index - 1:03d}\n" if index > 0 else ""
    return (
        f'"""Synthetic inference module {index:03d}."""\n'
        "\n"
        f"{previous}"
        "\n"
        "\n"
        f"def step_{index:03d}(value):\n"
        f"    total = value + {index}\n"
        "    return total\n"
        "\n"
        "\n"
        f"def use_{index:03d}(count: int) -> int:\n"
        f"    return step_{index:03d}(count) + {index}\n"
    )


def main_source() -> str:
    """The program entry: one call through the whole chain."""

    last = MODULE_COUNT - 1
    return (
        f"from mod_{last:03d} import use_{last:03d}\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        f"    print(use_{last:03d}(1))\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def write_tree(directory: Path | None = None) -> Path:
    """Materialize the synthetic tree, or a fresh temporary copy when no
    directory is given. The caller owns the returned root."""

    root = directory if directory is not None else Path(tempfile.mkdtemp(prefix="pyscriptc-infer-bench-"))
    root.mkdir(parents=True, exist_ok=True)
    for index in range(MODULE_COUNT):
        (root / f"mod_{index:03d}.py").write_text(module_source(index), encoding="utf-8")
    (root / "__main__.py").write_text(main_source(), encoding="utf-8")
    return root
