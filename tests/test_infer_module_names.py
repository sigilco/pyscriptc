"""The infer runner's module names match the lowerer's unit names.

The pin map is keyed by the qualified name of the callee
(`pkg.mod.helper`). The lowerer asks for the pins of the name it lowers
the definition under, so a pin keyed under a different spelling never
reaches the definition it proves.

The lowerer names a unit after the tree it lowers: a package tree
namespaces its modules under the package directory (`yt_dlp.utils._utils`),
a program tree (no `__init__.py` at the root) uses the plain relative path
(`util.helper`). The infer runner must name the same file the same way.
"""

from __future__ import annotations

from pathlib import Path

from pyscriptc.inferrunner import _module_name
from pyscriptc.package import _discover


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def test_a_package_tree_namespaces_under_the_package_directory(tmp_path: Path) -> None:
    root = _tree(
        tmp_path / "yt_dlp",
        {
            "__init__.py": "",
            "utils/__init__.py": "",
            "utils/_utils.py": "def helper(v):\n    return v\n",
            "utils/traversal.py": "def walk(v):\n    return v\n",
            "extractor/generic.py": "def probe(v):\n    return v\n",
        },
    )
    assert _module_name(root / "utils" / "_utils.py", root) == "yt_dlp.utils._utils"
    assert _module_name(root / "utils" / "__init__.py", root) == "yt_dlp.utils"
    assert _module_name(root / "extractor" / "generic.py", root) == "yt_dlp.extractor.generic"
    assert _module_name(root / "__init__.py", root) == "yt_dlp"


def test_a_program_tree_names_the_plain_relative_path(tmp_path: Path) -> None:
    root = _tree(
        tmp_path / "prog",
        {
            "__main__.py": "from util import helper\n",
            "util.py": "def helper(v):\n    return v\n",
            "pkg/__init__.py": "",
            "pkg/mod.py": "def probe(v):\n    return v\n",
        },
    )
    assert _module_name(root / "util.py", root) == "util"
    assert _module_name(root / "pkg" / "mod.py", root) == "pkg.mod"
    assert _module_name(root / "pkg" / "__init__.py", root) == "pkg"
    assert _module_name(root / "__main__.py", root) == "__main__"


def test_the_infer_names_match_the_lowerer_names(tmp_path: Path) -> None:
    root = _tree(
        tmp_path / "yt_dlp",
        {
            "__init__.py": "",
            "__main__.py": "",
            "utils/__init__.py": "",
            "utils/_utils.py": "def helper(v):\n    return v\n",
            "extractor/__init__.py": "",
            "extractor/generic.py": "def probe(v):\n    return v\n",
        },
    )
    units, _skipped = _discover(root, True, entry=None)
    mismatched = [
        (str(path.relative_to(root)), _module_name(path, root), name)
        for name, path, _pkg in units
        if _module_name(path, root) != name
    ]
    assert mismatched == []
