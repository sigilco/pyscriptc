---
title: Example programs
description: Programs in the repository that compile today, with the value each one prints.
---

# Example programs

The programs below, from [`examples/`](https://github.com/sigilco/pyscriptc/tree/master/examples), are an invoice, an access-log gate, a log token, a release note, a record digest, a URL allow-list, a route label, and a hostname lookup. Each one compiles, and the printed value matches CPython.

`--args` passes `int`, `float`, `bool`, and `str`. Lists and bytes are built inside the function.

## Invoice

`examples/invoice.py` bills a meter and totals cents with a floor-divided tax. A negative line returns `-1`.

```bash
uv run pyscriptc compile examples/invoice.py --run --call charge --args '[10, 2, 50]' -o /tmp/charge
uv run pyscriptc compile examples/invoice.py --run --call invoice --args '[10]' -o /tmp/invoice
uv run pyscriptc compile examples/invoice.py --run --call bad_invoice -o /tmp/bad-invoice
```

`charge` prints `600` (`(10 + 2) * 50`). `invoice` prints `4290` (lines of 1000, 2500, and 400 cents, plus 10 percent). `bad_invoice` prints `-1`.

## Access log

`examples/access_log.py` admits one JSON object of strings. GET, a path under `/api`, and status `200` return `0`. Anything else returns `1`.

```bash
uv run pyscriptc compile examples/access_log.py --run --call admit \
  --args '["{\"method\":\"GET\",\"path\":\"/api/health\",\"status\":\"200\"}"]' \
  -o /tmp/admit
```

A POST to the same path prints `1`. A missing field raises `KeyError`.

## Request id

`examples/request_id.py` returns the first digit run in a log line.

```bash
uv run pyscriptc compile examples/request_id.py --run --call request_id \
  --args '["id=88421 status=500"]' -o /tmp/request-id
```

That prints `88421`. A line with no digits prints an empty line.

## Release note

`examples/release_note.py` adds whole days to an ISO date, writes the result, and reads it back.

```bash
uv run pyscriptc compile examples/release_note.py --run --call stamp \
  --args '["2020-01-01", 40, "/tmp/release.txt"]' -o /tmp/stamp
```

That prints `2020-02-10`, and `/tmp/release.txt` contains the same text.

## Record digest

`examples/blob_digest.py` packs two integers, checks a base64 round trip, and returns the SHA-256 hex digest.

```bash
uv run pyscriptc compile examples/blob_digest.py --run --call fingerprint \
  --args '[1, 2]' -o /tmp/fingerprint
```

That prints `34fb5c825de7ca4aea6e712f19d439c1da0c92c37b423936c5f618545ca4fa1f`.

## URL gate

`examples/url_gate.py` matches a URL path with `fnmatch` and percent-encodes a path.

```bash
uv run pyscriptc compile examples/url_gate.py --run --call allow \
  --args '["https://example.com/api/health", "/api/*"]' -o /tmp/allow
uv run pyscriptc compile examples/url_gate.py --run --call quote_path \
  --args '["https://example.com/a b"]' -o /tmp/quote
```

`allow` prints `1`. The same host with pattern `/api/*` and path `/health` prints `0`. `quote_path` prints `/a%20b`.

## Route label

`examples/route_label.py` takes the last segment of a path and a status. The status defaults to `200`.

```bash
uv run pyscriptc compile examples/route_label.py --run --call label \
  --args '["/api/health", 201]' -o /tmp/label
```

That prints `health 201`. Omitting the status prints `health 200`.

## Hostname

`examples/hostname.py` runs through embedded CPython (fallback mode). The binary links libpython.

```bash
uv run pyscriptc compile examples/hostname.py --run --call hostname -o /tmp/hostname
```

It prints the same name `socket.gethostname()` prints under CPython.

## Word count (a program directory)

`examples/wordcount/` is a program: `__main__.py` is the entry point and `tally.py` is imported by its plain name. The whole tree compiles into one binary.

```bash
uv run pyscriptc compile examples/wordcount --run -o /tmp/wordcount
```

It prints `three 3`, the word that appears most often in the sentence and how often it appears.
