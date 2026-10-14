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

# Primary-score weights. Plan section 3 (primary metric) and section 4 (weak probe classes).
# `program` is one script. Top100 uses CORPUS_WEIGHTS. Modules stay at 1.0: section 3 does not
# name a separate module weight.
WEIGHTS: dict[str, float] = {
    "function": 1.0,
    "class": 1.0,
    "method": 1.0,
    "exception": 1.0,
    "constant": 0.1,
    "module": 1.0,
    "language": 1.0,
    "program": 3.0,
}

# Top100 secondary: 2.0 per corpus function, 5.0 per whole package (section 3).
CORPUS_WEIGHTS: dict[str, float] = {
    "function": 2.0,
    "program": 5.0,
}

# Weak folds stay in the score below a core probe (section 4). Constants stay on WEIGHTS.
WEAK_FOLDS: frozenset[str] = frozenset({"handle", "recv-handle", "exec", "none", "shape"})
WEAK_FOLD_WEIGHT: float = 0.25

# A raises probe with a valid argument list stays at 0 until E2. This table does not flip it.
RAISES_WEIGHT: float = 0.0
# Mention probes are 0, including a dropped no-valid-argument raises row once it is a mention.
# Weight 0 leaves the weighted sum, the same way the v1 continuity rows do.
MENTION_WEIGHT: float = 0.0


def probe_weight(category: str, *, kind: str = "value", fold: str = "", area: str = "") -> float:
    """Weight stored on a Probe.

    Mention and raises contribute nothing. A weak fold is 0.25, and a constant stays at 0.1
    even when its fold is weak (section 4 leaves constants unchanged). Scripts and top100 use
    their own rows of the primary metric.
    """

    if kind == "mention":
        return MENTION_WEIGHT
    if kind == "raises":
        return RAISES_WEIGHT
    if area == "top100":
        return CORPUS_WEIGHTS[category]
    if area == "scripts":
        return WEIGHTS["program"]
    if fold in WEAK_FOLDS and category != "constant":
        return WEAK_FOLD_WEIGHT
    return WEIGHTS[category]


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


@dataclass(frozen=True)
class Probe:
    """One executable check. Produced by compat2/probes, consumed by compat2/score.

    kind "value": compile `source`, run `call(*args)` natively, compare to the CPython oracle.
    kind "raises": same, but the expected result is an exception type name in `expect_exc`.
    kind "mention": the name is only referenced, so it can never beat partial. The headroom
    check marks such rows reachable=False and the scorer refuses to start.
    """

    id: str
    area: Literal["language", "builtins", "surface", "modules", "top100", "scripts"]
    category: Category
    kind: Literal["value", "raises", "mention"]
    source: str  # python module text containing the function named by `call`
    call: str = "probe"
    args: tuple[object, ...] = ()
    expect_exc: str = ""
    weight: float = 1.0
    # Subset of CPython 3.10 to 3.14 where the name exists and the probe agrees with the oracle (probes/versions.py)
    versions: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return asdict(self)
