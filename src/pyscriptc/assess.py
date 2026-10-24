"""`pyscriptc assess PATH`: evaluate a script, library or app for native gains and island routes.

Throws pyscriptc at any Python script, library or app. Per-file lowering status,
fences grouped by root cause with the nearest typed idiom, per-import ladder
classification, overall native vs island vs blocked shares, island routing lines
for what does not compile, and measured speedups with `--bench` and receipts with
`--verify` on compilable entry points.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from pyscriptc import coverage, imports, verify
from pyscriptc.errors import IRError
from pyscriptc.reflection import mechanism_of, routes_to_island

SCHEMA_VERSION = 1
DEFAULT_TOP_N = 3

STATUS_FULLY = "fully"
STATUS_PARTLY = "partly"
STATUS_REFUSED = "refused"

ROOT_REFLECTION = "reflection"
ROOT_UNTYPED_IMPORT = "untyped-import"
ROOT_UNSUPPORTED = "unsupported-construct"


def is_stdlib_partial(fence: dict[str, Any]) -> bool:
    """Return True if fence is an unlowered attribute of a partially lowered stdlib module."""
    message = str(fence.get("message", ""))
    family = str(fence.get("family", ""))
    return "is not in the lowered subset of" in message or (family.startswith("stdlib:") and family != "queued-stdlib")


def root_cause_of(fence: dict[str, Any]) -> str:
    """Classify a fence into reflection culprit, untyped import, or unsupported construct."""
    message = str(fence.get("message", ""))
    family = str(fence.get("family", ""))
    code = str(fence.get("code", ""))

    if routes_to_island(message) or family == "reflection" or mechanism_of(message) is not None:
        return ROOT_REFLECTION

    if is_stdlib_partial(fence):
        return ROOT_UNSUPPORTED

    if (
        family in ("import-table", "queued-stdlib")
        or fence.get("dependency") is not None
        or "is not in the closed import table" in message
        or (code == "PS2002" and ("import " in message or "circular import" in message or "is not exported" in message))
    ):
        return ROOT_UNTYPED_IMPORT

    return ROOT_UNSUPPORTED


def nearest_typed_idiom(fence: dict[str, Any]) -> str | None:
    """Extract the nearest typed idiom if one exists for this fence."""
    message = str(fence.get("message", ""))
    if "typed idiom: " in message:
        return message.split("typed idiom: ", 1)[1].strip()
    hint = str(fence.get("hint", ""))
    if hint:
        return hint
    return None


def _extract_import_name(fence: dict[str, Any]) -> str:
    """Extract the import name from an import fence."""
    dependency = fence.get("dependency")
    if dependency and dependency.get("distribution"):
        return str(dependency["distribution"])
    if dependency and dependency.get("policy"):
        return str(dependency["policy"])

    statement = fence.get("statement", "")
    if statement:
        try:
            tree = ast.parse(statement)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if node.names:
                        return node.names[0].name.split(".")[0]
                elif isinstance(node, ast.ImportFrom):
                    if node.level and node.level > 0:
                        # Relative imports stay inside the assessed package
                        return ""
                    if node.module:
                        return node.module.split(".")[0]
        except Exception:
            pass

    message = str(fence.get("message", ""))
    match = re.search(r"([\w.]+)\s+is not in the closed import table", message)
    if match:
        return match.group(1).split(".")[0]
    return ""


def _target_package_roots(target: Path, target_files: list[str]) -> set[str]:
    """Return the set of package and module root names defined by the target."""
    roots: set[str] = set()
    target_resolved = target.resolve()
    if target.is_file():
        roots.add(target.stem)
        return roots

    roots.add(target.name)
    roots.add(target_resolved.name)
    for fpath in target_files:
        try:
            rel = Path(fpath).resolve().relative_to(target_resolved)
            parts = rel.parts
            if parts:
                first = parts[0]
                if first.endswith(".py"):
                    roots.add(first[:-3])
                else:
                    roots.add(first)
                if len(parts) > 1 and parts[0] == "src":
                    sub = parts[1]
                    if sub.endswith(".py"):
                        roots.add(sub[:-3])
                    else:
                        roots.add(sub)
        except ValueError:
            pass
    return roots


def _discover_entry_points(target: Path) -> list[Path]:
    """Find runnable entry points in a target."""
    if target.is_file():
        return [target.resolve()]
    candidates: list[Path] = []
    main_py = target / "__main__.py"
    if main_py.is_file():
        candidates.append(main_py.resolve())
    app_main = target / "main.py"
    if app_main.is_file() and app_main.resolve() not in candidates:
        candidates.append(app_main.resolve())
    for py_file in sorted(target.glob("*.py")):
        resolved = py_file.resolve()
        if resolved not in candidates:
            try:
                text = py_file.read_text(encoding="utf-8")
                if '__name__ == "__main__"' in text or "__name__ == '__main__'" in text:
                    candidates.append(resolved)
            except OSError:
                pass
    return candidates


def _oracle_command_for(
    oracle: verify.Oracle,
    target: Path,
    entry: Path,
    argv: list[str],
    call: str | None,
    call_args: list[Any],
    scratch: Path,
) -> tuple[list[str], Path]:
    """Build the CPython oracle command and the cwd to run in."""
    if call is not None:
        return verify.oracle_command(oracle, entry, argv, call, call_args, scratch), entry.parent
    if target.is_dir():
        if (target / "__main__.py").exists():
            return [oracle.executable, str(target), *argv], target.parent
        if (target / "main.py").exists():
            return [oracle.executable, "-m", f"{target.name}.main", *argv], target.parent
    return verify.oracle_command(oracle, entry, argv, call, call_args, scratch), entry.parent


def _run_bench(
    entry_point: Path,
    oracle: verify.Oracle,
    binary: Path,
    argv: list[str],
    stdin: bytes,
    timeout: float = 60.0,
    samples: int = 5,
    cpython_cmd: list[str] | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Time CPython against the native binary on the user's inputs."""
    if cpython_cmd is None:
        cpython_cmd = [oracle.executable, str(entry_point), *argv]
    native_cmd = [str(binary.absolute()), *argv]
    run_cwd = cwd or entry_point.parent

    cpython_times: list[float] = []
    native_times: list[float] = []

    # Interleaved measurement
    for _ in range(samples):
        # Time CPython
        t0 = time.perf_counter()
        c_obs = verify.run_process(cpython_cmd, stdin=stdin, cwd=run_cwd, timeout=timeout)
        t1 = time.perf_counter()
        if c_obs.timed_out:
            return {"entry": str(entry_point), "error": "CPython timed out during benchmark"}
        cpython_times.append(t1 - t0)

        # Time native binary
        t0 = time.perf_counter()
        n_obs = verify.run_process(native_cmd, stdin=stdin, cwd=run_cwd, timeout=timeout)
        t1 = time.perf_counter()
        if n_obs.timed_out:
            return {"entry": str(entry_point), "error": "native binary timed out during benchmark"}
        native_times.append(t1 - t0)

    cpython_sorted = sorted(cpython_times)
    native_sorted = sorted(native_times)
    mid = samples // 2
    c_med = cpython_sorted[mid] if samples % 2 else (cpython_sorted[mid - 1] + cpython_sorted[mid]) / 2
    n_med = native_sorted[mid] if samples % 2 else (native_sorted[mid - 1] + native_sorted[mid]) / 2

    ratio = c_med / max(n_med, 1e-9)
    return {
        "entry": str(entry_point),
        "samples": samples,
        "cpythonMs": round(c_med * 1000, 2),
        "nativeMs": round(n_med * 1000, 2),
        "ratio": round(ratio, 2),
        "speedup": f"{ratio:.1f}x CPython",
    }


