"""The generated docs pages match the code they come from."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from scripts.gen_cli_docs import render as render_cli  # noqa: E402
from scripts.gen_config_docs import render as render_config  # noqa: E402
from scripts.gen_diagnostics_docs import render as render_diagnostics  # noqa: E402

from pyscriptc.cli import build_parser  # noqa: E402
from pyscriptc.config import _KEYS  # noqa: E402
from pyscriptc.diagnostics import DIAGNOSTICS  # noqa: E402


@pytest.mark.parametrize(
    ("script", "page"),
    [
        ("gen_cli_docs", "docs/reference/cli.md"),
        ("gen_config_docs", "docs/reference/config.md"),
        ("gen_diagnostics_docs", "docs/reference/diagnostics.md"),
    ],
)
def test_generated_page_is_current(script: str, page: str) -> None:
    renderers = {
        "gen_cli_docs": render_cli,
        "gen_config_docs": render_config,
        "gen_diagnostics_docs": render_diagnostics,
    }
    content = renderers[script]()
    current = (ROOT / page).read_text(encoding="utf-8")
    assert content == current, f"{page} is out of date; run: uv run python scripts/{script}.py"


def test_cli_page_covers_every_subcommand() -> None:
    text = render_cli()
    parser = build_parser()
    sub = [a for a in parser._actions if type(a).__name__ == "_SubParsersAction"][0]
    for name in sub.choices:
        assert f"### `{name}`" in text, f"cli.md misses subcommand {name}"


def test_config_page_covers_every_key() -> None:
    text = render_config()
    for key in _KEYS:
        assert f"`{key}`" in text, f"config.md misses key {key}"


def test_diagnostics_page_covers_every_code() -> None:
    text = render_diagnostics()
    for code in DIAGNOSTICS:
        assert f"`{code}`" in text, f"diagnostics.md misses code {code}"
