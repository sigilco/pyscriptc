"""Root conftest: make `pytest -n auto` use the resource-bounded worker count.

`--update-goldens` re-blesses the emitter golden corpus from the current
emitter output. The golden test (`tests/test_emitter_goldens.py`) uses it
after an intentional emitter change, so the corpus diff is the review artifact.
"""

from __future__ import annotations

import pytest
from scripts.pytest_workers import workers


def pytest_xdist_auto_num_workers(config):
    return workers()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="rewrite the emitter golden corpus from the current emitter output",
    )
