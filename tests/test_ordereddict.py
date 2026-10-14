"""`collections.OrderedDict()` with no arguments is an empty dict.

`len` of that value is 0. A positional iterable, keyword arguments,
`move_to_end`, `popitem`, and equality with a dict stay fenced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, run_cli, write_case

_PROBE = """
import collections

def checklist() -> int:
    return len(collections.OrderedDict())

def assigned() -> int:
    od: dict[str, int] = collections.OrderedDict()  # type: ignore[assignment]
    od["a"] = 1
    return len(od)
"""


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_ordereddict_matches_cpython(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ordered.py", _PROBE)
    expected = {"checklist": "0", "assigned": "1"}
    for func, want in expected.items():
        got = compile_run(tmp_path, path, func)
        assert got.returncode == 0, f"{func}\n{got.stderr}"
        assert got.stdout == oracle(path, func)
        assert got.stdout.strip() == want


def test_ordereddict_stays_fenced(tmp_path: Path) -> None:
    cases = [
        (
            "iterable.py",
            "import collections\ndef probe() -> int:\n    collections.OrderedDict([('a', 1)])\n    return 1\n",
            "an OrderedDict iterable is not lowered",
        ),
        (
            "keywords.py",
            "import collections\ndef probe() -> int:\n    collections.OrderedDict(a=1)\n    return 1\n",
            "OrderedDict keyword arguments are not lowered",
        ),
        (
            "move.py",
            "import collections\ndef probe() -> int:\n    collections.OrderedDict().move_to_end('a')\n    return 1\n",
            "OrderedDict.move_to_end is not lowered",
        ),
        (
            "pop.py",
            "import collections\ndef probe() -> int:\n    collections.OrderedDict().popitem()\n    return 1\n",
            "OrderedDict.popitem is not lowered",
        ),
        (
            "eq.py",
            "import collections\ndef probe() -> int:\n    return 1 if {'a': 1} == collections.OrderedDict() else 0\n",
            "OrderedDict equality is not lowered",
        ),
    ]
    for name, source, text in cases:
        path = write_case(tmp_path, name, source)
        got = run_cli(
            "compile",
            str(path),
            "--skip-check",
            "--emit",
            "llvm",
            "-o",
            str(tmp_path / f"{name}.ll"),
        )
        assert got.returncode == 3, f"{name}\n{got.stderr}"
        assert text in got.stderr, f"{name}\n{got.stderr}"
