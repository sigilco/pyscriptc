# SPDX-License-Identifier: Apache-2.0
"""The `@specialize` marker and the specialization report (issue #1309).

`pyscriptc._annotations.specialize` marks a definition the compiler
monomorphizes on purpose: the marker asserts the per-call-site
specialization the lowering already performs for a `TypeVar`-generic
function (`.agents/docs/python-style.md` rule 7) and for a `Generic[T]`
class. It is a statement of intent on the page, never a behavior change:
a marked definition lowers to exactly the copies it would lower to
undecorated.

The assertion is checked, not silently accepted. A marked definition whose
signature carries no TypeVar, and whose class declares no TypeVar
parameter, refuses with `PS1207`: the annotation promised something the
compiler does not do for it, and a hint the compiler ignores is worse than
a refusal naming the mismatch (the design note's own risk rule,
`.agents/docs/design/jit-and-codon-annotations.md`, candidate C).

The report is the legibility half of the marker: it lists, per marked
definition, the concrete copies the lowering emitted and the TypeVar
mapping each copy substituted, read from the lowering's own
instantiation tables (`inst_fns`, `class_insts`), never re-derived. The
compile prints it with `--specializations`; a marked definition that
specialized nowhere reports zero copies.
"""

from __future__ import annotations

import ast
from typing import Any

from pyscriptc.errors import span_of
from pyscriptc.lower._core import Fence, _has_typevar

SPECIALIZE_FENCE_MESSAGE = (
    "@specialize marks a definition for per-call-site monomorphization, but "
    "this definition is not generic: its signature carries no TypeVar (and a "
    "class carries no TypeVar parameter), so the compiler emits exactly one "
    "copy and there is nothing to specialize"
)


class SpecializeMixin:
    """The `@specialize` marker check and the specialization report."""

    def _specialize_check(self, fences: list[Fence]) -> None:
        """Fence a marked definition the specialization assertion does not hold for.

        A module-level def is generic when its lowered signature carries a
        TypeVar placeholder; a class is generic when it declared TypeVar
        parameters (`Generic[T]` or `class C[T]` style, recorded on the
        class info at collection). The check runs after the instantiation
        drains: a generic def with zero call sites is still generic (the
        assertion holds; the report shows zero copies), while a non-generic
        def fences wherever it is called.
        """

        for name in sorted(self._specialize_marked):
            node = self._specialize_marked_nodes.get(name)
            if node is None:
                continue
            if isinstance(node, ast.ClassDef):
                info = self.classes.get(name)
                if info is not None and info.get("typevars"):
                    continue
            else:
                sig = self.sigs.get(name)
                if sig is not None and (_has_typevar(sig[1]) or any(_has_typevar(p["type"]) for p in sig[0])):
                    continue
            fences.append(Fence("PS1207", SPECIALIZE_FENCE_MESSAGE, span_of(node, self.filename), name))

    def _specialize_report(self, lowered: list[dict[str, Any]], lowered_classes: list[dict[str, Any]]) -> None:
        """Build the specialization report rows for the marked definitions.

        The rows read the lowering's own bookkeeping: `inst_fns` maps each
        function instantiation key to the emitted copy's name and carries
        the TypeVar mapping the copy substituted; `class_insts` does the
        same for generic classes keyed by type argument. A marked
        definition with no entry specialized nowhere (zero emitted copies):
        the row still exists, because "nothing specialized" is exactly the
        fact the report exists to surface.
        """

        if not self._specialize_marked:
            return
        rows: list[dict[str, Any]] = []
        for name in sorted(self._specialize_marked):
            node = self._specialize_marked_nodes.get(name)
            row: dict[str, Any] = {
                "name": name,
                "line": node.lineno if node is not None else None,
                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                "copies": [],
                "count": 0,
            }
            if isinstance(node, ast.ClassDef):
                # The class instantiation key holds the concrete type
                # arguments; the TypeVar names they bind to are the class's
                # own declared parameters, in order.
                tvars = list((self.classes.get(name) or {}).get("typevars") or [])
                for (base, arg_keys), emit in sorted(self.class_insts.items()):
                    if base != name:
                        continue
                    row["copies"].append(
                        {
                            "name": emit,
                            "typevars": [
                                {"typevar": tvars[i] if i < len(tvars) else f"arg{i}", "type": key}
                                for i, key in enumerate(arg_keys)
                            ],
                        }
                    )
            else:
                for (fn_name, tv_keys), emit in sorted(self.inst_fns.items()):
                    if fn_name != name:
                        continue
                    mapping = [{"typevar": tv, "type": key} for tv, key in tv_keys]
                    row["copies"].append({"name": emit, "typevars": mapping})
            row["count"] = len(row["copies"])
            rows.append(row)
        self.specialization_rows = rows


def render_specializations(rows: list[dict[str, Any]]) -> str:
    """The text form of the specialization report (issue #1309).

    Same DX shape as the plane map and the coverage report: one block per
    marked definition, the copies and their TypeVar substitutions spelled
    out, and a nothing-found line when the source marks nothing (the
    report answers "what did the compiler specialize" even when the answer
    is nothing).
    """

    if not rows:
        return "no @specialize definitions in this module"
    lines: list[str] = []
    total = 0
    for row in rows:
        total += row["count"]
        head = f"{row['name']}: {row['count']} specialization(s)"
        if row.get("line") is not None:
            head += f" (line {row['line']})"
        lines.append(head)
        for copy in row["copies"]:
            tvs = ", ".join(f"{item['typevar']}={item['type']}" for item in copy["typevars"])
            lines.append(f"  {copy['name']}" + (f" [{tvs}]" if tvs else ""))
    lines.append(f"total: {total} emitted cop{'y' if total == 1 else 'ies'} across {len(rows)} marked definition(s)")
    return "\n".join(lines)
