"""EXPERIMENTAL: engine-capability manifest, prototype of design section C.

Two subcommands:

    PYTHON_OF_THE_ENGINE scripts/engine_manifest.py probe [--reference FILE] > manifest.json
    uv run python scripts/engine_manifest.py check MANIFEST.json PROGRAM.py

`probe` runs INSIDE the engine being described (CPython, PyPy, GraalPy, or any
interpreter that runs Python 3.8+ source). It records the engine identity, which
modules of the reference stdlib list resolve, and which language features compile.
The reference list is the 3.14 `sys.stdlib_module_names` of the interpreter that
writes it (`--reference` takes a JSON list produced by `refs`); without one the
probing interpreter's own list is used.

`check` runs on the host and reports, per import of PROGRAM.py, whether the
manifest's engine can resolve it. Exit code 1 when anything is unsupported.
A third party owns the manifest of its engine; this file only fixes the shape.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import platform
import sys
import sysconfig

SCHEMA = 1

# One snippet per feature; `compile()` decides, nothing is executed.
FEATURES = {
    "walrus_pep572": "(x := 1)",
    "match_pep634": "match 1:\n case 1: pass",
    "except_star_pep654": "try:\n pass\nexcept* ValueError:\n pass",
    "type_params_pep695": "def f[T](x: T) -> T: return x",
    "fstring_nesting_pep701": "x = 1\nf'{f\"{x}\"}'",
    "tstring_pep750": "x = 1\nt'{x}'",
    "except_no_parens_pep758": "try:\n pass\nexcept ValueError, TypeError:\n pass",
    "paren_with_pep617": "with (open('a'), open('b')): pass",
}


def probe(reference: list[str]) -> dict:
    available: list[str] = []
    missing: list[str] = []
    builtin = set(sys.builtin_module_names)
    for name in sorted(reference):
        if name in builtin:
            available.append(name)
            continue
        try:
            found = importlib.util.find_spec(name) is not None
        except Exception:
            found = False
        (available if found else missing).append(name)
    features = {}
    for key, snippet in FEATURES.items():
        try:
            compile(snippet, "<probe>", "exec")
            features[key] = True
        except SyntaxError:
            features[key] = False
        except Exception:
            features[key] = False
    try:  # PEP 649 is a runtime semantic: annotations are lazy, so an undefined name is fine
        exec(compile("def f(x: _Undefined_pep649): pass", "<probe>", "exec", dont_inherit=True), {})
        features["deferred_annotations_pep649"] = True
    except Exception:
        features["deferred_annotations_pep649"] = False
    return {
        "schema": SCHEMA,
        "engine": {
            "name": platform.python_implementation().lower(),
            "version": platform.python_version(),
            "language_level": "{}.{}".format(*sys.version_info[:2]),
            "platform": sysconfig.get_platform(),
            "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        },
        "stdlib": {"available": available, "missing": missing},
        "features": features,
    }


def check(manifest: dict, program: str) -> int:
    tree = ast.parse(open(program, encoding="utf-8").read())
    missing = set(manifest["stdlib"]["missing"])
    known = set(manifest["stdlib"]["available"]) | missing
    bad = 0
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [(node.module, node.lineno)]
        for full, line in names:
            root = full.split(".")[0]
            if root in missing:
                eng = manifest["engine"]
                where = f"{eng['name']} {eng['version']}"
                print(f"{program}:{line}: `{full}` is not available on {where} (stdlib module missing)")
                bad += 1
            elif root not in known:
                continue  # third party: not the stdlib manifest's call
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--reference", help="JSON list of reference stdlib module names")
    sub.add_parser("refs", help="print this interpreter's sys.stdlib_module_names as JSON")
    c = sub.add_parser("check")
    c.add_argument("manifest")
    c.add_argument("program")
    args = ap.parse_args()
    if args.cmd == "refs":
        json.dump(sorted(sys.stdlib_module_names), sys.stdout)
        return 0
    if args.cmd == "probe":
        ref = json.load(open(args.reference)) if args.reference else sorted(sys.stdlib_module_names)
        json.dump(probe(ref), sys.stdout, indent=1, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    return check(json.load(open(args.manifest)), args.program)


if __name__ == "__main__":
    raise SystemExit(main())
