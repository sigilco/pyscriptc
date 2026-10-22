# Vendored stub for the lowered yaml surface (PyYAML's `yaml` package).
# Only `safe_load` lowers (issue #254, the closed subset in
# runtime/ps_yaml.c); the rest of PyYAML stays fenced at the call site.
from typing import Any

__all__ = ["safe_load"]

def safe_load(string: str) -> Any: ...
