# Design note: crypto and compression libraries for the lowered stdlib modules

Issue #1282. Question: for the lowered stdlib modules that need a real compression or crypto implementation (`zlib`, `hashlib` beyond the hand-written digests, `ssl`), do we link the system OpenSSL and zlib, vendor minimal single-file implementations (miniz, a BLAKE2 reference, Monocypher), or build static libraries with zig?

This note records the analysis and a recommendation. It changes no code, and it appends no row to `.agents/decisions.md`: the proposed row is at the end for the owner to land.

## The inputs this decision sits on

The note is not a free-standing design. Four facts from the tree constrain it.

First, the runtime already answers the hash question on its own. `src/pyscriptc/runtime/ps_hash.c` is a hand-written md5, sha1, sha256, sha384 and sha512 implementation with no OpenSSL behind it, and its first line says so: "md5, sha1, sha256, sha384, and sha512. No OpenSSL." The file is 882 lines and carries its own x86-64 and aarch64 acceleration paths (`PS_HASH_X86_64`, `PS_HASH_AARCH64`, the `cpuid` and `sysctl` probes that pick SHA-NI and crypto-extension instructions). `ps_hmac.c` (120 lines) builds the HMAC construction over the same primitives. The `hashlib` pack (`src/pyscriptc/restlower/_hashlib.py`) lowers exactly five algorithms (`_ALGS = {"md5": 1, "sha1": 2, "sha256": 3, "sha384": 4, "sha512": 5}`) and every other name fences with `PS2001`. `hmac` lowers over the same five ids (`src/pyscriptc/restlower/_hmac.py`).

Second, the compression question is already answered for the archive path, and only for that path. `zlib_link_flags` in `src/pyscriptc/emit/llvm/_driver.py` adds `-lz` to the link line of a program whose emitted IR reaches the archive symbols, and refuses to add it on the wasm and Windows targets. The archive runtime (`ps_archive.c`) reads a `.tar.gz` stream and a deflated zip member through zlib and raises `RuntimeError` when the build does not link it. Nothing else in the tree links zlib. The onefile stub (`src/pyscriptc/onefile.py`) is a separate case: it is a host-side C stub that links zlib by design, because the onefile payload is a zlib-compressed archive.

Third, the HTTPS decision is already taken and it is not OpenSSL. Divergence 18 says a program reaching an `https://` URL gains libcurl's flags at build time (`curl-config --libs`, then `pkg-config libcurl`), and a machine without libcurl fails the compile with a diagnostic naming the package to install. `PSC_CURL_DYNAMIC=1` opts out and the request raises `OSError` at run time. Issue #235 (HTTPS via mbedTLS through the FFI manifest) was closed as superseded by the libcurl-backed HTTPS bundle (#884), and its own comment thread records the owner's direction: "Default: generated programs that use https link against the system libcurl as a shared library at build time." The transport interface stays for a future bundled TLS backend.

Fourth, `ssl` does not compile at all. Divergence 29: an import of `ssl` or `http.client` stops the compile with `PS2001` naming the module. The `socket` subset (divergence 77) fences the `ssl` wrap with `PS2001` too, and the `http.server` subset (divergence 80) fences `ssl` the same way.

So the decision the issue asks for is narrower than it looks. The live questions are: what fills the `hashlib` gaps (sha3, blake2), whether the existing `-lz` link should stay a system-zlib link or become a vendored one, and what an `ssl` module would link if it ever lands.

## The options

### Option A: link the system OpenSSL

`hashlib`, `hmac` and a future `ssl` link `-lcrypto` (and `-lssl`) from the system, resolved the way libcurl is resolved today (a `pkg-config` or `curl-config` probe, then a diagnostic naming the package to install).

Pros: OpenSSL is the reference implementation for most of what the modules need, it is already present on nearly every machine the compiler runs on, and the `ssl` module is a near-impossible module to build from scratch (X.509 parsing, certificate chains, TLS state machine, cipher suites, hostname verification).

Cons: it is a large link-time dependency for a hash function. The measured static cost on this host (Debian 12, OpenSSL 3.5.4) is the number that matters: linking a program that calls `EVP_MD_CTX_new` against `/usr/lib/x86_64-linux-gnu/libcrypto.a` plus its `zlib` and `zstd` backends pulls in the whole crypto provider, and the shared-library form is 6.2 MB of `libcrypto.so.3` on disk. Even the shared form makes the emitted binary depend on the target machine having the same OpenSSL ABI. OpenSSL 3.x's provider model also means the actual code reached is behind an indirection that a static link cannot trim the way a hand-written digest can. And it does nothing for wasm: wasi-libc ships no OpenSSL, and there is no OpenSSL build for `wasm32-wasi-musl` in any distribution.

