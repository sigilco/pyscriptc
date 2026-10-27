"""The data-file reader of the acceptance program (issue #665)."""

import json
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data.json"


def read_data() -> str:
    return json.loads(DATA_FILE.read_text())["msg"]
