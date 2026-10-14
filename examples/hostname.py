"""Print the machine name through the CPython island.

`hostname` calls `socket.gethostname`. The binary links libpython and
initializes the embedded interpreter on that call. A script that never calls
the island does not link libpython.
"""

import socket


def hostname() -> str:
    return socket.gethostname()
