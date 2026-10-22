"""Parse one log line into its source and message."""

SEPARATOR = " "


def parse_source(line: str) -> str:
    """The source name: the text before the first separator."""
    parts: list[str] = line.split(SEPARATOR, 1)
    return parts[0]


def parse_message(line: str) -> str:
    """The message: the text after the first separator, empty when absent."""
    parts: list[str] = line.split(SEPARATOR, 1)
    if len(parts) < 2:
        return ""
    return parts[1]
