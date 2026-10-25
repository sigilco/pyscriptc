# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "tomli>=2.0.0",
# ]
# ///
from __future__ import annotations

from typing import Any

import tomli

TOML_DOC: str = """
[server]
host = "127.0.0.1"
port = 8080
enabled = true

[database]
connections = 10
timeout = 5.5
"""


def parse_config(doc: str) -> dict[str, Any]:
    data: dict[str, Any] = tomli.loads(doc)
    return data


def format_summary(data: dict[str, Any]) -> str:
    server: dict[str, Any] = data.get("server", {})
    host: str = str(server.get("host", ""))
    port: int = int(server.get("port", 0))
    enabled: bool = bool(server.get("enabled", False))
    return f"host={host} port={port} enabled={enabled}"


def main() -> None:
    cfg: dict[str, Any] = parse_config(TOML_DOC)
    summary: str = format_summary(cfg)
    print("Config summary:", summary)
    db: dict[str, Any] = cfg.get("database", {})
    print("DB connections:", db.get("connections"))


if __name__ == "__main__":
    main()
