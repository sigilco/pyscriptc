"""The license texts a bundle output carries in `share/licenses/`.

Everything here is boxed-only code (the ledger counts this module whole; see
`.agents/docs/boxed-cost.md` and `AGENTS.md`).

A `--embed-python=bundle` output redistributes a compiled CPython: the PSF
license and the licenses of everything compiled into libpython must travel with
it. The texts come from three sources:

- the bundled runtime's own CPython license text, taken from the pack
  (`lib/pythonX.Y/LICENSE.txt` of the pinned python-build-standalone asset);
- the license text of every third-party component compiled into libpython,
  vendored as the `pyscriptc.pbs_licenses` package data (the python-build-
  standalone `install_only` asset the pack is cut from ships no `licenses/`
  directory; the `full` archive does, and `scripts/sync_pbs_licenses.py`
  vendors its texts with their hashes pinned in `packaging/pbs_licenses.toml`);
- the third-party notice rollup for the bundled stdlib and libraries, rendered
  from `packaging/third-party.toml` and the runtime lock by
  `scripts/gen_third_party_notices.py` and shipped as the `NOTICE` of the
  same package data;
- the `licenses/` subdirectory of every `*.dist-info` the bundle's
  site-packages carries (issue #660), copied per distribution.

Shipping the data as package data keeps an installed compiler independent of
a repository checkout: `stage_bundle` reads only the installed package and the
pack.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

PACKAGE = "pyscriptc.pbs_licenses"
NOTICE_NAME = "NOTICE"


@contextmanager
def _data_dir() -> Iterator[Path]:
    """The packaged license data directory, as a real filesystem path."""
    with resources.as_file(resources.files(PACKAGE)) as path:
        yield path


def stage_pack_licenses(dest: Path) -> list[str]:
    """Copy the packaged third-party license texts into `dest`.

    The CPython text is not copied here: the bundled runtime's own text comes
    from the pack (`stage_cpython_license`), which is the exact text of the
    interpreter that travels.
    """
    copied: list[str] = []
    with _data_dir() as root:
        for source in sorted(root.iterdir()):
            if source.name == NOTICE_NAME or not source.is_file():
                continue
            shutil.copy2(source, dest / source.name)
            copied.append(source.name)
    return copied


def stage_notice(dest: Path) -> bool:
    """Copy the packaged third-party notice rollup into `dest` as `NOTICE`."""
    with _data_dir() as root:
        source = root / NOTICE_NAME
        if not source.is_file():
            return False
        shutil.copy2(source, dest / NOTICE_NAME)
        return True


def stage_cpython_license(dest: Path, pack_directory: Path, version: str) -> bool:
    """Copy the bundled runtime's own CPython license text into `dest`.

    The pack carries it as `lib/pythonX.Y/LICENSE.txt`; the text is copied as
    `LICENSE.cpython.txt`. Returns False when the pack has no such file (an
    unusual pack; the vendored text of the other components still travels).
    """
    source = pack_directory / "lib" / f"python{version}" / "LICENSE.txt"
    if not source.is_file():
        return False
    shutil.copy2(source, dest / "LICENSE.cpython.txt")
    return True


def stage_site_licenses(site_packages: Path, dest: Path) -> list[str]:
    """Copy each bundled distribution's `licenses/` directory into `dest`.

    Every `*.dist-info` under the staged site-packages contributes a
    `<name>/` subdirectory holding its `licenses/` tree; a distribution that
    ships no license directory contributes nothing. Returns the distribution
    names staged.
    """
    staged: list[str] = []
    if not site_packages.is_dir():
        return staged
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        licenses = dist_info / "licenses"
        if not licenses.is_dir():
            continue
        name = dist_info.name[: -len(".dist-info")]
        shutil.copytree(licenses, dest / name, dirs_exist_ok=True)
        staged.append(name)
    return staged


def stage_share_licenses(
    output: Path,
    *,
    pack_directory: Path | None = None,
    version: str = "",
    site_packages: Path | None = None,
) -> Path:
    """Write the whole `share/licenses/` tree of a bundle output.

    `output` is the staged bundle directory (`name/`); the licenses land in
    `name/share/licenses/`. Returns the licenses directory.
    """
    dest = output / "share" / "licenses"
    dest.mkdir(parents=True, exist_ok=True)
    stage_pack_licenses(dest)
    stage_notice(dest)
    if pack_directory is not None and version:
        stage_cpython_license(dest, pack_directory, version)
    if site_packages is not None:
        stage_site_licenses(site_packages, dest)
    return dest
