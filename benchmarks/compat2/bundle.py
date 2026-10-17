"""Bundles: compile up to 50 probes of one import set as a single translation unit.

Per probe cost is dominated by process start, lowering and clang over every runtime C file,
so one build for 50 probes cuts the per probe time by the same factor. A bundle never decides
anything it cannot prove for each probe:

- fenced probes are attributed through the IR (`function` is `probe_<i>`), so no bisect;
- a probe passes only when its bit is set in the mask the bundle returns, and the bit is set
  only when the native value equals the CPython oracle value;
- every other outcome (wrong value, crash, timeout, build error) is re-run solo by the
  regular evaluator, so evidence and typing match an unbundled run. A bundle that crashes or
  fails to build is bisected until the culprit is alone.

A probe is bundleable when it is a `value` probe whose source is imports plus one function
returning `int`, with scalar arguments and an oracle value that is an int. Everything else
runs solo.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from benchmarks.compat2.outcomes import Probe, Row

if TYPE_CHECKING:
    from benchmarks.compat2.score import Result

MAX_BUNDLE = 50
ROOT = Path(__file__).resolve().parents[2]
I64 = 2**63
# the mask holds one bit per probe and must stay a positive i64
assert MAX_BUNDLE < 63

Solo = Callable[[Probe, float], "Result"]


@dataclass(frozen=True)
class Parsed:
    imports: tuple[str, ...]
    func: ast.FunctionDef


def parse(probe: Probe) -> Parsed | None:
    """The probe's shape when it can join a bundle, else None."""

    if probe.kind != "value" or probe.area == "top100" or not probe.call:
        return None
    if any(type(a) not in (int, str, bool, float, bytes) or (isinstance(a, float) and a != a) for a in probe.args):
        return None
    if any(isinstance(a, float) and a in (float("inf"), float("-inf")) for a in probe.args):
        return None
    try:
        tree = ast.parse(probe.source)
    except SyntaxError:
        return None
    imports: list[str] = []
    funcs: list[ast.FunctionDef] = []
    for node in tree.body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                return None
            imports.append(ast.unparse(node))
        elif isinstance(node, ast.FunctionDef) and node.name == probe.call:
            funcs.append(node)
        else:
            return None
    if len(funcs) != 1:
        return None
    func = funcs[0]
    returns = func.returns
    if not (isinstance(returns, ast.Name) and returns.id == "int"):
        return None
    if any(isinstance(n, ast.Name) and n.id == probe.call for n in ast.walk(func) if n is not func):
        return None  # recursion: renaming would break it
    if func.decorator_list:
        return None
    return Parsed(tuple(sorted(set(imports))), func)


def plan(probes: list[Probe], size: int = MAX_BUNDLE) -> list[list[Probe]]:
    """Units of work: bundles of one import set, and singletons for everything else."""

    groups: dict[tuple[str, ...], list[Probe]] = defaultdict(list)
    units: list[list[Probe]] = []
    for probe in probes:
        parsed = parse(probe)
        if parsed is None:
            units.append([probe])
        else:
            groups[parsed.imports].append(probe)
    for members in groups.values():
        units += [members[i : i + size] for i in range(0, len(members), size)]
    return units


_BATCH_ORACLE = r"""
import json, os, signal, sys
jobs = json.load(sys.stdin)
out = []
def alarm(*_):
    raise TimeoutError
signal.signal(signal.SIGALRM, alarm)
for source, call, args, limit in jobs:
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        sys.stdout = open(os.devnull, "w")
        result = ["raise", "Error"]
        try:
            signal.alarm(limit)
            ns = {}
            exec(compile(source, "<probe>", "exec"), ns)
            value = ns[call](*args)
            result = ["ok", repr(value) if type(value) is not int else str(value)]
        except BaseException as exc:
            result = ["raise", type(exc).__name__]
        os.write(write_fd, json.dumps(result).encode())
        os._exit(0)
    os.close(write_fd)
    data = b""
    while chunk := os.read(read_fd, 65536):
        data += chunk
    os.waitpid(pid, 0)
    try:
        out.append(json.loads(data))
    except ValueError:
        out.append(["raise", "crash"])
print(json.dumps(out))
"""


