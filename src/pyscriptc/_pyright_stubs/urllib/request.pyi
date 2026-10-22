# Vendored surface for the lowered urllib.request subset (issues #60, #225,
# #226). Typeshed's urlopen returns `Any`; the subset pins the record shape:
# the response exposes status/headers/body with getcode/info/read shims, and
# Request carries the request fields. Everything else in urllib.request
# stays fenced at the call site.
__all__ = ["Request", "urlopen", "HTTPResponse", "HttpRequest", "HttpResponse"]


class HttpRequest:
    url: str
    method: str
    body: bytes
    headers: str
    timeout: float

    def __init__(
        self,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        method: str | None = None,
        timeout: float = 0.0,
    ) -> None: ...


class Request(HttpRequest):
    pass


class HttpResponse:
    status: int
    headers: str
    body: bytes

    def getcode(self) -> int: ...
    def info(self) -> str: ...
    def read(self) -> bytes: ...


HTTPResponse = HttpResponse


def urlopen(
    url: str | HttpRequest, data: bytes | None = None, timeout: float = 0.0
) -> HttpResponse: ...