### Option B: vendor minimal single-file implementations

Vendor `miniz` (a single-file zlib clone) for compression, and either a BLAKE2 reference implementation or Monocypher for the extra digests.

Pros: no build-host dependency at all. The same source compiles on every target the compiler already targets, including `wasm32-wasi-musl`, `wasm32-wasi-p2`, the three mobile triples and Windows, because a vendored `.c` file is just another runtime unit. Static size is minimal: miniz is roughly 2,000 lines and a few tens of kilobytes of `.text`; a BLAKE2b implementation is about 300 lines. Licensing is clean (miniz is MIT, the BLAKE2 reference code is CC0 / OpenSSL licensed, Monocypher is CC0), and the sources fit the existing SPDX-header convention the repo adopted on 2026-11-11.

Cons: `miniz` is not zlib. It implements the DEFLATE format and the zlib wrapper format, so the compressed bytes match for the same compression level and the `adler32` / `crc32` checksums match, but the *compressed output* is not byte-identical to zlib's at the same level, and the `zlib` module's documented `compressobj` attributes (`level`, `method`, `wbits`, `memLevel`, `strategy`) do not map one to one onto miniz's knobs. That matters for CPython byte parity, because CPython's `zlib.compress` output is zlib's own output and a program that hashes its own compressed bytes would differ. The same applies to `hashlib.blake2b`: a vendored BLAKE2 implementation that is correct produces CPython's exact digest (BLAKE2 is a fixed-function digest, not a compression algorithm, so a correct implementation is byte-identical), while `zlib` and `deflate` are not.

Monocypher is the odd one out: it is a modern, small, audited crypto library, but it does not implement SHA-3 or BLAKE2 at all. It offers BLAKE2b and BLAKE2s in its optional (and separately licensed, public-domain) `optional` module, but not Keccak. So Monocypher covers the blake2 half of the `hashlib` gap and nothing of the sha3 half.

### Option C: build static libraries with zig

