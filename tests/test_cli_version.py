# SPDX-License-Identifier: Apache-2.0
import pytest

import pyscriptc
from pyscriptc.cli._main import main


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([flag])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith(f"pyscriptc {pyscriptc.__version__}")
