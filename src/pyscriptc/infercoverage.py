# SPDX-License-Identifier: Apache-2.0
"""The `pyscriptc infer` coverage report (issue #1304, the stage 1 diagnostics).

Stage 1 of the loose-typing and default-inference rollout: the number the
stage 2 decision reads. For every module in a tree the report counts the
unannotated parameters (the parameters a default-on pin pass would try to
prove), the ones the package-wide pins cover, the ones the pins do not
cover, and the pin rate — the share of the unannotated surface the pins
would lift. A tree total closes the report.

Diagnostics only: nothing here touches lowering, the pins or the user's
source. The unannotated-parameter census is static (the parameter AST
walk `apply_pins` already reads); the pin coverage reads the same pin
facts `build_facts` reduces, under the same all-or-nothing rule, so the
report never promises a pin the lowerer would not apply.

Self parameters (`self`, `cls`) are not counted: the lowerer types them
from the owning class (`_signature`), so they are not part of the surface
the pins would have to prove.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pyscriptc.inferfacts import InferFacts, _unannotated_params
from pyscriptc.inferrunner import _module_name


def _defs_of(tree: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """The tree's defs, each with the qualified name the report shows.

    A module-level def is qualified by its module (`pkg.mod.func`), the
    name the pin map keys it by. A method is qualified by its class
    (`pkg.mod.Class.method`), so a method never collects a module-level
    def's pin of the same name. The pin engine keys call sites by plain
    name, so a method is never pinned anyway; the class prefix keeps the
    report honest about what the fence belongs to.
    """

    out: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((node.name + "." + child.name, child))
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node))
    return out


@dataclass
class ModuleCoverage:
    """One module's row of the report."""

    module: str
    unannotated: int = 0
    pinned: int = 0
    unpinned: int = 0
    params: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """The JSON row: counts, the pin rate and the parameter detail."""
        rate = self.pinned / self.unannotated if self.unannotated else None
        return {
            "module": self.module,
            "unannotated_params": self.unannotated,
            "pinned_params": self.pinned,
            "unpinned_params": self.unpinned,
            "pin_rate": round(rate, 4) if rate is not None else None,
            "params": self.params,
        }


def _module_of_callee(callee: str) -> str:
    """The dotted module part of a qualified pin callee (`pkg.mod.func`)."""
    return callee.rpartition(".")[0]


def coverage_report(root: Path, facts: InferFacts, policy: Any = None) -> dict[str, Any]:
    """The coverage report for a tree: per module and the tree total.

    `root` is the package directory the facts were built over, `facts`
    the `build_facts` result. A pin covers the unannotated parameter its
    qualified callee names; every unannotated parameter no pin reaches is
    unpinned, the fence the default-on decision would leave standing.

    `policy` is the optional typing policy (issue #1315): when it is
    given, the report carries the typing section the strictness levels
    configure, and the per-module rows carry the level of their module.
    Without a policy the report is the plain coverage report it always
    was.
    """

    root = root.resolve()
    pins_by_module: dict[str, dict[tuple[str, str], Any]] = {}
    for (callee, param), pin in facts.pins.items():
        pins_by_module.setdefault(_module_of_callee(callee), {})[(callee, param)] = pin
    rows: dict[str, ModuleCoverage] = {}
    paths: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        paths.append(path)
        module = _module_name(path, root)
        row = rows.setdefault(module, ModuleCoverage(module=module))
        module_pins = pins_by_module.get(module, {})
        for name, node in _defs_of(tree):
            qualified = f"{module}.{name}"
            for param in _unannotated_params(node):
                pin = module_pins.get((qualified, param))
                row.unannotated += 1
                row.params.append(
                    {
                        "param": f"{qualified}.{param}",
                        "pinned": pin is not None,
                        "type": None if pin is None else pin.kind,
                    }
                )
                if pin is None:
                    row.unpinned += 1
                else:
                    row.pinned += 1
    modules = [row.as_dict() for row in sorted(rows.values(), key=lambda r: r.module)]
    unannotated = sum(row["unannotated_params"] for row in modules)
    pinned = sum(row["pinned_params"] for row in modules)
    unpinned = sum(row["unpinned_params"] for row in modules)
    report: dict[str, Any] = {
        "provider": facts.provider,
        "modules": modules,
        "total": {
            "unannotated_params": unannotated,
            "pinned_params": pinned,
            "unpinned_params": unpinned,
            "pin_rate": round(pinned / unannotated, 4) if unannotated else None,
        },
    }
    if policy is not None:
        from pyscriptc import typinglevels

        findings: list[typinglevels.Finding] = []
        for path in paths:
            findings.extend(typinglevels.scan_file(path))
        report["typing"] = typinglevels.typing_report(policy, paths, findings)
    return report


def render_coverage_text(report: dict[str, Any]) -> str:
    """The text report: one row per module, the tree total last.

    `pin_rate` is `None` when nothing is unannotated (nothing to prove);
    the text prints `-` there. When the report carries the typing section
    (issue #1315), the typing findings join the rows.
    """

    lines: list[str] = []
    lines.append(f"inference coverage ({report['provider']})")
    lines.append(f"{'module':<40} {'unannotated':>11} {'pinned':>8} {'unpinned':>9} {'pin rate':>9}")
    for row in report["modules"]:
        rate = "-" if row["pin_rate"] is None else f"{row['pin_rate'] * 100:.1f}%"
        lines.append(
            f"{row['module']:<40} {row['unannotated_params']:>11} {row['pinned_params']:>8} "
            f"{row['unpinned_params']:>9} {rate:>9}"
        )
    total = report["total"]
    rate = "-" if total["pin_rate"] is None else f"{total['pin_rate'] * 100:.1f}%"
    lines.append(
        f"{'total':<40} {total['unannotated_params']:>11} {total['pinned_params']:>8} "
        f"{total['unpinned_params']:>9} {rate:>9}"
    )
    if "typing" in report:
        _render_typing(lines, report["typing"])
    return "\n".join(lines)


def _render_typing(lines: list[str], section: dict[str, Any]) -> None:
    """The typing section of the text report: the mode, then the findings."""

    summary: dict[str, Any] = section["summary"]
    count: int = summary["errors"] + summary["warnings"]
    lines.append("")
    lines.append(f"typing mode {section['mode']}: {count} finding(s)")
    findings: list[dict[str, Any]] = section["findings"]
    for finding in findings:
        severity: str = finding["severity"]
        lines.append(f"  {severity}: {finding['file']}:{finding['line']}: {finding['code']} {finding['message']}")
