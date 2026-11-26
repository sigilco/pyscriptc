# Addendum: the #1282 decision row and the link-audit criterion

Issue #1282. The assessment note lives in `crypto-and-compression-libs.md` (landed 2026-11-17 with PR #1297); this addendum records the two things the note does not: the link-audit criterion's verdict on the system-library candidates, and the decision row that the note left for the owner to land (landed in `.agents/decisions.md` on 2026-11-26, PR #1411).

## The link-audit criterion (#1247)

Issue #1282 names the link audit as one of the criteria, so here is exactly what the audit covers and what that implies for a system-zlib or system-OpenSSL link.

What the audit checks today, from the tree:

- `src/pyscriptc/bindeps.py` matches `libpython*.dylib`, `Python.framework/.../Python` and ELF `Shared library: [libpython*]` - the patterns are named `_LIBPYTHON_MACHO`, `_FRAMEWORK_MACHO` and `_LIBPYTHON_ELF` (`bindeps.py:62-64`), and nothing else.
- The audit runs only on the island lane (`"ps_island_" in llvm_text`, `src/pyscriptc/cli/_main.py:2449`), because only an island link can embed libpython at all; a native-only compile never pays the `otool`/`readelf` call.
- The verdict is a hard fail on `compile --emit bin` (exit non-zero) or a named warning with `--allow-host-libpython` (`_main.py:2456-2467`).

What that means for the crypto/compression candidates:

- A system `-lz` or `-lcrypto` link is **not** audited. A binary that links the build host's `libz.1.dylib` or `libcrypto.3.dylib` by absolute path or bare soname passes the audit silently, and breaks on a target machine with a different or absent library - the exact footgun #1247 closed for `libpython`, one layer down. The archive path's `-lz` link (`zlib_link_flags`, `src/pyscriptc/emit/llvm/_driver.py:232-247`) is a live instance of this on the desktop POSIX lane.
- So the link-audit criterion does not veto the system-library options on its own; it prices them. Choosing system OpenSSL or system zlib means either extending the audit to those sonames (a real, small change to `bindeps.py` and the audit call site) or accepting unaudited host dependencies in shipped binaries. The vendored-source option has no entry in the audit at all, which is the strongest reading of the criterion: the audit exists to catch host dependencies, and the way to pass it is not to have them.

The vendored direction is therefore also the link-audit direction: it removes the one unaudited host dependency the tree already has (the archive path's `-lz`), instead of adding a second one (`-lcrypto`).

## Vendor facts and sources

Facts cited in the note and the row, with their sources (checked 2026-11-26):

- miniz is a single-source-file zlib-subset implementation, MIT licensed, with the entire inflater available separately as `miniz_tinfl.c` at about 550 lines and no heap use in the low-level codec API (https://github.com/richgel999/miniz, README and `miniz.h` header comment).
- The BLAKE2 reference implementation is the RFC 7693 appendix code, dual-licensed CC0-1.0 / OpenSSL (https://www.rfc-editor.org/rfc/rfc7693.html, appendix C; the reference sources carry both license texts).
- Monocypher is CC0-1.0 (or BSD-2-Clause), under 2,000 lines, dependency-free, and implements BLAKE2b - but not Keccak/SHA-3, which its author documents as a deliberate choice (https://monocypher.org/, https://monocypher.org/manual/blake2b, https://loup-vaillant.fr/articles/implemented-my-own-crypto).
- CPython's `hashlib` exposes blake2b/blake2s with key, salt and person keyword arguments and sha3/shake through the `_blake2` and `_sha3` builtin modules, so the vendored implementations must match those parameter surfaces or fence the keywords (https://docs.python.org/3.14/library/hashlib.html).
- OpenSSL 3.x is Apache-2.0 licensed and its `libcrypto` shared object is multi-megabyte on every tier the compiler targets; the note's Debian measurement (6.2 MB `libcrypto.so.3`) and this host's macOS measurement (33 KB binary growth for a shared `-lz` link, versus 12.7 KB for a static `libz.a` on the note's host) agree on the shape: shared links are small in the binary and large in the deployment, static links are bounded but per-target.

## What implementation follows

The decision row commits the direction, not the code. When the implementation lands, it is, in order:

1. `hashlib` sha3 (sha3_224, sha3_256, sha3_384, sha3_512) over a vendored Keccak implementation, with the `shake_128`/`shake_256` XOF domain separation, each with a differential probe against the pinned CPython (the differential probe rule, #524).
2. `hashlib` blake2b/blake2s over the RFC 7693 reference, with the keyed variant implemented exactly or fenced with `PS2001` from the first cut - an ignored `key=` would be a silent security failure, not a parity gap.
3. The archive path's `-lz` replaced by a vendored inflate (miniz `tinfl`), removing the build-host zlib dependency and the wasm/Windows refusals in `zlib_link_flags` with no parity cost (decompression is format-exact).
4. The `hmac` algorithm tables extended to the new digests, no new library.
5. `ssl` stays fenced (D29); the TLS transport is decided with the bundled-TLS-backend slice #235 left open, not here.

Each step is its own PR with its own differential probe; none of them changes a frozen gate.

## The documentation-policy check

Per the documentation policy (`AGENTS.md`, "Documentation policy"), the user docs state divergences and gaps, never feature lists, and a support-state change updates the affected divergence or gap entry in the same PR. This issue implements nothing, so no support state changes and no entry in `docs/reference/divergences.md` moves:

- `ssl` and `http.client` stay fenced with `PS2001` (divergence 29, ledger row `D29`, status `fenced`). The decision explicitly keeps that fence, so the entry stays as it is.
- The `hashlib` subset stays `md5`, `sha1`, `sha256`, `sha384`, `sha512` (`src/pyscriptc/restlower/_hashlib.py`, `_ALGS`), with everything else fenced by `PS2001`. The decision adds no algorithm, so the fence stays.
- The archive read path still reads `r:gz` tars and `ZIP_DEFLATED` members through zlib and raises `RuntimeError` on a build without it (divergence 69). The vendored inflate removes that build-host dependency when it lands, and that PR will update divergence 69 in the same change.

Nothing in `docs/` claims a crypto, `hashlib` or `ssl` support state that this decision contradicts, so this PR touches no user doc.
