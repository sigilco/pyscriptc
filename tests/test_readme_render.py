"""The committed README.md is the default render of README.tmpl.md."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("render_readme", ROOT / "scripts" / "render_readme.py")
render_readme = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_readme)


def test_readme_matches_default_render(monkeypatch):
    monkeypatch.delenv("PYSC_DOCS_URL", raising=False)
    assert (ROOT / "README.md").read_text() == render_readme.render()


def test_env_var_overrides_docs_url(monkeypatch):
    monkeypatch.setenv("PYSC_DOCS_URL", "https://docs.example.test/")
    text = render_readme.render()
    assert "https://docs.example.test/guide/cli/" in text
    assert "{{DOCS_URL}}" not in text