def assess(
    target: Path,
    *,
    bench: bool = False,
    verify_diff: bool = False,
    run_argv: list[str] | None = None,
    stdin_path: str | None = None,
    call: str | None = None,
    call_args: list[Any] | None = None,
    timeout: float = verify.DEFAULT_TIMEOUT,
    compile_args: Any = None,
) -> dict[str, Any]:
    """Run full assessment on target."""
    target = Path(target)
    if not target.exists():
        raise IRError(f"source not found: {target}")
    target_resolved = target.resolve()

    argv = list(run_argv or [])
    stdin = Path(stdin_path).read_bytes() if stdin_path and Path(stdin_path).exists() else b""

    # 1. Run coverage
    try:
        cov_report = coverage.build_report(target)
    except Exception as exc:
        cov_report = {
            "schemaVersion": 2,
            "target": str(target),
            "summary": {
                "files": 0,
                "definitions": 0,
                "nativeDefinitions": 0,
                "fences": 0,
                "rootFences": 0,
            },
            "fences": [],
            "definitions": [],
            "capabilities": {
                "byFile": [],
                "totals": {"files": 0, "definitions": 0, "native": 0, "fenced": 0, "island": 0},
            },
            "error": str(exc),
        }

    # 2. Run imports
    try:
        imp_report = imports.build_report(target)
    except Exception as exc:
        imp_report = {
            "schemaVersion": 2,
            "target": str(target),
            "summary": {"imports": 0, "statements": 0, "byLane": {}, "byLadder": {}, "byCapability": {}},
            "imports": [],
            "error": str(exc),
        }

    # 3. Gather files
    capabilities_by_file = {
        str(Path(item["file"]).resolve()): item for item in cov_report.get("capabilities", {}).get("byFile", [])
    }
    if target.is_file():
        target_files = [str(target.resolve())]
    else:
        discovered_set = {str(Path(p).resolve()) for p in capabilities_by_file.keys()}
        for py_path in target.rglob("*.py"):
            discovered_set.add(str(py_path.resolve()))
        target_files = sorted(discovered_set)

    fences_by_file: dict[str, list[dict[str, Any]]] = {}
    for fence in cov_report.get("fences", []):
        fpath = str(Path(fence["file"]).resolve())
        fences_by_file.setdefault(fpath, []).append(fence)

    file_results: list[dict[str, Any]] = []
    untyped_imports_needed: dict[str, dict[str, Any]] = {}
    stays_blocked: list[dict[str, Any]] = []
    package_roots = _target_package_roots(target, target_files)

    for fpath in target_files:
        fpath_resolved = str(Path(fpath).resolve())
        cap = capabilities_by_file.get(fpath_resolved, {})
        local_fences = fences_by_file.get(fpath_resolved, [])

        total_defs = cap.get("definitions", 0)
        native_defs = cap.get("native", 0)
        fenced_defs = cap.get("fenced", 0)
        island_defs = cap.get("island", 0)
        fence_count = len(local_fences)

        if not cap and fence_count == 0:
            try:
                tree = ast.parse(Path(fpath_resolved).read_text(encoding="utf-8"), filename=fpath_resolved)
                defs = coverage._definitions(tree)
                total_defs = len(defs)
                native_defs = total_defs
            except Exception:
                pass

        has_error = bool(cov_report.get("error"))
        if has_error:
            status = STATUS_REFUSED
            local_fences = [
                {
                    "code": "PS3001",
                    "message": cov_report["error"],
                    "family": "construct-other",
                    "line": 1,
                    "col": 1,
                    "statement": "",
                }
            ]
            fence_count = 1
        elif fence_count == 0:
            status = STATUS_FULLY
        elif native_defs > 0:
            status = STATUS_PARTLY
        else:
            status = STATUS_REFUSED

        # Relativize fpath for clean display
        if target.is_file():
            rel_path = target.name
        else:
            try:
                rel_path = os.path.relpath(fpath_resolved, target_resolved)
            except ValueError:
                rel_path = fpath

        grouped: dict[str, list[dict[str, Any]]] = {
            ROOT_REFLECTION: [],
            ROOT_UNTYPED_IMPORT: [],
            ROOT_UNSUPPORTED: [],
        }

        for fence in local_fences:
            is_not_exported = "is not exported" in fence.get("message", "")
            is_partial_stdlib = is_stdlib_partial(fence)
            root_cause = ROOT_UNSUPPORTED if is_partial_stdlib else root_cause_of(fence)
            idiom = nearest_typed_idiom(fence)
            culprit = mechanism_of(fence.get("message", "")) if root_cause == ROOT_REFLECTION else None
            import_name = _extract_import_name(fence) if root_cause == ROOT_UNTYPED_IMPORT else ""

            # Filter out imports whose root is in the assessed package itself
            is_own_pkg = bool(import_name and import_name.split(".")[0] in package_roots)
            if is_own_pkg or is_not_exported or is_partial_stdlib:
                route_import_name = ""
            else:
                route_import_name = import_name

            item = {
                "code": fence.get("code", ""),
                "message": fence.get("message", ""),
                "line": fence.get("line", 1),
                "col": fence.get("col", 1),
                "definition": fence.get("definition"),
                "statement": fence.get("statement", ""),
                "rootCause": root_cause,
                "culprit": culprit,
                "typedIdiom": idiom,
                "importName": route_import_name,
                "nameMissing": is_not_exported,
                "stdlibPartial": is_partial_stdlib,
            }
            grouped[root_cause].append(item)

            if root_cause == ROOT_UNTYPED_IMPORT and route_import_name:
                if route_import_name not in untyped_imports_needed:
                    untyped_imports_needed[route_import_name] = {
                        "name": route_import_name,
                        "policyLine": f'{route_import_name} = "island"',
                        "construct": fence.get("statement") or f"import {route_import_name}",
                        "file": rel_path,
                        "line": fence.get("line", 1),
                    }
            elif root_cause == ROOT_UNSUPPORTED:
                reason = (
                    "partially lowered stdlib module keeps fence with named idiom"
                    if is_partial_stdlib
                    else "unsupported construct outside island routing"
                )
                stays_blocked.append(
                    {
                        "file": rel_path,
                        "line": fence.get("line", 1),
                        "code": fence.get("code", ""),
                        "family": fence.get("family", ""),
                        "message": fence.get("message", ""),
                        "hint": idiom,
                        "reason": reason,
                    }
                )

        file_results.append(
            {
                "file": rel_path,
                "absolutePath": fpath,
                "status": status,
                "definitions": total_defs,
                "native": native_defs,
                "fenced": fenced_defs,
                "island": island_defs,
                "fences": fence_count,
                "groupedFences": grouped,
            }
        )

    # 4. Summary calculation
    cap_totals = cov_report.get("capabilities", {}).get("totals", {})
    tot_defs = max(cap_totals.get("definitions", 0), sum(f["definitions"] for f in file_results))
    nat_defs = max(cap_totals.get("native", 0), sum(f["native"] for f in file_results))
    isl_defs = max(cap_totals.get("island", 0), sum(f["island"] for f in file_results))
    blk_defs = max(cap_totals.get("fenced", 0), sum(f["fenced"] for f in file_results))

    files_fully = sum(1 for f in file_results if f["status"] == STATUS_FULLY)
    files_partly = sum(1 for f in file_results if f["status"] == STATUS_PARTLY)
    files_refused = sum(1 for f in file_results if f["status"] == STATUS_REFUSED)

    if tot_defs > 0:
        share_nat = round(nat_defs / tot_defs, 4)
        share_isl = round(isl_defs / tot_defs, 4)
        share_blk = round(blk_defs / tot_defs, 4)
    else:
        # Fall back to file counts when there are no class/function definitions
        if file_results:
            if files_fully == len(file_results):
                share_nat, share_isl, share_blk = 1.0, 0.0, 0.0
            else:
                has_island = any(f["groupedFences"][ROOT_REFLECTION] for f in file_results)
                if has_island:
                    share_nat, share_isl, share_blk = 0.0, 1.0, 0.0
                else:
                    share_nat, share_isl, share_blk = 0.0, 0.0, 1.0
        else:
            share_nat, share_isl, share_blk = 0.0, 0.0, 0.0

    # 5. Imports classification
    assessed_imports = []
    for item in imp_report.get("imports", []):
        imp_name = item["name"]
        imp_root = imp_name.split(".")[0]
        is_own = imp_root in package_roots

        cap = item.get("capability", {})
        routing = cap.get("routing", "native")
        codes = cap.get("codes", [])

        if not is_own and (routing == "island" or imp_name in untyped_imports_needed):
            effective_routing = "island"
            ladder_text = "island (requires libpython)"
            requires_libpython = True
        elif routing == "fenced" and codes:
            effective_routing = "fenced"
            ladder_text = f"native: fences ({', '.join(codes)})"
            requires_libpython = item.get("requiresLibpython", False)
        else:
            effective_routing = "native"
            ladder_text = "native"
            requires_libpython = False

        assessed_imports.append(
            {
                "name": imp_name,
                "ladder": ladder_text,
                "routing": effective_routing,
                "codes": codes,
                "requiresLibpython": requires_libpython,
                "distribution": item.get("distribution", ""),
            }
        )
        if not is_own and routing != "native" and imp_name not in untyped_imports_needed:
            if imp_name not in sys.stdlib_module_names:
                untyped_imports_needed[imp_name] = {
                    "name": imp_name,
                    "policyLine": f'{imp_name} = "island"',
                    "construct": f"import {imp_name}",
                    "file": item.get("sites", [{}])[0].get("file", ""),
                    "line": item.get("sites", [{}])[0].get("line", 1),
                }

    # 6. Island Routing
    policy_lines = ["[tool.pyscriptc.imports]"]
    for route in sorted(untyped_imports_needed.values(), key=lambda r: r["name"]):
        policy_lines.append(f'{route["name"]} = "island"')

    island_routing = {
        "policyLines": policy_lines if len(policy_lines) > 1 else [],
        "routes": sorted(untyped_imports_needed.values(), key=lambda r: r["name"]),
        "staysBlocked": stays_blocked,
    }

    total_imports = len(assessed_imports)
    isl_imports = sum(1 for i in assessed_imports if i["routing"] == "island")
    nat_imports = sum(1 for i in assessed_imports if i["routing"] == "native")
    fnc_imports = sum(1 for i in assessed_imports if i["routing"] == "fenced")
    share_import_island = round(isl_imports / total_imports, 4) if total_imports > 0 else 0.0

    # 7. Compilable entry points
    entry_points = _discover_entry_points(target)
    compilable_entries: list[Path] = []
    for entry in entry_points:
        entry_resolved = str(entry.resolve())
        matching_file = next((f for f in file_results if f["absolutePath"] == entry_resolved), None)
        if matching_file is not None:
            if matching_file["status"] == STATUS_FULLY:
                compilable_entries.append(entry)
        elif not file_results or all(f["status"] == STATUS_FULLY for f in file_results):
            compilable_entries.append(entry)

    oracle: verify.Oracle | None = None
    if bench or verify_diff:
        try:
            oracle = verify.find_oracle()
        except Exception:
            oracle = None

    verify_receipts: list[dict[str, Any]] = []
    bench_results: list[dict[str, Any]] = []

    if verify_diff or bench:
        for entry in compilable_entries:
            if oracle is None:
                err_msg = "no CPython 3.14 oracle available: set PYSC_ORACLE or run uv python install 3.14"
                if verify_diff:
                    verify_receipts.append({"entry": str(entry), "error": err_msg})
                if bench:
                    bench_results.append({"entry": str(entry), "error": err_msg})
                continue

            # Build binary for run
            key = f"assess-{entry.stem}-{entry.stat().st_mtime_ns}"
            from pyscriptc import cli

            is_dir_target = target.is_dir()
            if is_dir_target:
                compile_source = str(target.resolve())
                program_mode = True
                name = target.resolve().name or "app"
            else:
                compile_source = str(entry.resolve())
                program_mode = False
                name = entry.stem or "app"

            with tempfile.TemporaryDirectory(prefix="pyscriptc-assess-") as scratch_name:
                scratch = Path(scratch_name)
                bin_path = scratch / name

                c_args = compile_args or argparse.Namespace(
                    source=compile_source,
                    program=program_mode,
                    call=call,
                    args=repr(call_args or []),
                    driver=None,
                    target=None,
                    linker=None,
                    sanitize=None,
                    debug_info=False,
                    skip_check=True,
                    checker="pyrefly",
                    no_cache=False,
                    ffi=[],
                )
                c_args.source = compile_source
                c_args.program = program_mode
                try:
                    binary, cache_hit, code, compile_log = cli._verify_binary(c_args, name, key, scratch)
                except Exception as exc:
                    code = 1
                    compile_log = str(exc)
                    binary = bin_path

                if code != 0:
                    fail_rec = verify.failure_receipt(str(entry), oracle, code, compile_log, timeout_seconds=timeout)
                    if verify_diff:
                        verify_receipts.append(fail_rec)
                    if bench:
                        bench_results.append({"entry": str(entry), "error": f"build failed with exit {code}"})
                    continue

                cpython_cmd, run_cwd = _oracle_command_for(oracle, target, entry, argv, call, call_args, scratch)

                if verify_diff:
                    started = time.monotonic()
                    expected = verify.run_process(cpython_cmd, stdin=stdin, cwd=run_cwd, timeout=timeout)
                    native = verify.run_process(
                        [str(binary.absolute()), *argv], stdin=stdin, cwd=run_cwd, timeout=timeout
                    )
                    checks = verify.compare(expected, native)
                    call_entry = None
                    if call is not None:
                        call_entry = {
                            "function": call,
                            "args": call_args or [],
                            "match": checks[0]["match"],
                            "comparedIn": "stdout",
                        }
                    receipt = verify.Receipt(
                        source=str(entry),
                        oracle=oracle,
                        binary=str(binary),
                        binary_bytes=verify._binary_size(binary),
                        argv=list(argv),
                        stdin_path=stdin_path,
                        stdin_bytes=len(stdin),
                        checks=checks,
                        call=call_entry,
                        cache_hit=cache_hit,
                        compile_ms=0,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        timeout_seconds=timeout,
                    )
                    verify_receipts.append(receipt.as_dict())

                if bench:
                    b_res = _run_bench(
                        entry,
                        oracle,
                        binary,
                        argv,
                        stdin,
                        timeout=timeout,
                        cpython_cmd=cpython_cmd,
                        cwd=run_cwd,
                    )
                    bench_results.append(b_res)

    summary = {
        "files": {
            "total": len(file_results),
            "fully": files_fully,
            "partly": files_partly,
            "refused": files_refused,
        },
        "definitions": {
            "total": tot_defs,
            "native": nat_defs,
            "island": isl_defs,
            "blocked": blk_defs,
        },
        "shares": {
            "native": share_nat,
            "island": share_isl,
            "blocked": share_blk,
            "importIsland": share_import_island,
        },
        "imports": {
            "total": total_imports,
            "native": nat_imports,
            "fenced": fnc_imports,
            "island": isl_imports,
            "share": share_import_island,
        },
        "requiresLibpython": any(i["requiresLibpython"] or i["routing"] == "island" for i in assessed_imports)
        or isl_defs > 0,
    }

    micro_mismatch = (oracle.version != verify.TARGET_CPYTHON) if oracle is not None else False

    return {
        "schemaVersion": SCHEMA_VERSION,
        "target": str(target),
        "targetVersion": verify.TARGET_CPYTHON,
        "microMismatch": micro_mismatch,
        "summary": summary,
        "files": file_results,
        "imports": assessed_imports,
        "islandRouting": island_routing,
        "compilableEntries": [str(e) for e in compilable_entries],
        "verify": verify_receipts if verify_diff else None,
        "bench": bench_results if bench else None,
    }


