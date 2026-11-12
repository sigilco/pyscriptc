"""The re corpus program: the island module the compiler routes to the engine."""

import re


def match_groups() -> str:
    m = re.match(r"(\d+)-(\d+)", "12-34")
    if m is None:
        return "none"
    return m.group(1) + "/" + m.group(2)


def find_all() -> str:
    return ",".join(re.findall(r"[a-z]+", "a1b2c3"))


def sub_count() -> int:
    return len(re.sub(r"\d", "#", "a1b2c3"))
