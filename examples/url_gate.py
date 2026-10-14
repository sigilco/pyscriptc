"""Allow a URL when its path matches an fnmatch pattern.

`allow` returns 1 on a match and 0 otherwise. `quote_path` returns the
percent-encoded path. `/` stays literal, which is `urllib.parse.quote`'s
default safe set.
"""

import fnmatch
import urllib.parse


def allow(url: str, pattern: str) -> int:
    path: str = urllib.parse.urlsplit(url).path
    if fnmatch.fnmatch(path, pattern):
        return 1
    return 0


def quote_path(url: str) -> str:
    path: str = urllib.parse.urlsplit(url).path
    return urllib.parse.quote(path)
