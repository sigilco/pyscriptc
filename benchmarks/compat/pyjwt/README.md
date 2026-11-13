# PyJWT on pyscriptc

Goal: get the useful PyJWT subset — `jwt.encode(payload, key,
algorithm="HS256")` and `jwt.decode(token, key, algorithms=["HS256"])` —
compiling **unchanged** under pyscriptc.

Pin: `pyjwt 2.15.1` sdist (`pyjwt-2.15.1.tar.gz`, sha256
`4f259e80cdfb6b3fc18a7de51fd1ef9ec79652f25019bae68975ca2468a34df8`), same
snapshot already vendored in `benchmarks/compat/top100/src/jwt`.

## What was tried

`src/jwt/` is the **minimal import closure** of the HS256 encode/decode path:
`api_jwt.py`, `api_jws.py`, `api_jwk.py`, `algorithms.py`, `exceptions.py`,
`utils.py`, `warnings.py`, `types.py`. The files are byte-identical to the
top100 pin. `__init__.py`, `jwks_client.py`, `jwk_set_cache.py` and `help.py`
are omitted because nothing on the HMAC path imports them (the package
`__init__` pulls `jwks_client` → `urllib`/`http.client`/`threading`, which is
even further out).

Compiling that tree unchanged still exits 3 with **106 fences** (roots +
derived, `pyscriptc coverage` census). No pyjwt file compiles unchanged on its
own: `exceptions.py` fences on `MissingRequiredClaimError`'s unannotated
`self.claim`, `warnings.py` fences on `DeprecationWarning`/`UserWarning` bases.

## Root fence families on the minimal tree

| Family | Fences | What it is | Backlog |
| --- | --- | --- | --- |
| union-types | 13 | `str | bytes | None`, `Options | None` signatures everywhere | R26 (epic) |
| dynamic-annotation | 15 | `Any` params/returns | R26-adjacent |
| parameter-forms | 8 | `**kwargs`, kw-only `*,` args on `decode`/`decode_complete`/`_validate_headers` | not yet itemized |
| unlowered-type-name | 8 | `Algorithm`, `SigOptions`, `JWKDict` annotations | R38 / class-value types |
| name-resolution | 13 | module-level globals read inside functions (`_jws_global_obj`, `default_algorithms`, `has_crypto`) | R22/R36-adjacent |
| class-body / class-keywords | 4 | `header_typ = "JWT"` class constants, `TypedDict(total=...)` | — |
| field-lookup | 2 | `MissingRequiredClaimError.claim` unannotated attr | — |
| forward-reference | 2 | `"EllipticCurve"` quoted annotations | — |
| import-table | 5 | `abc`, `collections.abc`, `binascii`, `calendar` | — |
| construct-other | 2 | `dict[bytes, int]`, `re.finditer` loop, `startswith(tuple)` | R3 |
| local-inference / typing-value | 3 | `Optional` + `cast` usages | R26, R38 |
| stdlib:datetime | 1 | `datetime.timezone` not in lowered subset | — |

The three that dominate are epics already tracked on the board:
union/Optional values (R26), parameter forms incl. `**kwargs`, and
module-level mutable globals.
Unchanged compilation of even the minimal tree is therefore not a
one-iteration task — that is the finding.

## What landed here instead

- **`int(str)` un-fenced** (R5 partial): `builtin_spec` now accepts a `str`
  argument and emits the existing `ps_int_from_bytes` parser (surrounding
  whitespace, sign, digits, `ValueError` on garbage — same semantics the
  bytes path already implements). Needed for `exp`/`iat` claim validation.
- **`src/jwtapp/jwt_compat.py`** — an HMAC-only port of the real code path
  that compiles today. Deltas vs the vendored sources, each forced by a
  fence above:

  | Real PyJWT | Compat delta | Blocker |
  | --- | --- | --- |
  | `payload: dict[str, Any]`, int `exp` | `dict[str, str]` claims, `exp` as decimal str | union-types R26 |
  | `decode(..., algorithms=None, options=None, **kwargs)` | positional `key`, `algorithms: list[str]` | parameter-forms |
  | `options.get(...)` / merged default options | dropped; defaults inlined | dict.get R1 + union R26 |
  | `json.dumps(payload, separators=(",",":"))` insertion order | `_dumps_claims` sorted keys | lowered `json.dumps` emits `", "`; dict has no insertion order |
  | `json.dumps(header, sort_keys=True)` | constant header JSON `{"alg":"HS256","typ":"JWT"}` | same |
  | `base64url_decode` returns `bytes` | returns `str` | `for c in bytes`, `* n` padding → explicit rem branches |
  | `datetime.now(timezone.utc)` / `timegm` | `int(time.time())` | datetime.timezone, calendar fences |
  | `except ... as`, `raise ... from`, `issubclass`, `isinstance(key, PyJWK)` | simplified control flow | R8, dunder/class forms |
  | `MissingRequiredClaimError` custom `__init__`/`__str__` | omitted | field-lookup, dunder dispatch R25 |
  | `warnings.warn(InsecureKeyLengthWarning)` | dropped | warning-class bases |
  | claim validators (`_validate_aud/iss/jti/nbf/iat`) | only `exp` numeric check kept | `Container`/`Iterable` ABCs, unions |

  `main.py` `probe()` does the acceptance round-trip:
  `encode({"exp": "2000000000", "sub": "u1"}, "secret", algorithm="HS256")`
  then `decode(token, "secret", ["HS256"])`, printing the token and the
  claims. The produced token is byte-identical to real PyJWT's (verified in
  `tests/test_pyjwt_compat.py`, which generates the expectation with the
  vendored package under CPython).

## Still blocked for fully-unchanged

R26 tagged unions (Optional/`Any`), parameter forms (`**kwargs`, kw-only),
module-level mutable globals read in functions, `dict.get`/`items` (R1),
`except ... as` (R8), class-body constants, TypedDict keywords, `abc`/
`collections.abc`/`binascii`/`calendar` imports, and dunder dispatch (R25)
for `__str__`/custom exception init.
