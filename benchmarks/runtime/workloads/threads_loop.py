"""threading.active_count and current_thread().name through the island."""

import threading


def bench(n: int) -> int:
    total: int = 0
    i: int = 0
    while i < n:
        label: str = threading.current_thread().name
        total = total + len(label) + threading.active_count()
        i = i + 1
    return total
