# subprocess.Popen: the synchronous child handle (issue #1345)

Status: landed on `work/1345-subprocess-child`. The runtime is `src/pyscriptc/runtime/ps_popen.c` with the lowering in `src/pyscriptc/restlower/_popen.py`, the differential probe is `probes/differential/subprocess_popen.py` (byte-compared against the pinned CPython), and the divergence entry is 92. The spawn environment (`env=`, `cwd=`, `start_new_session=`, the `stdin=` mode keywords beyond the pipe mask) is issue #1344 and is fenced by name in this surface.

## The design problem

`subprocess.run` (issue #1223) and `asyncio.create_subprocess_exec` (issue #1266) both spawn a child, but neither hands the caller a handle: `run` blocks to the exit and the asyncio surface binds the child to an event loop. The shape the issue asks for is CPython's interactive `Popen`: the caller spawns, then drives the child one statement at a time (`poll`, `wait`, signal it, read its pipes incrementally). That surface cannot be expressed over the `run` runtime helper, because `run`'s C function owns the whole child lifetime inside one call. The handle needs a runtime record that outlives the lowering call that made it.

## The runtime record

`ps_popen.c` keeps a `PsPopen` record per child: the pid, one descriptor per piped stream (`out_fd`, `err_fd`, `in_fd`, -1 when the stream was not piped), a pending-read buffer per read side (the parked surplus an earlier short `read(n)` left), the reaped flag, the exit code and the argv repr the timeout message carries. The record is the value the `Popen` class carries: `emit/llvm/_abi.py` whitelists `Popen` in its raw-pointer return set, so a runtime constructor returns the record pointer rather than a boxed class layout, the same treatment `Socket` and the http.server records get.

`Popen(argv)` forks and execs at once and answers the record immediately. The pipe mask is an int argument the lowering computes from the `stdout=`/`stderr=`/`stdin=` keywords (bit 0 stdout, bit 1 stderr, bit 2 stdin), so the runtime needs no keyword parsing. Each piped stream dup2s a pipe over itself in the child; the parent keeps the other end on the record.

## poll/wait and the tagged None

CPython's `poll()` and `returncode` answer `None` while the child runs. The compiler's tagged optional machinery (`_opt_type`, `_opt_box`, `_opt_fill`, the shape `os.getenv` uses) expresses that: the runtime answers two helpers, `ps_popen_reaped` (the flag) and `ps_popen_poll` (the code), and the lowering boxes the hit-then-fill cell. A `None` result is the child that still runs; the code is the reaped exit code. `wait(timeout=)` takes a whole-millisecond bound (0 is no deadline) and polls the child in 2 ms slices against a monotonic deadline; a child that outlives the bound raises `subprocess.TimeoutExpired` with CPython's message (`Command '<cmd repr>' timed out after N seconds`) and stays alive, CPython's contract, so the caller decides between `kill()` and keep-waiting.

A child a signal killed reports the negated signal number (`-9` for SIGKILL), CPython's `Popen.returncode` spelling; `subprocess.run`'s older helper reports `128+N` there, which divergence 71's entry predates and #1345 does not change.

## Pipes

`proc.stdout` answers a `PsPipe` wrapper (the record plus a stream selector) allocated per read of the attribute; the buffers live on the child record, so every spelling of the attribute reads the same stream and a parked surplus survives across spellings. The methods: `read(n)` (n <= 0 reads to EOF, a positive n parks the surplus), `readline()` (one line, trailing newline kept), `write(data)` (stdin only, the bytes argument reaches the ABI as its length and pointer), `flush()` (a no-op, write(2) has no buffer), `close()` (the write end goes away, the child reading stdin sees EOF), and `for line in proc.stdout` (the lowering's pipe-iteration loop in `lower/_stmt_b.py`). A read or write on a stream that was not piped raises `ValueError` at run time; CPython answers `None` for that stream attribute and the read on the `None` raises `AttributeError`, so this is divergence 93's value-shape difference, and the probe records it by asserting the fallback line rather than diffing bytes.

## What fences

- `communicate()` refuses with `PS2001`: it drains both pipes to EOF and blocks until the child exits, exactly the shape the incremental reads replace.
- The spawn keywords beyond the pipe mask (`env=`, `cwd=`, `shell=`, `start_new_session=`, `text=`, `universal_newlines=`) refuse with `PS2003` naming issue #1344; the pipe keywords accept only `subprocess.PIPE`.
- The `wait(timeout=)` bound and the `read(n)` count are compile-time literals (the runtime helper takes the bound as an argument, so a computed value would be silently dropped); a computed one refuses with `PS2003`, the rule `subprocess.run(timeout=)` already set.
- `send_signal(sig)` takes a `signal.SIG*` constant (the closed subset of divergence 68); a bare int refuses. A signal to a child that already exited is a no-op, CPython's shape.
- The handle is native-only: wasm32 refusals name the `ps_popen_`/`ps_pipe_` symbols (WASI has no fork/exec) and the mobile targets fence at compile time with the mobile guard.

## Files

Runtime `src/pyscriptc/runtime/ps_popen.c` (mobile-excluded, wasm-guarded); lowering `src/pyscriptc/restlower/_popen.py`; class table and field reads in `src/pyscriptc/stdmore.py`, `src/pyscriptc/lower/_classes.py`, `src/pyscriptc/lower/_call_attr.py`; the pipe iteration loop in `src/pyscriptc/lower/_stmt_b.py`; the ABI whitelist in `src/pyscriptc/emit/llvm/_abi.py`; the vendored stub alignment (`IO[AnyStr]` streams, `returncode: int | None`) in `src/pyscriptc/check.py`; the probe `probes/differential/subprocess_popen.py`; divergence 93 in `docs/reference/divergences.md` with the ledger row in `scripts/gen_divergence_ledger.py`.
