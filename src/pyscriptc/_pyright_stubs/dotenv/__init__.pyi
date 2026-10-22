# Vendored surface for the lowered subset (the compiler's closed dotenv
# rows): python-dotenv types itself loosely (`StrPath`, `Dict[str, str | None]`);
# this stub pins the two names the lowerer accepts.
from typing import Optional

__all__ = ["dotenv_values", "load_dotenv"]

def dotenv_values(dotenv_path: str) -> dict[str, Optional[str]]: ...
def load_dotenv(dotenv_path: str) -> bool: ...
