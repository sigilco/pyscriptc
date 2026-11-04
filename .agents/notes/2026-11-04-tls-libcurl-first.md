# tls-curl: libcurl first for https:// (issue #235)

Owner decision 2026-11-04: libcurl first, mbedTLS as the portable fallback later.

The runtime reaches an `https://` URL through libcurl, loaded at run time with `dlopen` (no build-time `-lcurl`), so binaries keep working on a machine without it and `http://` programs gain no libcurl dependency. Only the seven easy API symbols resolve, verification is always on (`CURLOPT_SSL_VERIFYPEER` and `CURLOPT_SSL_VERIFYHOST` at their defaults), and the `PsHttpTransport` interface stays for `http://` and for the mbedTLS backend that comes later.

## Notes

- The client drives the redirects itself (`CURLOPT_FOLLOWLOCATION` stays off), so the method and body rules and the 10-hop cap are the same on the `http://` and `https://` paths.
- The tests use a fake libcurl: a small shared library built by the test that exports the same seven symbols, records the options it saw and answers with a canned response. The runtime honours an absolute `PSC_LIBCURL_PATH` for the library it loads, and only that (a bare name never redirects the load).
- Without libcurl the `https://` request raises the existing not-supported `URLError` with a hint that installing libcurl enables it.
- The plan file `.agents/plans/2026-11-04-tls-libcurl-first.md` carries the shape; its earlier draft (link-time `-lcurl`) is superseded.
