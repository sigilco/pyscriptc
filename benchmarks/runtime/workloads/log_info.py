"""logging.info through the embedded logging module. Stdout stays the checksum."""

import logging


def bench(n: int) -> int:
    i: int = 0
    while i < n:
        logging.info("bench")
        i = i + 1
    return n
