"""HMAC-only port of the PyJWT 2.15.1 encode/decode surface for pyscriptc.

Semantics follow ``jwt/api_jws.py`` + ``jwt/api_jwt.py`` from the vendored pin
(``benchmarks/compat/top100/src/jwt``): compact JSON, base64url without padding,
HMAC-SHA256 over ``header.payload``, ``alg`` allow-list on decode, and the
``PyJWTError`` hierarchy. The exact deltas forced by today's fences are listed
in ``benchmarks/compat/pyjwt/README.md``.
"""

import base64
import hashlib
import hmac
import json
import time


class PyJWTError(Exception):
    pass


class InvalidTokenError(PyJWTError):
    pass


class DecodeError(InvalidTokenError):
    pass


class InvalidSignatureError(DecodeError):
    pass


class ExpiredSignatureError(InvalidTokenError):
    pass


class InvalidAlgorithmError(PyJWTError):
    pass


# json.dumps(header, separators=(",", ":"), sort_keys=True) of pyjwt's
# {"typ": "JWT", "alg": "HS256"}: sort_keys puts "alg" first.
_HEADER_JSON = '{"alg":"HS256","typ":"JWT"}'
_HEX = "0123456789abcdef"


def _strip_padding(text: str) -> str:
    """base64url_encode: ``urlsafe_b64encode(data).rstrip(b"=")``."""

    n: int = len(text)
    while n > 0 and text[n - 1 : n] == "=":
        n = n - 1
    return text[:n]


def _b64url_decode(data: str) -> str:
    """base64url_decode: re-pad then decode; returns str, not bytes."""

    rem: int = len(data) - (len(data) // 4) * 4
    padded: str = data
    if rem == 1:
        padded = data + "==="
    if rem == 2:
        padded = data + "=="
    if rem == 3:
        padded = data + "="
    return base64.urlsafe_b64decode(padded).decode()


def _json_quote(text: str) -> str:
    """CPython json string quoting for the ASCII subset pyscriptc lowers."""

    out: str = '"'
    i: int = 0
    while i < len(text):
        c: str = text[i : i + 1]
        code: int = ord(c)
        if c == '"' or c == "\\":
            out = out + "\\" + c
        elif c == "\n":
            out = out + "\\n"
        elif c == "\r":
            out = out + "\\r"
        elif c == "\t":
            out = out + "\\t"
        elif code == 8:
            out = out + "\\b"
        elif code == 12:
            out = out + "\\f"
        elif code < 32:
            out = (
                out
                + "\\u00"
                + _HEX[code // 16 : code // 16 + 1]
                + _HEX[code - (code // 16) * 16 : code - (code // 16) * 16 + 1]
            )
        else:
            out = out + c
        i = i + 1
    return out + '"'


def _dumps_claims(payload: dict[str, str]) -> str:
    """json.dumps(payload, separators=(",", ":")) with sorted keys.

    Sorted keys keep the signing input deterministic: a hash map has no
    insertion order, so emitting claims sorted is the closest stable match to
    CPython's insertion-ordered dump.
    """

    keys: list[str] = sorted(payload.keys())
    out: str = "{"
    i: int = 0
    for k in keys:
        if i > 0:
            out = out + ","
        out = out + _json_quote(k) + ":" + _json_quote(payload[k])
        i = i + 1
    return out + "}"


def encode(payload: dict[str, str], key: str, algorithm: str = "HS256") -> str:
    """PyJWS.encode for the HS256 path: header.payload + base64url signature."""

    if algorithm != "HS256":
        raise InvalidAlgorithmError("Algorithm not supported")
    signing_input: str = _strip_padding(base64.urlsafe_b64encode(_HEADER_JSON.encode()).decode())
    signing_input = (
        signing_input + "." + _strip_padding(base64.urlsafe_b64encode(_dumps_claims(payload).encode()).decode())
    )
    signature: bytes = hmac.new(key.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _strip_padding(base64.urlsafe_b64encode(signature).decode())


def decode(token: str, key: str, algorithms: list[str]) -> dict[str, str]:
    """PyJWT.decode: segment check, alg allow-list, HS256 verify, exp check."""

    parts: list[str] = token.split(".")
    if len(parts) != 3:
        raise DecodeError("Not enough segments")
    header: dict[str, str] = json.loads(_b64url_decode(parts[0]))
    alg: str = header["alg"]
    if alg not in algorithms:
        raise InvalidAlgorithmError("The specified alg value is not allowed")
    if alg != "HS256":
        raise InvalidAlgorithmError("Algorithm not supported")
    signing_input: str = parts[0] + "." + parts[1]
    expected: bytes = hmac.new(key.encode(), signing_input.encode(), hashlib.sha256).digest()
    actual: bytes = base64.urlsafe_b64decode(_pad(parts[2]))
    if not hmac.compare_digest(expected, actual):
        raise InvalidSignatureError("Signature verification failed")
    claims: dict[str, str] = json.loads(_b64url_decode(parts[1]))
    if "exp" in claims:
        if int(claims["exp"]) < int(time.time()):
            raise ExpiredSignatureError("Signature has expired")
    return claims


def _pad(data: str) -> str:
    rem: int = len(data) - (len(data) // 4) * 4
    if rem == 1:
        return data + "==="
    if rem == 2:
        return data + "=="
    if rem == 3:
        return data + "="
    return data