def render_text(
    report: dict[str, Any],
    *,
    show_all: bool = False,
    top_n: int = DEFAULT_TOP_N,
) -> str:
    """Format the assessment report as human-readable text."""
    lines: list[str] = [f"assess {report['target']}:", ""]

    summary = report["summary"]
    shares = summary["shares"]
    defs = summary["definitions"]
    files = summary["files"]
    imps = summary["imports"]

    lines.append("Summary:")
    if defs["total"] > 0:
        lines.append(f"  Native:   {shares['native'] * 100:5.1f}% ({defs['native']} of {defs['total']} definitions)")
        import_note = f", {shares['importIsland'] * 100:5.1f}% import-level" if imps["total"] > 0 else ""
        lines.append(
            f"  Island:   {shares['island'] * 100:5.1f}% ({defs['island']} of {defs['total']} definitions{import_note})"
        )
        lines.append(f"  Blocked:  {shares['blocked'] * 100:5.1f}% ({defs['blocked']} of {defs['total']} definitions)")
    else:
        status_word = "compiles natively" if shares["native"] == 1.0 else "fenced"
        lines.append(f"  Native:   {shares['native'] * 100:5.1f}% ({files['total']} files, {status_word})")
        import_note = f" ({shares['importIsland'] * 100:5.1f}% import-level)" if imps["total"] > 0 else ""
        lines.append(f"  Island:   {shares['island'] * 100:5.1f}%{import_note}")
        lines.append(f"  Blocked:  {shares['blocked'] * 100:5.1f}%")

    lines.append(
        f"  Files:    {files['total']} "
        f"({files['fully']} lowers fully, {files['partly']} partly, {files['refused']} refused)"
    )
    lines.append(
        f"  Imports:  {imps['total']} ({imps['native']} native, {imps['fenced']} fenced, {imps['island']} island)"
    )
    lines.append("")

    lines.append("Files:")
    files_list = report["files"]
    if not show_all and len(files_list) > 10:
        display_files = files_list[:10]
        files_overflow = len(files_list) - 10
    else:
        display_files = files_list
        files_overflow = 0

    for f in display_files:
        status_label = f"lowers {f['status']}" if f["status"] in (STATUS_FULLY, STATUS_PARTLY) else STATUS_REFUSED
        detail = (
            f"({f['native']} native, {f['fenced']} fenced, {f['island']} island "
            f"of {f['definitions']} definitions, {f['fences']} fences)"
            if f["definitions"] > 0
            else f"({f['fences']} fences)"
        )
        lines.append(f"  {f['file']}: {status_label} {detail}")

        grouped = f["groupedFences"]
        for root_cause, label in (
            (ROOT_REFLECTION, "reflection"),
            (ROOT_UNTYPED_IMPORT, "untyped import"),
            (ROOT_UNSUPPORTED, "unsupported construct"),
        ):
            items = grouped[root_cause]
            if not items:
                continue
            lines.append(f"    {label}:")
            if not show_all and len(items) > top_n:
                to_show = items[:top_n]
                overflow = len(items) - top_n
            else:
                to_show = items
                overflow = 0

            for item in to_show:
                if root_cause == ROOT_REFLECTION:
                    culprit_str = f"{item['culprit']}: " if item["culprit"] else ""
                    lines.append(f"      - line {item['line']}: {culprit_str}{item['message']}")
                    if item["typedIdiom"]:
                        lines.append(f"        typed idiom: {item['typedIdiom']}")
                elif root_cause == ROOT_UNTYPED_IMPORT:
                    lines.append(f"      - line {item['line']}: {item['message']}")
                    if item.get("nameMissing") or "is not exported" in item.get("message", ""):
                        lines.append("        name is missing")
                    elif item.get("importName"):
                        lines.append(f'        island route: {item["importName"]} = "island"')
                else:  # ROOT_UNSUPPORTED
                    lines.append(f"      - line {item['line']}: {item['code']} {item['message']}")
                    if item["typedIdiom"]:
                        lines.append(f"        typed idiom: {item['typedIdiom']}")
                    if item.get("stdlibPartial"):
                        lines.append(
                            "        partially lowered stdlib module keeps fence with named idiom (no island route)"
                        )

            if overflow > 0:
                lines.append(f"        ... and {overflow} more {label} fence(s) (use --all for full list)")

    if files_overflow > 0:
        lines.append(f"  ... and {files_overflow} more file(s) (use --all for full list)")

    lines.append("")

    if report["imports"]:
        lines.append("Imports:")
        imps_list = report["imports"]
        if not show_all and len(imps_list) > 10:
            display_imps = imps_list[:10]
            imps_overflow = len(imps_list) - 10
        else:
            display_imps = imps_list
            imps_overflow = 0

        for imp in display_imps:
            lines.append(f"  {imp['name']}: {imp['ladder']}")
        if imps_overflow > 0:
            lines.append(f"  ... and {imps_overflow} more import(s) (use --all for full list)")
        lines.append("")

    island_routing = report["islandRouting"]
    lines.append("Path to the island:")
    if island_routing["policyLines"]:
        lines.append("  Add to pyproject.toml:")
        for line in island_routing["policyLines"]:
            lines.append(f"    {line}")
        if island_routing["routes"]:
            for route in island_routing["routes"]:
                lines.append(f"    # forced by: {route['construct']} ({route['file']}:{route['line']})")
    else:
        lines.append("  (no island routes needed)")

    lines.append("")
    lines.append("  Stays blocked (cannot route to island):")
    if island_routing["staysBlocked"]:
        blocked_list = island_routing["staysBlocked"]
        if not show_all and len(blocked_list) > top_n:
            display_blocked = blocked_list[:top_n]
            blocked_overflow = len(blocked_list) - top_n
        else:
            display_blocked = blocked_list
            blocked_overflow = 0

        for blk in display_blocked:
            hint_str = f" - {blk['hint']}" if blk["hint"] else ""
            reason_str = (
                f" ({blk['reason']})"
                if blk.get("reason") and blk["reason"] != "unsupported construct outside island routing"
                else ""
            )
            lines.append(f"    {blk['file']}:{blk['line']}: {blk['code']} {blk['family']}{hint_str}{reason_str}")

        if blocked_overflow > 0:
            lines.append(f"    ... and {blocked_overflow} more blocked construct(s) (use --all for full list)")
    else:
        lines.append("    (none)")

    if report["verify"] is not None:
        lines.append("")
        lines.append("Verify:")
        if not report["verify"]:
            lines.append("  (no compilable entry point to verify)")
        for rec in report["verify"]:
            if "error" in rec and "output" not in rec.get("error", {}):
                lines.append(f"  {rec.get('entry', report['target'])}: {rec['error']}")
            elif "checks" in rec:
                from pyscriptc.verify import Oracle, Receipt

                receipt_obj = Receipt(
                    source=rec["source"],
                    oracle=Oracle(rec["oracle"]["executable"], rec["oracle"]["version"]),
                    binary=rec["native"]["binary"],
                    binary_bytes=rec["native"]["bytes"],
                    argv=rec["argv"],
                    stdin_path=rec["stdin"]["path"],
                    stdin_bytes=rec["stdin"]["bytes"],
                    checks=rec["checks"],
                    call=rec.get("call"),
                    cache_hit=rec["compile"]["cacheHit"],
                    compile_ms=rec["compile"]["milliseconds"],
                    duration_ms=rec["durationMs"],
                    timeout_seconds=rec["timeoutSeconds"],
                )
                rendered = verify.render_text(receipt_obj)
                for line in rendered.splitlines():
                    lines.append(f"  {line}")
            elif "error" in rec:
                rendered = verify.render_failure(rec)
                for line in rendered.splitlines():
                    lines.append(f"  {line}")

    if report["bench"] is not None:
        lines.append("")
        lines.append("Benchmark:")
        if not report["bench"]:
            lines.append("  (no compilable entry point to benchmark)")
        for b in report["bench"]:
            if "error" in b:
                lines.append(f"  {b['entry']}: skipped ({b['error']})")
            else:
                lines.append(
                    f"  {b['entry']}: {b['speedup']} "
                    f"(median native {b['nativeMs']}ms vs cpython {b['cpythonMs']}ms, {b['samples']} samples)"
                )

    return "\n".join(lines)
