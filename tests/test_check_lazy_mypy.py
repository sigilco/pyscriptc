"""`pyscriptc.check` must import with mypy absent (mypy is the optional `oracle-mypy` extra)."""

import subprocess
import sys

BLOCKED = """
import sys
for name in [m for m in sys.modules if m == "mypy" or m.startswith("mypy.")]:
    del sys.modules[name]
sys.modules["mypy"] = None  # makes `import mypy` raise ImportError
import pyscriptc.check as check
assert check.CheckFailed
try:
    check.plugin("1")
except check.CheckFailed as exc:
    assert "oracle-mypy" in str(exc)
else:
    raise SystemExit("plugin() should fail without mypy")
"""


def test_check_imports_without_mypy() -> None:
    proc = subprocess.run([sys.executable, "-c", BLOCKED], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
