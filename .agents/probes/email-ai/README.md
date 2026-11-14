# email-ai consumer probes

Minimal repro cases for the fences found while porting a real 7.2k-LOC Rust CLI
(`email-ai`) to pyscriptc. See the write-up: [`../email-ai-consumer-probes.md`](../email-ai-consumer-probes.md).

Verified on master `ec024c72` (v0.10.0b1), 2026-11-12.

## Run them

```sh
# from the pyscriptc repo checkout
uv run pyscriptc coverage .agents/probes/email-ai/g1_subprocess_timeout.py
```

Each `gN_*.py` fences; each `ok_gN_*.py` compiles clean (kept as the regression guard for each fix).

## Index

| Probe | Fence | Gap |
|---|---|---|
| `g1_subprocess_timeout.py` | PS2003 | `subprocess.run(..., timeout=)` not lowered |
| `g2_async_subprocess.py` | PS1106 | `asyncio.subprocess.Process` not lowered |
| `g3_sys_stdio.py` | PS2001 | `sys.stdin.readline` / `sys.stdout.flush` not lowered |
| `g4_dict_object.py` | PS1104 | `dict[str, object]` not lowered (heterogeneous JSON) |
| `g5_installer_archives.py` | PS2001 | `tarfile.open` not lowered |
| `g6_hashlib_stream.py` | PS2003 | zero-arg `hashlib.sha256()` + `.update()` not lowered |
| `g6b_hashlib_oneshot.py` | PS2003 | `file.read(n)` not lowered (one-shot hash works) |
| `g7_email_mime.py` | PS2002 | `email` module not in the closed import table |
| `g9_file_read_size.py` | PS2003 | `file.read(n)` not lowered (dup of g6b root cause) |
| `g10_http_server.py` | PS2002 | `http.server` not in the closed import table |
| `g10b_socket.py` | PS2001 | `socket.socket` not lowered (no server surface) |
| `ok_g8_argv.py` | - | `sys.argv` lowers |
| `ok_g9b_file_read_all.py` | - | whole-file `read()` lowers |
| `ok_g11_urllib_download.py` | - | `urllib.request.urlopen(...).read()` lowers |

## Notes

- `g6b` and `g9` share a root cause: the one-argument `read(size)` on a binary file
  fences with `PS2003: BytesIO.read expects 0 argument(s)`. `g6b` additionally shows
  the one-shot `hashlib.sha256(data).hexdigest()` path is fine once `read(n)` is fixed.
- `g3` has a working workaround today: `os.read(0, N)` / `os.write(1, ...)` on the raw
  fds lowers natively and round-trips a JSON-RPC line. The missing piece is the
  *buffered line reader* and a flush story.
- `g1` and `g2` are the same capability at two levels (sync and async); fixing the
  subprocess timeout keyword likely wants the same underlying watchdog.