def oracle_batch(probes: list[Probe], timeout: float) -> list[tuple[str, str]] | None:
    """CPython results for every probe in one process (a forked child each), or None on failure."""

    jobs = [[p.source, p.call, list(p.args), max(1, int(timeout))] for p in probes]
    try:
        with tempfile.TemporaryDirectory(prefix="compat2-oracle-") as tmp:
            proc = subprocess.run(
                [sys.executable, "-c", _BATCH_ORACLE],
                input=json.dumps(jobs),
                capture_output=True,
                text=True,
                timeout=timeout * 2 + len(probes),
                cwd=tmp,
            )
        if proc.returncode != 0:
            return None
        return [(kind, value) for kind, value in json.loads(proc.stdout.splitlines()[-1])]
    except (subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def source_of(probes: list[Probe], expected: dict[str, int]) -> str:
    parsed = [parse(p) for p in probes]
    assert all(parsed)
    lines = list(parsed[0].imports)  # type: ignore[union-attr]
    body = ["def bundle() -> int:", "    mask: int = 0"]
    for i, (probe, shape) in enumerate(zip(probes, parsed, strict=True)):
        func = ast.parse(ast.unparse(shape.func)).body[0]  # type: ignore[union-attr]
        assert isinstance(func, ast.FunctionDef)
        func.name = f"probe_{i}"
        lines += ["", ast.unparse(func)]
        call = f"probe_{i}({', '.join(repr(a) for a in probe.args)})"
        body += [f"    if {call} == {expected[probe.id]}:", f"        mask = mask + {1 << i}"]
    return "\n".join([*lines, "", *body, "    return mask", ""])


def evaluate_group(probes: list[Probe], timeout: float, solo: Solo) -> list[Result]:
    """One Result per probe, in order. `solo` is the regular per probe evaluator."""

    from benchmarks.compat2.score import Result  # late: score imports this module

    if len(probes) == 1:
        return [solo(probes[0], timeout)]
    started = time.monotonic()
    oracle = oracle_batch(probes, timeout)
    if oracle is None:
        return [solo(p, timeout) for p in probes]
    results: dict[str, Result] = {}
    members: list[Probe] = []
    expected: dict[str, int] = {}
    for probe, (kind, value) in zip(probes, oracle, strict=True):
        if kind == "ok" and value.lstrip("-").isdigit() and abs(int(value)) < I64:
            members.append(probe)
            expected[probe.id] = int(value)
        else:
            results[probe.id] = solo(probe, timeout)  # excluded or raising oracle: the solo path types it
    if members:
        results |= _run_bundle(members, expected, timeout, solo, Result, started)
    return [results[p.id] for p in probes]


def _run_bundle(
    members: list[Probe],
    expected: dict[str, int],
    timeout: float,
    solo: Solo,
    result_cls: type[Result],
    started: float,
) -> dict[str, Result]:
    def row(probe: Probe, outcome: str, code: str = "", evidence: str = "") -> Row:
        return Row(
            id=probe.id,
            area=probe.area,
            category=probe.category,
            outcome=outcome,  # type: ignore[arg-type]
            code=code,
            evidence=evidence[:300],
        )

    def bisect() -> dict[str, Result]:
        if len(members) == 1:
            return {members[0].id: solo(members[0], timeout)}
        mid = len(members) // 2
        out: dict[str, Result] = {}
        for half in (members[:mid], members[mid:]):
            out |= _run_bundle(half, {p.id: expected[p.id] for p in half}, timeout, solo, result_cls, time.monotonic())
        return out

    if len(members) == 1:
        return bisect()
    with tempfile.TemporaryDirectory(prefix="compat2-bundle-") as tmp:
        src = Path(tmp) / "bundle.py"
        binary = Path(tmp) / "bundle"
        src.write_text(source_of(members, expected), encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pyscriptc", "compile", str(src), "--emit", "bin", "--call", "bundle"]
                + ["-o", str(binary), "--skip-check"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=timeout * 2,
            )
        except subprocess.TimeoutExpired:
            return bisect()
        if proc.returncode == 3:
            fences = _fences(Path(str(binary) + ".ir.json"), len(members))
            if fences is None:
                return bisect()
            shared, own = fences
            fenced = {i: sorted(shared + own.get(i, []))[0] for i in range(len(members)) if shared or i in own}
            per = (time.monotonic() - started) / len(members)
            out = {
                members[i].id: result_cls(row(members[i], "fence", code, text), per)
                for i, (code, text) in ((i, f[1:]) for i, f in fenced.items())
            }
            rest = [p for i, p in enumerate(members) if i not in fenced]
            if rest:
                out |= _run_bundle(
                    rest, {p.id: expected[p.id] for p in rest}, timeout, solo, result_cls, time.monotonic()
                )
            return out
        if proc.returncode != 0 or not binary.exists():
            return bisect()
        try:
            ran = subprocess.run([str(binary)], cwd=tmp, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return bisect()
    if ran.returncode != 0:
        return bisect()
    try:
        mask = int(ran.stdout.strip())
    except ValueError:
        return bisect()
    per = (time.monotonic() - started) / len(members)
    out = {}
    for i, probe in enumerate(members):
        out[probe.id] = result_cls(row(probe, "pass"), per) if mask >> i & 1 else solo(probe, timeout)
    return out


def _fences(
    ir_path: Path, count: int
) -> tuple[list[tuple[int, str, str]], dict[int, list[tuple[int, str, str]]]] | None:
    """Fences from the bundle IR: (module level, per probe index). None when one cannot be attributed.

    Each fence is (line, code, text). Module level fences (an unsupported import) apply to every probe.
    """

    try:
        fences = json.loads(ir_path.read_text(encoding="utf-8"))["fences"]
    except (OSError, ValueError, KeyError):
        return None
    shared: list[tuple[int, str, str]] = []
    own: dict[int, list[tuple[int, str, str]]] = defaultdict(list)
    for fence in fences:
        span = fence.get("span", {})
        entry = (span.get("line", 0), fence["code"], f"{span.get('line', 0)}:{span.get('col', 0)}: {fence['message']}")
        name = fence.get("function")
        if name is None:
            shared.append(entry)
        elif name.startswith("probe_") and name[6:].isdigit() and int(name[6:]) < count:
            own[int(name[6:])].append(entry)
        else:
            return None
    return shared, dict(own)
