"""A short label for one route: the last path segment and a status code."""


def label(path: str, status: int = 200) -> str:
    parts: list[str] = path.split("/")
    name: str = parts[-1]
    return f"{name} {status}"