Use `zig cc` (or the `ziglang` wheel's compiler) to cross-compile zlib, a BLAKE2 implementation and, if wanted, OpenSSL into per-target static archives that the compiler ships and links.

Pros: the resulting archives are real, complete implementations with real zlib byte parity and a real OpenSSL. Static archives remove the target machine's library dependency entirely, which is exactly what a "ship a binary that runs anywhere" product needs.

Cons: it is the largest mechanism of the three and the most expensive to maintain. It needs a vendored source tree per library, a build recipe per target triple (three mobile triples, two wasm triples, Windows, Linux, macOS), and a pin policy for each upstream. It also collides with the mobile story: `docs/guide/mobile.md` records that a library build refuses `--driver zig` outright ("zig bundles neither bionic nor the Apple SDKs, so the build compiles through clang with an explicit sysroot"), so a zig-built static archive cannot be produced on the mobile lane at all without a second build path. And the zig-based build is exactly the dependency the self-contained-install note (#1291) argues against making unconditional.

### Option D (status quo for crypto, change for compression)

Keep the hand-written digests as the only `hashlib` implementations, keep `ssl` fenced, and decide the compression question on its own: keep the system `-lz` link for the archive path, or vendor miniz for it.

Pros: no new dependency anywhere, no new link-audit surface, and every already-lowered module keeps its current byte parity. The `hashlib` subset is already at parity with CPython for the five algorithms it covers, and the remaining algorithms are fenced honestly rather than approximated.

Cons: `hashlib` stays narrower than CPython's, so a program that needs `sha3_256` or `blake2b` does not compile. The `ssl` module stays fenced, so a program that needs a TLS socket does not compile. And the archive path's `-lz` keeps a build-host dependency that the wasm and Windows targets already refuse.

## The measurements

Sizes measured on this host (Debian 12, x86_64, clang 19.1.7, `-O2`), linking a one-symbol program against each library:

| Link | Binary size |
| --- | --- |
| no library (baseline) | 15,864 bytes |
| `-lz` (shared) | 15,976 bytes |
| `libz.a` (static) | 28,592 bytes |
| `-lcrypto` (shared) | 16,064 bytes on disk, 6.2 MB of `libcrypto.so.3` |
| `-lssl -lcrypto` (shared) | 16,112 bytes on disk, plus 1.1 MB of `libssl.so.3` |

The shared-library numbers are small in the binary but not in the deployment: the binary is portable only to a machine with the same OpenSSL and zlib versions, which is the same portability hole #1247 closed for `libpython` on the island lane, one layer down. The static numbers are the real cost: a statically linked zlib adds about 12.7 KB to a binary, and a statically linked `libcrypto.a` cannot even be linked without dragging in its `zlib` and `zstd` backends on this host (the `libcrypto.a` link failed with undefined `inflate`/`deflate`/`ZSTD_*` references until `-lz` was added, which is a concrete measurement of how entangled OpenSSL 3.5 is).

Wheel and install-size impact:

- A vendored source file (`miniz.c` at about 2,000 lines, a BLAKE2 reference at about 300 lines) is package data under `pyscriptc/runtime/` (the `pyproject.toml` `package-data` block already globs `runtime/*.c` and `runtime/*.h`), so it rides the existing wheel with no new packaging machinery. The wheel grows by the source text, tens of kilobytes at most.
- A zig-built static archive is a different shape entirely: it is a prebuilt `.a` per target triple, so the wheel grows by one archive per supported platform (order of a hundred kilobytes each for zlib, several hundred kilobytes for OpenSSL), and the compiler has to know which archive belongs to which target. That is per-platform wheel content in a project whose wheel is currently pure Python plus sources.
- The system OpenSSL option grows the wheel by nothing at all, which is its only real advantage. It pushes the cost onto the target machine instead.

## Per-module recommendations

### `zlib`

Recommendation: keep the `zlib` module itself fenced (it is not lowered today), and replace the archive path's system `-lz` link with a vendored inflate.

Reasoning: the `zlib` module is not lowered today (nothing in `src/pyscriptc/restlower/` or the stdlib queue names it; `src/pyscriptc/stdlibzip.py` lists it only as a builtin the bundled CPython runtime serves). The only zlib consumer in the tree is `ps_archive.c`, whose gzip and deflate reads produce *decompressed* bytes, not compressed bytes. Decompression is format-exact: any correct DEFLATE decoder returns the same bytes, so a vendored miniz inflate would be byte-identical to zlib's, and a vendored miniz deflate would not be.

That splits the question cleanly. For the archive read path, miniz's inflate is a drop-in with byte parity, and it removes the build-host zlib dependency and the wasm and Windows refusals. For a future `zlib` module, miniz's deflate would break CPython parity on the compressed bytes, so the module needs real zlib, which means either the system `-lz` (the current archive path's shape) or a zig-built static zlib.

Recommendation, sharper: vendor an inflate for the archive path (miniz's inflate side, or a vendored zlib build), and keep the system `-lz` for the *deflate* side only if a deflate writer is ever needed. The archive path today only inflates, so the system zlib link can be replaced by a vendored inflate with no parity cost at all, and the `-lz` build-host dependency, the wasm refusal and the Windows refusal all disappear in one step. A future `zlib` module needs real zlib for the compressed bytes, so the module stays fenced until that decision is taken separately.

### `hashlib`: md5, sha1, sha2 (sha256, sha384, sha512)

Recommendation: keep the hand-written implementations in `ps_hash.c`. No change.

Reasoning: they are already at CPython byte parity, they are already fast on the two architectures that matter (the SHA-NI and ARM crypto-extension paths), they carry no build-host dependency, they work on every target including wasm and the mobile triples, and they cost 882 lines of auditable C. Swapping them for OpenSSL would trade a working, dependency-free, parity-proven implementation for a 6.2 MB shared object or a several-hundred-kilobyte static archive.

### `hashlib`: sha3 (sha3_224, sha3_256, sha3_384, sha3_512) and shake

Recommendation: a vendored, dedicated Keccak implementation, not OpenSSL, not Monocypher.

Reasoning: SHA-3 is a fixed-function digest with no parameters beyond the output length, so a correct implementation is byte-identical to CPython's. A Keccak-f[1600] implementation is roughly 200 to 300 lines of portable C, with no dependency and no configuration surface. Monocypher does not implement Keccak at all, so it does not cover this gap. OpenSSL covers it, but at the cost described above. `hashlib.shake_128` and `shake_256` are a separate story because they are extendable-output functions with a length parameter; CPython's `shake_128(data).hexdigest(32)` reads 32 bytes of output, and a vendored implementation must match CPython's domain-separation byte (the `0x1F` suffix for SHA-3, the `0x1F` with a different prefix for the SHAKE cSHAKE variants), which is a parity risk worth its own differential probe rather than an assumption.

### `hashlib`: blake2 (blake2b, blake2s)

Recommendation: a vendored BLAKE2 reference implementation (the RFC 7693 reference code, which is CC0 / OpenSSL licensed), not Monocypher.

Reasoning: BLAKE2 is a fixed-function family, so a correct implementation is byte-identical to CPython's, which uses the reference implementation through `_blake2`. The reference code is small (about 300 lines), portable C with no configuration, and its own header exposes the `blake2b_init`, `blake2b_update` and `blake2b_final` trio the streaming `hashlib` shape needs. Monocypher's BLAKE2b is a valid alternative and is CC0, but it lives in Monocypher's separately licensed optional module, which is a second license to track for the same result.

The one caution: `hashlib.blake2b` in CPython takes key, salt, and person keyword arguments, and the keyed variant is not just "prefix the key". A vendored implementation that ignores the key would be a silent security failure, so the subset should either implement the keyed path exactly or fence the keyword arguments with `PS2001` from the first cut.

### `hmac`

Recommendation: no change. Keep it over the hand-written digests.

Reasoning: `ps_hmac.c` builds the HMAC construction over the same five primitives, so it inherits the same parity, size and portability properties. Adding sha3 or blake2 to `hashlib` should be followed by the matching `hmac` algorithm ids (CPython's `hmac.new` accepts a digest name, and the current `_HMAC_ALGS` map is keyed by the same five names), which is a small extension of the existing tables, not a new library.

### `secrets`

Recommendation: no change, and no library is needed at all.

Reasoning: `ps_secrets.c` reads `/dev/urandom` directly, which is the same source CPython's `SystemRandom` reads through `os.urandom`, and the values differ from CPython's by construction (divergence 58). No crypto library contributes anything here. The one portability gap worth noting is that `/dev/urandom` is a POSIX path: a Windows target has no such file, and the mobile targets have their own entropy story. That is a runtime-portability question, not a library-choice question, and it stays outside this decision.

### `ssl`

Recommendation: do not build `ssl` on OpenSSL, and do not build it in this milestone. Keep the `PS2001` fence, and record the direction for when the module is scoped.

Reasoning: the `ssl` module surface is a Python-level wrapper over a TLS stack, and CPython's `ssl` wraps OpenSSL. A byte-parity `ssl` module means the same exceptions, the same `SSLError` hierarchy, the same `wrap_socket` signature and the same verification defaults. The only implementation that gives that cheaply is OpenSSL itself, because that is what CPython wraps. But the current TLS story in this project is libcurl at build time (divergence 18) and the `http://` native client, not a TLS stream, and issue #235 already recorded that a transport interface is the shape that a future bundled TLS backend plugs into. An `ssl` module on top of OpenSSL would also drag the OpenSSL version into the program's deployment contract in a way the current libcurl link does not (libcurl is the transport; OpenSSL would be the application-level API).

The direction to record: when `ssl` is scoped, the TLS transport (not the `ssl` module) is the thing to decide first, and the decision should be revisited together with the bundled-TLS-backend slice that #235's comment thread left open.

## Licensing

Everything in the recommended direction is permissive and compatible with the repo's Apache-2.0 licensing and with the SPDX-header convention adopted on 2026-11-11.

| Component | License | Notes |
| --- | --- | --- |
| zlib (system, current archive path) | Zlib | Already in `packaging/third-party.toml` as part of the bundled CPython runtime inventory (zlib 1.3.1). |
| OpenSSL 3.x | Apache-2.0 | Already in `packaging/third-party.toml` (OpenSSL 3.5.4) for the bundled runtime. |
| miniz | MIT | Permissive; a vendored copy needs its license text and a `LICENSE.miniz.txt` entry if it ships in a bundle output. |
| BLAKE2 reference (RFC 7693 appendix) | CC0-1.0 / OpenSSL | Permissive; the dual license means the MIT-style OpenSSL license text can be used. |
| Monocypher | CC0-1.0 | Permissive, but does not cover SHA-3. |

The licensing story for a *vendored* library has one wrinkle the system-library story does not: a vendored source becomes part of the compiler's own source tree, and the repo's license-header convention means each vendored file needs its upstream copyright notice preserved rather than the project's own SPDX header applied over it. The bundled-CPython-output license staging (`src/pyscriptc/licenses.py`, `stage_pack_licenses`) only covers what travels with a `--embed-python=bundle` output, so a vendored library that ends up in a plain native binary needs its own notice path, or it needs to be scoped to the bundle outputs only.

## The mobile and wasm story

The three lanes differ sharply, and this is the strongest argument against a single library choice for everything.

The wasm targets are the clearest. `zlib_link_flags` refuses to add `-lz` on `wasm32` and Windows targets because "their toolchains ship no zlib, and the compressed members raise at run time there". wasi-libc ships no zlib and no OpenSSL, so on the wasm lane the only options that work today are the hand-written runtime code (which is what `ps_hash.c` is) and a vendored source file. The wasm lane is also where a `dlopen`-style optional backend is impossible: divergence 31 records that WASI preview1 has "no `dlopen`", so the system-OpenSSL option and any runtime-loaded TLS backend are both off the table there.

The mobile lane refuses `--driver zig` for library builds (no bionic, no Apple SDKs in the zig bundle), so anything built by zig cannot be produced on the mobile lane without a second build path. A vendored `.c` file, by contrast, is just another runtime unit that the existing clang-with-sysroot path compiles unchanged. The mobile lane also has its own app-store constraints: an iOS app linking OpenSSL statically is a known App Store review friction point, while a small vendored inflate and a Keccak implementation carry no such risk.

Windows is the weakest lane overall and should not decide anything here: the repo's own position is that "Windows is experimental cross-compilation only; native Windows compiles are not supported yet" (divergence 33), and no CI job builds or runs Windows-emitted binaries.

The conclusion for the lanes is consistent: **the vendored-source option is the only one that works on all lanes**, and the system-library option only works on the desktop POSIX lane, which is where the current `-lz` link already lives.

## Recommendation

Vendor, in this order, and keep everything else as it is.

1. `hashlib` sha3 (sha3_224, sha3_256, sha3_384, sha3_512): a vendored Keccak implementation, plus the `shake_128` and `shake_256` domain-separation handling, each with a differential probe against the pinned CPython.
2. `hashlib` blake2b and blake2s: a vendored BLAKE2 reference implementation, with the keyed variant either implemented exactly or fenced with `PS2001` from the first cut.
3. `zlib` compression for the archive path: a vendored inflate (miniz's inflate side, or a vendored zlib build), which removes the build-host `-lz` dependency and lifts the wasm and Windows refusals, with no parity cost because the archive path only decompresses.
4. `hmac`: extend the algorithm tables to the new digests once they land, with no new library.
5. `secrets`: no change; it needs no crypto library.
6. `ssl`: no change now; keep the `PS2001` fence. When the module is scoped, decide the TLS transport first, alongside the bundled-TLS-backend slice that issue #235's thread left open. Do not put OpenSSL behind the module without a separate decision.

Rejected, with reasons: system OpenSSL (a 6.2 MB shared object and a target-machine ABI dependency for a job the 882-line hand-written digest file already does, and nothing on wasm or the mobile lanes); zig-built static libraries (the largest mechanism, the per-target matrix, the collision with the mobile lane's clang-only build, and a per-platform binary in a wheel that is currently pure Python plus sources); a miniz deflate for a future `zlib` module (the compressed bytes would not match CPython's, so the module would need real zlib anyway); Monocypher (it does not implement Keccak, so it covers only the blake2 half of the gap, and its optional module is a second license to track).

The general principle behind the recommendation: **a digest is a fixed function and can be vendored at parity for a few hundred lines; a compression codec's output is an implementation detail and can only be vendored at parity by vendoring the same implementation; a TLS stack is neither, and belongs to a different decision.** OpenSSL belongs to the TLS decision, not to the hash decision.

## Proposed decisions.md row

Not appended to `.agents/decisions.md`. Proposed wording for the owner to land with the implementation PR:

```
2026-11-17 | Crypto and compression libraries for the lowered stdlib: vendor the fixed-function digests (a Keccak implementation for sha3, the BLAKE2 reference for blake2), vendor an inflate for the archive path, keep the hand-written md5/sha1/sha2 in ps_hash.c, keep secrets library-free, and keep the ssl module fenced | A fixed-function digest is byte-identical across implementations, so a few hundred vendored lines buy CPython parity with no build-host dependency on any lane, while OpenSSL costs a 6.2 MB shared object and an ABI dependency and does not exist on wasm or the mobile triples; the archive path only decompresses, so a vendored inflate is byte-exact, and a vendored deflate would not be (the compressed bytes would differ from CPython's), so a future zlib module needs real zlib and stays unfenced; ssl needs a TLS transport decision, not a library decision, and issue #235 already took the transport direction (libcurl at build time, a bundled TLS backend later) | Link the system OpenSSL (a 6.2 MB dependency for a job the 882-line hand-written digest file already does, and nothing on wasm or mobile), zig-build static libraries (a per-target build matrix, blocked on the mobile lane's clang-only build, and prebuilt binaries in a pure-Python wheel), vendor a deflate for a future zlib module (the compressed bytes would not match CPython's) | `.agents/docs/design/crypto-and-compression-libs.md`
```
