# tls-curl: https:// over dlopen'ed libcurl (issue #235, stacked on #881)

Owner decision: libcurl first. For `https://` the runtime performs the whole
request through libcurl's easy API in a new `runtime/ps_http_curl.c`; the
transport interface stays for `http://` and a later mbedTLS backend.

Decision 2026-11-04: libcurl loads at run time (dlopen `libcurl.so.4`,
`libcurl.4.dylib`, `libcurl.dll`), never a build-time `-lcurl`, so binaries
keep working on a machine without it and `http://` programs gain no libcurl
dependency. The pre-branch draft of this plan (link-time `-lcurl` resolved
through `curl-config`, gated on the emitted IR calling `ps_http_`) is
superseded by the dlopen shape.

## Deliverables

1. `ps_http_curl.c`: an `https://` round trip over curl_easy (URL, method,
   headers, body, timeout from the deadline, redirect rules matched to the
   HTTP layer, verification on through CURLOPT_SSL_VERIFYPEER and VERIFYHOST
   at their defaults, response into the existing PsHttpResp record). The
   library loads at run time and only the seven easy API symbols resolve;
   a failed load raises the existing "https is not supported" URLError with
   a hint that installing libcurl enables it.
2. Tests with a fake libcurl shim (a C shared library built by the test and
   loaded through `PSC_LIBCURL_PATH`, which the runtime honours only when
   the value is an absolute path): records the options it saw, returns a
   canned response; asserts verification on, headers and body pass through,
   timeout applied, and the load-failure path raises the documented error.
3. Divergence 18 rewrite + `.agents/notes/2026-11-04-tls-libcurl-first.md`
   (one-line note: libcurl first, mbedTLS as the portable fallback later).
