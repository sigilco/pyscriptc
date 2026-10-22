"""Repeated probe-worker runs must emit byte-identical output.

This is the worker-level half of the generation a/b check: nondeterministic calls are
demoted statically (VOLATILE_CALLS) or normalized after the axis flags them, so two
identical runs of the same module's worker print the same JSON lines.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from benchmarks.compat2.probes import typeshed

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_surface_worker_output_is_reproducible() -> None:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "PYSC_TYPESHED": str(typeshed.stdlib_dir()),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    # random/secrets cover VOLATILE_CALLS demotion; os/time/posix cover pid, clock and
    # scheduler entries plus the untabled-fallthrough path.
    for module in ("random", "secrets", "time", "os", "posix"):
        runs = [
            subprocess.run(
                [sys.executable, "-m", "benchmarks.compat2.probes.surface", module],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            for _ in range(2)
        ]
        assert all(run.returncode == 0 for run in runs), f"{module} worker failed: {runs[0].stderr[-300:]}"
        assert runs[0].stdout == runs[1].stdout, f"{module} worker output differed between identical runs"
