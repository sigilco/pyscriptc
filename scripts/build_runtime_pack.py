"""Build the pinned CPython runtime pack for one platform.

    uv run python scripts/build_runtime_pack.py --platform macos-arm64 -o out/

Reads `packaging/runtime.lock.toml`, downloads the pinned python-build-standalone
archive (sha256-verified against the lock), and writes
`pyscriptc-runtime-cp314-<platform>.tar.zst`: the `include/` tree, the shared
libpython (with its `.so.N` companions and symlinks), the stdlib (with
`lib-dynload`), the `python` executable stub and the CPython license file.
The release workflow job `runtime-pack` attaches the pack to the GitHub
release; `pyscriptc embed fetch` downloads and unpacks it (issue #657).

The pack for macOS arm64 and Linux x86_64 is at most 60 MB compressed
(kill criterion of the embedding release plan); the script prints the size.
"""

from __future__ import annotations

import argparse
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pyscriptc import embed  # noqa: E402

# What travels in the pack, relative to the archive's payload root.
_LIB_KEEP = ("libpython*",)


def build(platform: str, out: Path, lock: Path | None = None) -> Path:
    pack = embed.lock_pack(platform, path=lock)
    with tempfile.TemporaryDirectory(prefix="runtime-pack-") as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / pack.asset
        embed._download(pack.asset_url, archive)
        embed.verify_sha256(archive, pack.sha256)
        staging = tmp_path / "unpacked"
        staging.mkdir()
        embed.unpack(archive, staging)
        payload = embed._payload_root(staging)
        version = embed.python_version()

        # The pack tree mirrors the install layout the driver and the bundle
        # staging expect: include/, lib/libpython*, lib/pythonX.Y/.
        pack_tree = tmp_path / "pack"
        (pack_tree / "lib").mkdir(parents=True)
        for item in sorted(payload.glob("include")):
            _copy(item, pack_tree / item.name)
        lib_src = payload / "lib"
        for pattern in _LIB_KEEP:
            for lib in sorted(lib_src.glob(pattern)):
                _copy(lib, pack_tree / "lib" / lib.name)
        stdlib = lib_src / f"python{version}"
        if stdlib.is_dir():
            _copy(stdlib, pack_tree / "lib" / f"python{version}")
        else:
            raise SystemExit(f"the archive has no lib/python{version}")
        # getpath wants lib/pythonX.Y/lib-dynload as the exec_prefix landmark
        # even when it is empty.
        (pack_tree / "lib" / f"python{version}" / "lib-dynload").mkdir(exist_ok=True)
        # The `python` executable stub: needed so the pack root passes the
        # getpath landmark check and can run `python -m` helpers.
        exe = payload / "bin" / f"python{version.split('.')[0]}.{version.split('.')[1]}"
        if not exe.exists():
            candidates = sorted((payload / "bin").glob("python*")) if (payload / "bin").is_dir() else []
            if not candidates:
                raise SystemExit("the archive has no python executable")
            exe = candidates[0]
        (pack_tree / "bin").mkdir()
        _copy(exe, pack_tree / "bin" / exe.name)
        license_src = payload / "lib" / f"python{version}" / "LICENSE.txt"
        if license_src.is_file():
            _copy(license_src, pack_tree / "LICENSE.txt")

        dest = out / f"pyscriptc-runtime-cp{version.replace('.', '')}-{platform}.tar.zst"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(dest, "w:zst") as tar:
            for member in sorted(pack_tree.rglob("*")):
                tar.add(member, arcname=member.relative_to(pack_tree))
        size = dest.stat().st_size
        print(f"{dest} {size} bytes ({size / (1024 * 1024):.1f} MB)")
        if size > 60 * 1024 * 1024:
            print("OVER the 60 MB kill criterion of the embedding release plan", file=sys.stderr)
            return dest
        return dest


_TRIMMED = ("__pycache__", "test", "idlelib", "tkinter", "turtledemo", "ensurepip", "pydoc_data", "site-packages")


def _copy(src: Path, dest: Path) -> None:
    import shutil

    if src.is_dir():
        shutil.copytree(src, dest, symlinks=True, ignore=shutil.ignore_patterns(*_TRIMMED))
    else:
        shutil.copy2(src, dest, follow_symlinks=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the pinned CPython runtime pack")
    ap.add_argument("--platform", required=True, help="lock-file platform key, e.g. macos-arm64")
    ap.add_argument("--lock", type=Path, default=None, help="runtime lock file (default: packaging/runtime.lock.toml)")
    ap.add_argument("-o", "--out", type=Path, required=True, help="output directory")
    args = ap.parse_args()
    build(args.platform, args.out, args.lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
