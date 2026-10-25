"""EXPERIMENTAL: build an island program that runs with no system Python.

    uv run python scripts/embed_poc.py --prefix PBS/python/install PROGRAM.py OUTDIR

`--prefix` is the `install/` tree of a python-build-standalone `*-full` archive
(any relocatable CPython prefix with `include/pythonX.Y` and
`lib/libpythonX.Y.dylib|so` works). The script compiles PROGRAM.py with
`--island`, links it against that prefix (the `PYSC_EXPERIMENTAL_EMBED_PREFIX`
seam in `pyscriptc.boxed.embed_link_flags`), then writes a self-contained
bundle:

    OUTDIR/bin/NAME            the island binary (rpath ../lib)
    OUTDIR/lib/libpythonX.Y.*  the interpreter
    OUTDIR/lib/pythonXY.zip    the trimmed stdlib, .pyc only, stored (no zlib needed), import closure

Nothing here runs on a default compile. Findings: .agents/plans/2026-10-25-embedding-libpython.md
"""

from __future__ import annotations

import argparse
import ast
import modulefinder
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# Imported by interpreter startup or by codecs at runtime, never visible in the
# program source: the closure must include them.
STARTUP = ["site", "io", "os", "encodings.utf_8", "encodings.ascii", "encodings.latin_1", "encodings.idna"]


def top_imports(source: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def closure(mods: set[str], stdlib: Path, dynload: Path) -> tuple[dict[str, Path], list[Path]]:
    """Return ({module name: source file}, [extension .so files in lib-dynload])."""
    finder = modulefinder.ModuleFinder(path=[str(stdlib), str(dynload)])
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "probe.py"
        script.write_text("".join(f"import {m}\n" for m in sorted(mods | set(STARTUP))), encoding="utf-8")
        finder.run_script(str(script))
    files: dict[str, Path] = {}
    ext: list[Path] = []
    for name, module in finder.modules.items():
        path = getattr(module, "__file__", None)
        if path and Path(path).suffix == ".py" and stdlib in Path(path).parents:
            files[name] = Path(path)
    # The host interpreter's builtin list decides what modulefinder calls an
    # extension, so match by name against the target's lib-dynload instead.
    wanted = set(finder.modules) | set(finder.badmodules)
    for so in sorted(dynload.glob("*.so")):
        stem = so.name.split(".")[0]
        if stem in wanted and "_test" not in stem and "tkinter" not in stem:
            ext.append(so)
    return files, ext


def write_zip(files: dict[str, Path], dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as zf, tempfile.TemporaryDirectory() as td:
        for name, src in sorted(files.items()):
            is_pkg = src.name == "__init__.py"
            arc = name.replace(".", "/") + ("/__init__.pyc" if is_pkg else ".pyc")
            pyc = Path(td) / (name + ".pyc")
            py_compile.compile(str(src), cfile=str(pyc), doraise=True, optimize=2)
            zf.write(pyc, arc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prefix", required=True, type=Path)
    ap.add_argument("--extra", action="append", default=[], help="extra stdlib module to include")
    ap.add_argument(
        "--trim-by-run",
        action="store_true",
        help="run the binary once and keep only the modules it imported (unsafe if a run takes other paths)",
    )
    ap.add_argument("program", type=Path)
    ap.add_argument("outdir", type=Path)
    args = ap.parse_args()
    args.program = args.program.resolve()
    prefix = args.prefix.resolve()
    version = next((prefix / "include").glob("python3.*")).name.removeprefix("python")
    stdlib = prefix / "lib" / f"python{version}"
    dynload = stdlib / "lib-dynload"
    out = args.outdir.resolve()
    (out / "bin").mkdir(parents=True, exist_ok=True)
    (out / "lib").mkdir(parents=True, exist_ok=True)
    binary = out / "bin" / args.program.stem
    env = {**os.environ, "PYSC_EXPERIMENTAL_EMBED_PREFIX": str(prefix)}
    cmd = ["pyscriptc", "compile", "--island", str(args.program), "--emit", "bin", "-o", str(binary)]
    subprocess.run(cmd, check=True, env=env, cwd=args.program.resolve().parent)
    for stray in out.joinpath("bin").glob(f"{binary.name}.*"):
        stray.unlink()  # compile intermediates (.ll, .o, .ir.json, .report.json)
    for lib in sorted((prefix / "lib").glob(f"libpython{version}.*")):
        if (
            os.environ.get("PYSC_EXPERIMENTAL_EMBED_LINK") != "static"
            and lib.suffix in (".dylib", ".so")
            and not lib.is_symlink()
        ):
            shutil.copy2(lib, out / "lib" / lib.name)
    files, ext = closure(top_imports(args.program) | set(args.extra), stdlib, dynload)
    zip_name = f"python{version.replace('.', '')}.zip"
    write_zip(files, out / "lib" / zip_name)
    # getpath wants lib/pythonX.Y/lib-dynload as the exec_prefix landmark even when empty.
    (out / "lib" / f"python{version}" / "lib-dynload").mkdir(parents=True, exist_ok=True)
    for so in ext:
        shutil.copy2(so, out / "lib" / f"python{version}" / "lib-dynload" / so.name)
    if args.trim_by_run:
        run = subprocess.run([str(binary)], env={"PYTHONPROFILEIMPORTTIME": "1"}, capture_output=True, text=True)
        if run.returncode != 0:
            print(f"trim run failed: {run.stderr[-300:]}", file=sys.stderr)
            return 1
        seen = {line.split("|")[-1].strip() for line in run.stderr.splitlines() if "|" in line}
        kept = {name: path for name, path in files.items() if name in seen}
        write_zip(kept, out / "lib" / zip_name)
        for so in ext:
            if so.name.split(".")[0] not in seen:
                (out / "lib" / f"python{version}" / "lib-dynload" / so.name).unlink()
        print(f"trimmed by run: {len(files)} -> {len(kept)} modules", file=sys.stderr)
    print(f"bundle: {out} ({len(files)} stdlib modules in {zip_name}, {len(ext)} extension files)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
