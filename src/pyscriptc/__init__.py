"""Spike compiler: CPython ast to a Python typed IR to LLVM."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _metadata_version


def _resolve_version() -> str:
    """Release version of this pyscriptc.

    Order: the `_release.py` stamp written next to the PostHog token stamp in the
    release and CI workflows (release truth, also present in the frozen binary),
    then the installed package metadata (dev checkouts install from pyproject.toml),
    then an unknown marker.
    """
    try:
        from pyscriptc._release import VERSION

        return str(VERSION)
    except ModuleNotFoundError:
        pass
    try:
        return _metadata_version("pyscriptc")
    except PackageNotFoundError:
        return "0+unknown"


__version__ = _resolve_version()
