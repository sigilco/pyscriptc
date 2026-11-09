"""Command line for the spike compiler: re-exports of the split modules."""

from typing import Any

from pyscriptc.cli._main import (
    build_parser,
    main,
)
from pyscriptc.cli._report import (
    _reduce_predicate,
    _scripts_of,
    config_cmd,
    coverage_cmd,
    explain_cmd,
    imports_cmd,
    logs_cmd,
    reduce_cmd,
)

__all__ = [
    "_reduce_predicate",
    "_scripts_of",
    "build_parser",
    "config_cmd",
    "coverage_cmd",
    "explain_cmd",
    "imports_cmd",
    "logs_cmd",
    "main",
    "reduce_cmd",
]


def __getattr__(name: str) -> Any:
    # The compiler modules are loaded lazily into `cli._main`'s globals by
    # `_load_compiler`; delegate attribute reads so `cli._verify_binary` and
    # friends keep working for callers like `assess.py`.
    import pyscriptc.cli._main as _main

    return getattr(_main, name)
