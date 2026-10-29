# mbedTLS license note

The vendored mbedTLS sources that `fetch_vendor.py` downloads are provided
under a dual license: Apache-2.0 OR GPL-2.0-or-later. The adapter ships the
Apache-2.0 option; the upstream license text is in `LICENSE-APACHE.md`.

The pinned version, URL and checksums are in `vendor/VENDOR.toml`. The
config trim (the four unset options) is recorded there too, so the fetched
tree is byte-identical on every host.

The adapter's own files (`ffi.toml`, `ps_ffi_http_tls.c`, `fetch_vendor.py`)
follow the repository license.
