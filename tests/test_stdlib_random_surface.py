"""random: the choices, sample, randbytes and distribution-draw surface.

The module-level generator is CPython's Mersenne Twister, so a seeded run
must print what CPython prints. The expected text comes from the host
`random` (the pinned CPython 3.14 in the test environment), run in-process.
"""

from __future__ import annotations

import random
from pathlib import Path

from tests.test_gates import compile_run, write_case


def _run(tmp_path: Path, name: str, source: str) -> str:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    return got.stdout


def test_seeded_draws_match_cpython(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import random",
            "",
            "def run() -> int:",
            "    random.seed(2024)",
            "    xs: list[int] = [1, 2, 3, 4, 5, 6, 7, 8, 9]",
            "    print(random.choices(xs, k=4))",
            "    print(random.sample(xs, 3))",
            "    print(random.randbytes(6))",
            "    print(random.gauss(1.0, 2.0))",
            "    print(random.gauss(1.0, 2.0))",
            "    print(random.normalvariate(0.0, 1.0))",
            "    print(random.expovariate(3.0))",
            "    print(random.triangular(0.0, 4.0, 1.0))",
            "    print(random.gammavariate(3.0, 1.0))",
            "    print(random.betavariate(2.0, 3.0))",
            "    print(random.lognormvariate(0.0, 1.0))",
            "    return 0",
            "",
        ]
    )
    random.seed(2024)
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    want = [
        random.choices(xs, k=4),
        random.sample(xs, 3),
        random.randbytes(6),
        random.gauss(1.0, 2.0),
        random.gauss(1.0, 2.0),
        random.normalvariate(0.0, 1.0),
        random.expovariate(3.0),
        random.triangular(0.0, 4.0, 1.0),
        random.gammavariate(3.0, 1.0),
        random.betavariate(2.0, 3.0),
        random.lognormvariate(0.0, 1.0),
    ]
    assert _run(tmp_path, "seeded", source) == "".join(f"{w}\n" for w in want) + "0\n"


def test_weighted_choices_and_errors(tmp_path: Path) -> None:
    source = "\n".join(
        [
            "import random",
            "",
            "def run() -> int:",
            "    random.seed(5)",
            "    names: list[str] = ['a', 'b', 'c']",
            "    print(random.choices(names, weights=[1, 0, 3], k=5))",
            "    try:",
            "        random.sample(names, 4)",
            "    except ValueError as e:",
            "        print(e)",
            "    return 0",
            "",
        ]
    )
    random.seed(5)
    names = ["a", "b", "c"]
    want = f"{random.choices(names, weights=[1, 0, 3], k=5)}\nSample larger than population or is negative\n0\n"
    assert _run(tmp_path, "weighted", source) == want
