---
title: Signing and verifying JWTs
description: The PyJWT subset pyscriptc lowers, the closed claim shape, and where it differs from PyJWT.
---

pyscriptc lowers two names of the `jwt` module (the PyJWT package, through the curated `pyscriptc-jwt` distribution package): `jwt.encode` and `jwt.decode`.
Both sign and verify with HMAC-SHA2 only: `HS256`, `HS384` and `HS512`.

```python
import jwt

token: str = jwt.encode({"sub": "u1"}, "secret")
claims: dict[str, str] = jwt.decode(token, "secret", ["HS256"])
print(claims["sub"])
```

`encode` takes the payload, the key, and optionally the algorithm, as the keyword `algorithm=` or the third positional argument.
`decode` takes the token, the key, and the algorithm allow-list, exactly as PyJWT requires it.
The key is a `str` or `bytes`; the token is a `str` or `bytes`.

The payload is a `dict[str, str]`.
A claim with an `int`, `list` or other value (`exp`, `aud`) is outside the subset and fences (`PS1003`).
`decode` needs its result annotation (`PS1001` without one) and returns the annotated `dict[str, str]`.

Errors raise `ValueError` where PyJWT raises its own `InvalidTokenError` hierarchy: a signature that does not verify, an algorithm outside the allow-list, an unsupported algorithm in the header, and a malformed token all surface as `ValueError`.

## Where it differs from PyJWT

Two differences, listed as divergence 19 in [Divergences from CPython](../reference/divergences.md):

- Only the HMAC family lowers.
  `RS256` and the other asymmetric algorithms need bignum and fence at compile time (`PS2001`), where PyJWT signs them with a private key.
  The algorithm argument must be a literal, so an `algorithms` list built at run time cannot select one.
- A multi-claim payload is emitted with the claims sorted by key, because the subset's dict is a hash map with no stable order.
  PyJWT emits the claims in the payload dict's insertion order, so a token over `{"role": "admin", "sub": "u1"}` carries the claims in a different order than PyJWT's.
  Decode does not depend on the order, and single-claim tokens are byte-identical to PyJWT's.

Everything else in PyJWT stays fenced: the exception constants (`jwt.PyJWTError` and friends), `jwt.get_unverified_header`, the `options` dict, and the `headers=` argument.
