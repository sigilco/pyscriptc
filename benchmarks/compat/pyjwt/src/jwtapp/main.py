from .jwt_compat import decode, encode


def probe() -> str:
    # Keys are listed in sorted order so the compact dump matches CPython's
    # insertion-ordered json.dumps byte for byte.
    payload: dict[str, str] = {"exp": "2000000000", "sub": "u1"}
    token: str = encode(payload, "secret", algorithm="HS256")
    claims: dict[str, str] = decode(token, "secret", ["HS256"])
    return token + "\n" + claims["sub"] + ":" + claims["exp"]
