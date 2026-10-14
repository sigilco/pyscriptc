"""Shared contract for scorer v2: typed outcomes, categories, ledger row.

Every compat2 module imports these; do not redefine them elsewhere.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Outcome = Literal["pass", "fence", "wrong-output", "crash", "timeout", "build-error", "excluded"]
Category = Literal["function", "class", "method", "exception", "constant", "module", "language", "program"]

OUTCOMES: tuple[str, ...] = (
    "pass",
    "fence",
    "wrong-output",
    "crash",
    "timeout",
    "build-error",
    "excluded",
)
# Soundness failures are worse than a fence and trip the hard gate.
SOUNDNESS: frozenset[str] = frozenset({"wrong-output", "crash"})

CATEGORIES: tuple[str, ...] = (
    "function",
    "class",
    "method",
    "exception",
    "constant",
    "module",
    "language",
    "program",
)

# Primary-score weights. Constants stay low so batches cannot dominate.
WEIGHTS: dict[str, float] = {
    "function": 1.0,
    "class": 1.0,
    "method": 1.0,
    "exception": 1.0,
    "constant": 0.1,
    "module": 1.0,
    "language": 1.0,
    "program": 5.0,
}


@dataclass(frozen=True)
class Row:
    """One denominator unit and its evidence. Also the support-ledger row."""

    id: str  # stable, e.g. "builtins.map", "stdlib.csv.reader", "lang.expr-pow"
    area: Literal["language", "builtins", "surface", "modules", "top100", "scripts"]
    category: Category
    outcome: Outcome
    reachable: bool = True  # False means a capped row: the headroom check must fail
    code: str = ""  # PS diagnostic code when outcome is fence
    evidence: str = ""  # probe path or log line
    epic: str = ""

    def to_json(self) -> dict[str, object]:
        return asdict(self)
