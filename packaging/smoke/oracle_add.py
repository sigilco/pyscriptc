"""CPython oracle for the release smoke of examples/add.py.

Run with CPython 3.12. Prints one call the same way
``pyscriptc compile --run --call`` prints a scalar result.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("pyscriptc_smoke_example", path)
    if spec is None or spec.loader is None:
        print(f"cannot load {path}", file=sys.stderr)
        raise SystemExit(2)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: oracle_add.py SOURCE FUNC ARGS_LITERAL", file=sys.stderr)
        return 2
    source = Path(argv[1])
    func = argv[2]
    args = ast.literal_eval(argv[3])
    if not isinstance(args, list):
        print("ARGS_LITERAL must be a list", file=sys.stderr)
        return 2
    value = getattr(load_module(source), func)(*args)
    if isinstance(value, float):
        print(f"{value:.17g}")
    else:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
