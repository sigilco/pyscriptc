"""Unannotated module-scope containers are constants: mutation needs a fence (#525).

A module constant re-emits its literal on every read, so the name has no
object identity: `xs.sort()` sorts a throwaway, `xs[i] = v` writes a
throwaway, and `ys = xs` freezes the copy at bind time. Each of those used
to lower silently; now each stops the compile with a PS2002 that names the
constant, the reason the lowered form cannot match CPython, and the
substitute (annotate the name so it takes a global slot, #512). Reads still
lower, so `print`, `in`, `.index` and friends on the same constants are
unchanged.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gates import run_cli, write_case


def _fences(tmp_path: Path, name: str, source: str, *texts: str) -> None:
    path = write_case(tmp_path, name, source)
    got = run_cli("compile", str(path), "--skip-check", "--emit", "llvm", "-o", str(tmp_path / f"{name}.ll"))
    assert got.returncode == 3, f"{name}\n{got.stdout}{got.stderr}"
    for text in texts:
        assert text in got.stderr, f"{name}\n{got.stderr}"


def test_mutating_call_on_module_constant_fences(tmp_path: Path) -> None:
    cases = [
        ("msort.py", "xs = [3, 1, 2]\nxs.sort()\n", "xs.sort", "xs: list[int]"),
        ("mpop.py", "xs = [3, 1, 2]\nxs.pop()\n", "xs.pop", "xs: list[int]"),
        ("mext.py", "xs = [3, 1, 2]\nxs.extend([4])\n", "xs.extend", "xs: list[int]"),
        ("mrev.py", "xs = [3, 1, 2]\nxs.reverse()\n", "xs.reverse", "xs: list[int]"),
        ("mapp.py", "xs = [3, 1, 2]\nxs.append(4)\n", "xs.append", "xs: list[int]"),
        ("mdup.py", 'd = {"a": 1}\nd.update({"b": 2})\n', "d.update", "d: dict[str, int]"),
        ("mdpop.py", 'd = {"a": 1}\nd.pop("a")\n', "d.pop", "d: dict[str, int]"),
        ("mdsd.py", 'd = {"a": 1}\nd.setdefault("b", 2)\n', "d.setdefault", "d: dict[str, int]"),
        ("msadd.py", "s = {1, 2}\ns.add(3)\n", "s.add", "s: set[int]"),
    ]
    for name, source, method, annotation in cases:
        _fences(
            tmp_path,
            name,
            source,
            "module-scope constant",
            "every read is a fresh copy",
            method,
            f"annotate {annotation.split(':')[0]} ({annotation} = ...)",
        )


def test_mutating_call_inside_function_fences(tmp_path: Path) -> None:
    _fences(
        tmp_path,
        "fnsort.py",
        "xs = [3, 1, 2]\n\n\ndef f() -> None:\n    xs.sort()\n\n\nf()\n",
        "module-scope constant",
        "xs.sort",
    )


def test_item_store_on_module_constant_fences(tmp_path: Path) -> None:
    _fences(
        tmp_path,
        "store.py",
        "xs = [1, 2]\n\n\ndef f() -> None:\n    xs[0] = 9\n\n\nf()\n",
        "module-scope constant",
        "item store",
        "annotate xs (xs: list[int] = ...)",
    )


def test_nested_read_through_module_constant_fences(tmp_path: Path) -> None:
    _fences(
        tmp_path,
        "nestsort.py",
        "xs = [[3, 1]]\nxs[0].sort()\n",
        "xs is a module-scope constant",
    )
    _fences(
        tmp_path,
        "neststore.py",
        'd = {"k": [1]}\n\n\ndef f() -> None:\n    d["k"][0] = 9\n\n\nf()\n',
        "d is a module-scope constant",
    )


def test_copy_on_alias_fences(tmp_path: Path) -> None:
    # The trailing print makes each module a script, so `xs` binds as a
    # constant; in a definition-only module it would take a global slot.
    _fences(
        tmp_path,
        "alias.py",
        "xs = [1, 2]\nys = xs\nprint(len(ys))\n",
        "ys = xs copies the module-scope constant xs",
        "a copy-on-alias is not representable",
        "annotate xs (xs: list[int] = ...)",
    )
    _fences(
        tmp_path,
        "annalias.py",
        "xs = [1, 2]\nys: list[int] = xs\nprint(len(ys))\n",
        "ys = xs copies the module-scope constant xs",
    )
    _fences(
        tmp_path,
        "chain.py",
        "xs = [1, 2]\nx = y = xs\nprint(len(y))\n",
        "copy-on-alias is not representable",
    )


def test_copy_on_alias_inside_function_fences(tmp_path: Path) -> None:
    _fences(
        tmp_path,
        "fnalias.py",
        "xs = [1, 2]\n\n\ndef f() -> None:\n    ys = xs\n\n\nf()\n",
        "ys = xs copies the module-scope constant xs",
    )


def test_reads_of_module_constant_still_lower(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "reads.py",
        'xs = [3, 1, 2]\nd = {"a": 9}\nprint(xs.index(1), xs.count(3), len(xs), d["a"], "a" in d, 2 in xs)\n',
    )
    got = run_cli("compile", str(path), "--run")
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "1 1 3 9 True True"


def test_diverted_names_still_lower(tmp_path: Path) -> None:
    # A module name the script rebinding-marks lowers inside the entry as a
    # local, so an item store on it keeps landing on the real container.
    path = write_case(tmp_path, "div.py", 'd = {"a": 1}\nd["b"] = 2\nprint(d["a"], d["b"], len(d))\n')
    got = run_cli("compile", str(path), "--run")
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "1 2 2"
