"""Regenerate docs/reference/divergence-ledger.json.

The ledger holds one row per numbered divergence in
docs/reference/divergences.md and one row per finding D1-D12 of the
2026-10-24 CPython divergence audit (.agents/plans/). Each row carries the
seam, a pointer to the prose, a probe source under probes/divergences/, and
the recorded bytes for both sides: CPython (stdout, stderr, exit) and
pyscriptc (stage, stdout, stderr, exit; stage is "compile" when the probe
fences). tests/test_divergence_ledger.py replays every row and fails when
the recorded bytes no longer match, so a fixed or moved divergence is a
reviewed change, not a silent one.

Rows that cannot be probed on the host (cross-target, network, curated
distributions, multi-module shapes, behaviours only visible against
`python -O` or the linked interpreter) carry probe: null and a note; the
test skips them.

Recorded bytes are normalized before they are stored and before they are
compared: the probe's absolute path becomes probes/divergences/<name> and
the scratch cwd becomes <cwd>. Everything else is the raw bytes.

Run: python scripts/gen_divergence_ledger.py [--check] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "probes" / "divergences"
LEDGER = ROOT / "docs" / "reference" / "divergence-ledger.json"
DOC = "docs/reference/divergences.md"
AUDIT = ".agents/plans/2026-10-24-cpython-divergence-audit.md"

ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
TIMEOUT = 180


def _row(
    id: str,
    seam: str,
    probe: str | None,
    *,
    listed: bool = True,
    docs: str = DOC,
    note: str | None = None,
    oracle_args: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "seam": seam,
        "listed": listed,
        "docs": docs,
        "probe": probe,
        "note": note,
        "oracle_args": oracle_args,
    }


ROWS: list[dict[str, Any]] = [
    _row("D1", "int is i64; overflow aborts instead of growing a big int", "d01_int_overflow.py", docs=f"{DOC}#1"),
    _row(
        "D2",
        "// and % by zero: ZeroDivisionError message follows the running interpreter's minor",
        "d02_floordiv_zero.py",
        docs=f"{DOC}#2",
    ),
    _row(
        "D3",
        "str model mid-migration: is* predicates needing Unicode category tables raise NotImplementedError",
        "d03_str_predicates.py",
        docs=f"{DOC}#3",
    ),
    _row("D4", "re subset: L/T/DEBUG and unknown flag bits raise ValueError", "d04_re_flags.py", docs=f"{DOC}#4"),
    _row(
        "D5",
        "json.dumps escapes the non-ASCII text like CPython's default and ensure_ascii=False writes it raw; "
        "the reader accepts both forms and a lone surrogate in an escape raises the JSONDecodeError family",
        "d05_json_ascii.py",
        docs=f"{DOC}#5",
    ),
    _row("D6", "set and frozenset iterate in insertion order, not hash order", "d06_set_order.py", docs=f"{DOC}#6"),
    _row(
        "D7",
        "after for i in range(0) the loop target reads as 0 instead of raising",
        "d07_range0_target.py",
        docs=f"{DOC}#7",
    ),
    _row(
        "D8",
        "a subclass __init__ that never assigns an inherited field leaves the type's zero value",
        "d08_inherited_field.py",
        docs=f"{DOC}#8",
    ),
    _row(
        "D9",
        "a user exception class runs its own __init__ on raise and carries the custom fields "
        "in the record's payload (issue #448); the residual seam is e.args, which holds the "
        "message list instead of the constructor arguments",
        "d09_user_exc_init.py",
        docs=f"{DOC}#9",
    ),
    _row(
        "D10",
        "Class.method(...) on classmethod/staticmethod does not pass the class object",
        "d10_classmethod.py",
        docs=f"{DOC}#10",
    ),
    _row(
        "D11",
        "shutil.copyfile of the same path raises RuntimeError, not SameFileError",
        "d11_shutil_samefile.py",
        docs=f"{DOC}#11",
    ),
    _row(
        "D12",
        "assert is always evaluated; a compiled binary has no -O",
        "d12_assert_opt.py",
        docs=f"{DOC}#12",
        oracle_args=["-O"],
    ),
    _row("D13", "is on a scalar is refused; only x is None compiles (as ==)", "d13_is_scalar.py", docs=f"{DOC}#13"),
    _row(
        "D14",
        "ABC/ABCMeta/Protocol/@abstractmethod compile as plain classes with no runtime semantics",
        "d14_abc.py",
        docs=f"{DOC}#14",
    ),
    _row(
        "D15",
        "calls through embedded CPython follow the interpreter the binary links, not the pin",
        None,
        docs=f"{DOC}#15",
        note="the embedded interpreter's bytes are build- and platform-dependent, so they are not ledger-recordable",
    ),
    _row(
        "D16",
        "dotenv compiles a closed subset of python-dotenv's parsing rules",
        None,
        docs=f"{DOC}#16",
        note="the comparison is against python-dotenv, which the pinned oracle does not ship",
    ),
    _row(
        "D17",
        "yaml.safe_load covers a closed YAML subset, not all of PyYAML",
        None,
        docs=f"{DOC}#17",
        note="the comparison is against PyYAML, which the pinned oracle does not ship",
    ),
    _row(
        "D18",
        "https:// rides the system libcurl; PSC_CURL_DYNAMIC=1 raises OSError instead",
        None,
        docs=f"{DOC}#18",
        note="needs a live TLS endpoint; connection-refused text is platform-dependent",
    ),
    _row(
        "D19",
        "generator.send(v) on an unstarted generator ignores v where CPython raises TypeError",
        "d19_gen_send.py",
        docs=f"{DOC}#19",
    ),
    _row(
        "D20",
        "generator.throw(E) raises E out of the call site; the generator's own except handlers never run",
        "d20_gen_throw.py",
        docs=f"{DOC}#20",
    ),
    _row(
        "D21",
        "yield from discards StopIteration.value; statement form never reads it",
        None,
        docs=f"{DOC}#21",
        note="not observable: the only lowered form is a statement, so no program reads the value",
    ),
    _row(
        "D22",
        "pyscriptc-jwt compiles the HMAC family only",
        None,
        docs=f"{DOC}#22",
        note="needs the curated pyscriptc-jwt distribution on both sides",
    ),
    _row(
        "D23",
        "pyscriptc-orjson compiles orjson.dumps/loads over the json subset",
        None,
        docs=f"{DOC}#23",
        note="needs the curated pyscriptc-orjson distribution on both sides",
    ),
    _row(
        "D24",
        "@contextmanager: the gen.throw path is unreachable, so the generator's except cannot see the exception",
        "d24_contextmanager.py",
        docs=f"{DOC}#24",
    ),
    _row("D25", "a finally pending when a generator is abandoned does not run", "d25_gen_finally.py", docs=f"{DOC}#25"),
    _row(
        "D26",
        "second-thread threading is native-only; the wasm32 link refuses it",
        None,
        docs=f"{DOC}#26",
        note="wasm32-target divergence; the host lane is unaffected",
    ),
    _row(
        "D27",
        "the HTTP client is a closed urllib.request subset; urlretrieve, openers and proxies are refused",
        "d27_urlretrieve.py",
        docs=f"{DOC}#27",
    ),
    _row(
        "D28",
        "https:// raises OSError: the native client speaks plaintext HTTP/1.1 only",
        None,
        docs=f"{DOC}#28",
        note="needs a live TLS endpoint; superseded in shape by D18's libcurl lane",
    ),
    _row(
        "D29",
        "ssl, http.client and http.server do not compile (PS2001); socket lowers its closed TCP subset",
        "d29_ssl.py",
        docs=f"{DOC}#29",
        note="the socket module lowers since #1270 (divergence 77); the d29_socket.py probe of the "
        "all-fenced era moved to the differential lane (probes/differential/socket_tcp.py, byte-identical "
        "over the subset)",
    ),
    _row(
        "D30",
        "urlopen and Request raise URLError on wasm32 and windows targets",
        None,
        docs=f"{DOC}#30",
        note="cross-target divergence; the host lane is unaffected",
    ),
    _row(
        "D31",
        "wasm32-wasi-musl produces a preview1 command module with a smaller than POSIX surface",
        None,
        docs=f"{DOC}#31",
        note="cross-target divergence",
    ),
    _row(
        "D32",
        "wasm32-wasi-p2 produces a WASI 0.2 component; D31's constraints apply unchanged",
        None,
        docs=f"{DOC}#32",
        note="cross-target divergence",
    ),
    _row(
        "D33",
        "*-windows-* targets produce a Win32 PE with a smaller than POSIX surface",
        None,
        docs=f"{DOC}#33",
        note="cross-target divergence; emitted binaries are never executed",
    ),
    _row(
        "D34",
        "only refusals in reached dependency modules stop the compile; unreached modules are pruned",
        None,
        docs=f"{DOC}#34",
        note="needs a multi-module package shape, which single-file probes do not cover",
    ),
    _row(
        "D35",
        "__getattr__ compiles for unknown-name reads only; the rest of the dynamic attribute protocol is refused",
        "d35_setattr.py",
        docs=f"{DOC}#35",
    ),
    _row(
        "D36",
        "the shortest float spelling sometimes picks an exponent form CPython does not use at that magnitude",
        "d36_float_repr.py",
        docs=f"{DOC}#36",
    ),
    _row(
        "D37",
        "round(x, n) outside 0 to 8: a constant fences at compile time, a variable raises ValueError",
        "d37_round_fence.py",
        docs=f"{DOC}#37",
    ),
    _row(
        "D38",
        "newline= is refused on open; encoding= compiles a constant-subset read path",
        "d38_open_newline.py",
        docs=f"{DOC}#38",
    ),
    _row(
        "D39",
        "int(text, base) covers bases 2 to 36; base 0 and out-of-range constants stop the compile",
        "d39_int_base0.py",
        docs=f"{DOC}#39",
    ),
    _row(
        "D40",
        "subprocess.run check=True raises the RuntimeError stand-in, not CalledProcessError",
        "d40_subprocess.py",
        docs=f"{DOC}#40",
    ),
    _row(
        "D68",
        "os.kill/os.killpg and the signal POSIX constants lower on POSIX hosts; handler forms fence (PS2001)",
        None,
        docs=f"{DOC}#68",
        note="the wasm/windows target refusals are cross-target; the host lane is byte-identical "
        "(probes/differential/os_kill_signal.py)",
    ),
    _row(
        "D41",
        "fallback mode routes refused functions and classes to embedded CPython",
        None,
        docs=f"{DOC}#41",
        note="needs --fallback=cpython; the embedded interpreter's bytes follow the build",
    ),
    _row(
        "D42",
        "under fallback the module top level is all-or-nothing and sys.argv[0] is the binary path",
        None,
        docs=f"{DOC}#42",
        note="needs --fallback=cpython; the embedded interpreter's bytes follow the build",
    ),
    _row(
        "D43",
        "an argparse type=Path option without required=True or a str default stops the compile",
        "d43_argparse.py",
        docs=f"{DOC}#43",
    ),
    _row(
        "D44",
        "a local bound to a value then assigned None is not widened to Optional",
        "d44_optional.py",
        docs=f"{DOC}#44",
    ),
    _row(
        "D45",
        "shapes refused after the mechanical-delta pass, each as its own PS code",
        "d45_set_dict.py",
        docs=f"{DOC}#45",
    ),
    _row(
        "D46",
        "a fallback bundle stages non-Python program files beside the binary",
        None,
        docs=f"{DOC}#46",
        note="needs a multi-file program and a relocated run; single-file probes do not cover it",
    ),
    _row(
        "D47",
        "os.walk materializes before the loop, so a dirnames edit does not prune; plus json.load/dump and the clocks",
        "d47_oswalk.py",
        docs=f"{DOC}#47",
    ),
    _row(
        "D48",
        "an --embed-python=bundle output carries a trimmed runtime",
        None,
        docs=f"{DOC}#48",
        note="needs --embed-python=bundle and a staged run",
    ),
    _row(
        "D49",
        "the program island carries a guarded precompiled code cache",
        None,
        docs=f"{DOC}#49",
        note="needs an island build and a run against a moved interpreter; behavior never differs, only start time",
    ),
    _row(
        "D50",
        "a module-scope unannotated container mutated or aliased is fenced (PS2002)",
        "d50_container_const.py",
        docs=f"{DOC}#50",
    ),
    _row(
        "D51",
        "list += and *= desugar to a fresh-concat rebind, not in-place",
        "d51_iadd_alias.py",
        docs=f"{DOC}#51",
    ),
    _row(
        "D52",
        "itertools.tee returns eager list copies; batched yields lists, not tuples",
        "d52_itertools_tee_batched.py",
        docs=f"{DOC}#52",
    ),
    _row(
        "D53",
        "a class decorator other than @dataclass is erased: the wrap never runs (advisory PS3099 note)",
        "d53_class_decorator_erased.py",
        docs=f"{DOC}#53",
    ),
    _row(
        "D54",
        "a method or function decorator outside the closed set that does not synthesize behavior "
        "is erased: the wrap never runs (advisory PS3099 note)",
        "d54_method_decorator_erased.py",
        docs=f"{DOC}#54",
    ),
    _row(
        "D55",
        "the uuid subset covers uuid4, uuid1, uuid5 and UUID(text); every other name is refused",
        "d55_uuid.py",
        docs=f"{DOC}#55",
    ),
    _row(
        "D56",
        "the binascii subset covers unhexlify and hexlify; every other name is refused",
        "d56_binascii.py",
        docs=f"{DOC}#56",
    ),
    _row(
        "D57",
        "the shlex subset covers split, quote and join; the shlex class and modes are refused",
        "d57_shlex.py",
        docs=f"{DOC}#57",
    ),
    _row(
        "D58",
        "the secrets subset covers token_hex, token_bytes and randbelow; every other name is refused",
        "d58_secrets.py",
        docs=f"{DOC}#58",
    ),
    _row(
        "D59",
        "the island plan is conservative past the module that forced it: untyped imports demote the "
        "whole module top level and, transitively through calls, every function naming them",
        None,
        note="needs a multi-module package shape, which single-file probes do not cover",
        docs=f"{DOC}#59",
    ),
    _row(
        "D60",
        "math.factorial(n) is an i64 overflow past 20 instead of growing a big int",
        "d60_factorial_overflow.py",
        docs=f"{DOC}#60",
    ),
    _row(
        "D61",
        "float text output is shortest-round-trip without CPython's fixed-exponent rule: 180.0 prints as 1.8e+02",
        "d61_float_print.py",
        docs=f"{DOC}#61",
    ),
    _row(
        "D62",
        "struct.calcsize and struct.unpack_from need a literal format with an explicit byte-order prefix; "
        "a short buffer raises ValueError, not struct.error",
        "d62_struct_unpack_from.py",
        docs=f"{DOC}#62",
    ),
    _row(
        "D63",
        "the random subset is a CPython-compatible Mersenne Twister; the Random class and rarer draws are refused",
        "d63_random.py",
        docs=f"{DOC}#63",
    ),
    _row(
        "D64",
        "the pyscriptc_arrow adapter widens every numeric element to double, so an int64 above 2**53 "
        "loses integer precision; the Arrow types outside the primitive subset are refused",
        None,
        docs=f"{DOC}#64",
        note="the comparison needs the vendored nanoarrow bundle and a live Arrow implementation, so the "
        "differential lives in tests/test_ffi_arrow_adapter.py and tests/test_ffi_arrow_pilot.py "
        "rather than in a probe the pinned oracle can run",
    ),
    _row(
        "D65",
        "the statistics subset runs double arithmetic for the float-valued seams: stdev/pstdev differ "
        "by one ulp from CPython's exact-rational sqrt, and the mean/median/variance family over "
        "list[int] returns float where CPython returns int (equal value, different repr)",
        "d65_statistics.py",
        docs=f"{DOC}#65",
    ),
    _row(
        "D66",
        "csv.DictReader is an eager list and a long record raises ValueError, not the None restkey",
        "d66_csv_dict_reader.py",
        docs=f"{DOC}#66",
    ),
    _row(
        "D67",
        "an island binary that links the build host's libpython refuses the compile (PS4304) "
        "unless --allow-host-libpython accepts it",
        None,
        docs=f"{DOC}#67",
        note="needs a toolchain and the host's shared libpython; otool/readelf read the artifact after the link",
    ),
    _row(
        "D69",
        "the archive read surface: r:bz2 and r:xz raise RuntimeError at run time (no bzip2 or lzma link), "
        "and the extractfile KeyError wording is the runtime's",
        "d69_archive.py",
        docs=f"{DOC}#69",
    ),
    _row(
        "D70",
        "dispatch is static: a subclass value in a base-annotated parameter is refused (PS1003), where CPython "
        "dispatches on the value's real class",
        "d69_covariant_slot.py",
        docs=f"{DOC}#70",
    ),
    _row(
        "D71",
        "the TimeoutExpired record holds the code and the message only: the attributes CPython's exception "
        "carries (cmd, timeout, output) are not readable and e.args holds the message list",
        "d71_subprocess_timeout.py",
        docs=f"{DOC}#71",
    ),
    _row(
        "D72",
        "the async subprocess handle reads -1 for a running child's returncode, where CPython reads None; "
        "the subset has no optional int and no stdin pipe",
        "d72_asyncio_subprocess.py",
        docs=f"{DOC}#72",
    ),
    _row(
        "D73",
        "a union annotation erases to its first arm, so a call that passes a later arm's value stops the "
        "compile with PS1003 where CPython runs the program",
        "d73_union_first_arm.py",
        docs=f"{DOC}#73",
    ),
    _row(
        "D74",
        "os.system answers CPython's raw POSIX wait status (the exit code in the high byte) and the command "
        "rides the host shell; the bytes form and the PathLike form are refused (PS1003)",
        None,
        docs=f"{DOC}#74",
        note="the host lane is byte-identical (probes/differential/os_system.py); the wasm link-time "
        "refusal, the mobile compile-time fence and the Windows CRT shell status are cross-target",
    ),
    _row(
        "D75",
        "a JSON integer literal too large for i64 parses as the dynamic value's float arm, where CPython's "
        "json grows an arbitrary-precision int",
        "d75_json_dynval_number.py",
        docs=f"{DOC}#75",
    ),
    _row(
        "D76",
        "the asyncio subprocess pipe form: stdout=/stderr= take only asyncio.subprocess.PIPE (PS2003 "
        "otherwise) and communicate answers the (stdout, stderr) pair of bytes with an inherited stream "
        "reading as the empty bytes, where CPython answers None there",
        None,
        docs=f"{DOC}#76",
        note="the host lane is byte-identical (probes/differential/asyncio_subprocess_pipes.py); the "
        "None-versus-empty-bytes seam and the missing stdin pipe are the rest of the entry",
    ),
    _row(
        "D77",
        "the socket subset is the blocking TCP surface over IPv4 (issue #1270): the loopback echo is "
        "byte-identical; IPv6, UDP, non-blocking mode, makefile and the rest of the module fence (PS2001) "
        "and a computed family or endpoint fences (PS1003) where CPython accepts them",
        None,
        docs=f"{DOC}#77",
        note="the host lane is byte-identical (probes/differential/socket_tcp.py); the fences, the "
        "IPv4-literal-only address and the native-only targets are the rest of the entry",
    ),
    _row(
        "D78",
        "the email read subset: get_payload() on a multipart part raises ValueError (CPython answers the list of "
        "the child parts) and an encoded word in an undecoded charset keeps the raw word",
        "d78_email.py",
        docs=f"{DOC}#78",
    ),
    _row(
        "D79",
        "asyncio.gather answers the list of the results in argument order (issue #1278), the way CPython "
        "does, so the result annotation is list[T]; return_exceptions=True fences (PS2003) where CPython "
        "lands the exceptions in the list",
        None,
        docs=f"{DOC}#79",
        note="the host lane is byte-identical (probes/differential/asyncio_gather_list.py); the "
        "return_exceptions fence and the one-result-type rule (PS2002) are the rest of the entry",
    ),
    _row(
        "D92",
        "the native signal handler surface (issue #1347): signal.signal installs a sigaction handler "
        "whose compiled function runs at a safepoint with the signal number only, where CPython calls "
        "the handler with the frame too and returns the previous handler",
        None,
        docs=f"{DOC}#92",
        note="the probe is the differential one (probes/differential/signal_handler.py); the lane's "
        "bytes are identical, the seams are the handler arity, the delivery point and the lost "
        "return value",
    ),
    _row(
        "D98",
        "the compare and ordering operators resolve their dunder from the operand classes' static mro "
        "with a fixed order (left definer, then the reflected dunder, then the identity fallback), where "
        "CPython tries the reflected operand at run time when the dunder answers NotImplemented; a return "
        "of NotImplemented fences at its return (PS2002), so a class pair that relies on the run-time "
        "fall-through does not compile, and the identity fallback answers False for unrelated classes "
        "where the pair would still be decidable at run time",
        None,
        docs=f"{DOC}#98",
        note="the resolved surface is the differential probe (probes/differential/protocol_dispatch.py); "
        "the NotImplemented fence and the mixed-class fence (PS2004) are the rest of the entry",
    ),
    _row(
        "D99",
        "hash(x) on a class resolves __hash__ statically: a definer's int result passes through with -1 "
        "mapped to -2, a class that defines __eq__ without __hash__ fences hash() at the call (PS2004) "
        "where CPython raises TypeError at run time, and with neither dunder defined the value hashes by "
        "address, so the numeric hash is not reproducible across runs or implementations in either; dict "
        "and set keys stay int or str and a class key is refused (PS2002)",
        None,
        docs=f"{DOC}#99",
        note="the hash-with-definer and truthiness surfaces are in the differential probe "
        "(probes/differential/protocol_dispatch.py); the address-dependent identity hash and the "
        "unhashable fence are the rest of the entry",
    ),
    _row(
        "D91",
        "math.sumprod over int sequences is i64 (divergence 1): a result past the i64 range raises "
        "OverflowError where CPython grows a big int",
        "d91_math_sumprod_int_overflow.py",
        docs=f"{DOC}#91",
    ),
    _row(
        "D93",
        "the subprocess.Popen child handle (issue #1345): the spawn, the pid, the poll, the bounded wait with "
        "its TimeoutExpired, the signals and the incremental pipe reads are byte-identical to the pinned "
        "CPython; a stream that was not piped answers a handle whose read raises ValueError where CPython "
        "answers None, and the communicate call, shell=, a counted text-mode read and the computed bounds fence; "
        "the spawn environment (env=, cwd=, start_new_session=, text=, issue #1344) is byte-identical",
        None,
        docs=f"{DOC}#93",
        note="the host lane is byte-identical (probes/differential/subprocess_popen.py and "
        "probes/differential/subprocess_popen_env.py); the unpiped-stream "
        "read, the communicate fence (PS2001) and the literal-bound fences (PS2003) are the rest of the entry",
    ),
    _row(
        "D80",
        "the http.server handler subset (issue #1270): the loopback request is byte-identical; headers.get "
        "answers the empty string where CPython answers None, the handler instance persists across the "
        "requests of one serve call where CPython builds one per request, and the runtime answers no "
        "log_message",
        None,
        docs=f"{DOC}#80",
        note="the host lane is byte-identical (probes/differential/http_server_loopback.py); the "
        "headers.get seam, the per-serve handler instance, the missing log_message and the fences are "
        "the rest of the entry",
    ),
    _row(
        "D81",
        "the asyncio stream surface (issue #1281): open_connection and the StreamReader / StreamWriter pair "
        "over the TCP socket runtime; readexactly raises RuntimeError where CPython raises IncompleteReadError, "
        "read(n) answers the bytes the buffer holds when the socket answered, and the surface is native-only",
        None,
        docs=f"{DOC}#81",
        note="the host lane is byte-identical (probes/differential/asyncio_streams.py); the "
        "readexactly exception class, the read(n) short read and the target fences are the rest of the entry",
    ),
    _row(
        "D82",
        "the verify oracle is the target's minor, not its exact patch: any 3.14.x is accepted silently, "
        "and an older oracle (3.11 through 3.13) is opt-in through --oracle-version or PYSC_ORACLE_ANY=1 "
        "(issue #1290)",
        None,
        docs=f"{DOC}#82",
        note="the oracle choice is a tool input, not a program behaviour: the ledger cannot record it as "
        "recorded bytes, so the entry names the contract instead",
    ),
    _row(
        "D83",
        "the asyncio server surface (issue #1281, slice 2): start_server and the Server handle over the TCP "
        "socket runtime; sockets answers the one listening socket the subset binds, and close() while "
        "serve_forever() is parked raises CancelledError at the await",
        None,
        docs=f"{DOC}#83",
        note="the host lane is byte-identical (probes/differential/asyncio_server_loopback.py); the "
        "sockets shape, the close cancellation and the target fences are the rest of the entry",
    ),
    _row(
        "D84",
        "time.process_time (issue #503) reads the host's process CPU clock (CLOCK_PROCESS_CPUTIME_ID), the "
        "CPython semantics; on wasm32 it reads the wall clock instead, because wasi-libc declares no process "
        "CPU clock",
        None,
        docs=f"{DOC}#84",
        note="the host lane is byte-identical (probes/differential/time_process_time.py); the wasm32 wall-clock "
        "fallback is the rest of the entry",
    ),
    _row(
        "D85",
        "an import outside the closed import table routes to the island lane by default (issue #1308): "
        "the compile records an advisory note instead of a PS2002 fence, and the module runs in the "
        "embedded interpreter",
        None,
        docs=f"{DOC}#85",
        note="the host lane is byte-identical (probes/differential/island_import_routing.py); the "
        "routing itself is a compile-time fact the ledger cannot record as program bytes",
    ),
    _row(
        "D86",
        "the counter/ordered/defaultdict marks ride the dict annotation, not the value: under a plain "
        "dict[K, V] boundary the value reprs as a plain dict and a d[k] miss raises KeyError (issue #491)",
        "d86_collections_marks.py",
        docs=f"{DOC}#86",
    ),
    _row(
        "D88",
        "the ndarray pack's reshape returns a contiguous copy, never numpy's shared-buffer view; "
        "the whole-array reduction answers a scalar, the shape a list and the axis reductions drop the axis",
        None,
        docs=f"{DOC}#88",
        note="the CPython side is real numpy, which the pinned oracle interpreter does not carry; "
        "the byte comparison runs in the differential lane instead "
        "(probes/differential/ndarray_subset.py, numpy's spelling on both sides)",
    ),
    _row(
        "D87",
        "math.comb, math.perm and math.prod are i64 (divergence 1): a result past the i64 range raises "
        "OverflowError where CPython grows a big int",
        "d87_math_int_overflow.py",
        docs=f"{DOC}#87",
    ),
    _row(
        "D89",
        "text I/O is strict UTF-8 at every OS boundary: argv, environ and OS names that are not UTF-8 raise "
        "UnicodeDecodeError (CPython carries them as surrogate escapes), and a text file decodes line by line "
        "so the error rises at the bad line (issue #478)",
        "d88_utf8_boundary.py",
        docs=f"{DOC}#89",
    ),
    _row(
        "AUDIT-D1",
        "list.sort() on a module-scope list was a no-op (audit D1)",
        "ad01_sort.py",
        listed=False,
        docs=AUDIT,
    ),
    _row(
        "AUDIT-D2",
        "module-scope mutable containers had value semantics (audit D2)",
        "ad02_alias.py",
        listed=False,
        docs=AUDIT,
    ),
    _row(
        "AUDIT-D3", "sum() over floats was a naive left fold (audit D3)", "ad03_sum_floats.py", listed=False, docs=AUDIT
    ),
    _row(
        "AUDIT-D4",
        "round(x, n) with n < 0 raised instead of rounding (audit D4)",
        "ad04_round_neg.py",
        listed=False,
        docs=AUDIT,
    ),
    _row(
        "AUDIT-D5",
        "round(x, n) with n > 8 raised instead of rounding (audit D5)",
        "ad05_round_big.py",
        listed=False,
        docs=AUDIT,
    ),
    _row(
        "AUDIT-D6",
        "round(x, n) scaled before rounding instead of rounding the exact decimal (audit D6)",
        "ad06_round_exact.py",
        listed=False,
        docs=AUDIT,
    ),
    _row(
        "AUDIT-D7", "assert raised Exception, not AssertionError (audit D7)", "ad07_assert.py", listed=False, docs=AUDIT
    ),
    _row(
        "AUDIT-D8",
        "a str tuple display omitted the quotes on its str elements (audit D8)",
        "ad08_tuple_str.py",
        listed=False,
        docs=AUDIT,
    ),
    _row("AUDIT-D9", "a NUL byte inside a str truncated the value (audit D9)", "ad09_nul.py", listed=False, docs=AUDIT),
    _row(
        "AUDIT-D10",
        "text-mode reads applied no universal newline translation (audit D10)",
        "ad10_newline.py",
        listed=False,
        docs=AUDIT,
    ),
    _row(
        "D90",
        "int / int past 2**53 divides the widened operands, so the quotient rounds before the division; "
        "CPython divides the exact integers and rounds once",
        "d90_int_truediv_wide.py",
        docs=f"{DOC}#90",
    ),
    _row(
        "D94",
        "argparse subparsers share one namespace: an unchosen subcommand's attribute reads its default "
        "(CPython raises AttributeError) and an absent optional subcommand dest reads the empty string "
        "(CPython reads None)",
        "d94_argparse_subparser_namespace.py",
        docs=f"{DOC}#94",
    ),
    _row(
        "D95",
        "argparse nargs: a list-valued option that does not appear reads an empty list (CPython None), and a "
        "nargs='?' argument needs a default (an absent value would be None)",
        "d95_argparse_nargs_absent.py",
        docs=f"{DOC}#95",
    ),
    _row(
        "D96",
        "the sqlite3 subset (issue #1343): the row is a list[object] in column order where CPython's cursor "
        "row is a tuple, cur.description answers the list of names where CPython answers a tuple of 7-tuples, "
        "cur.lastrowid answers 0 where CPython answers None on a cursor no execute ran on, connect accepts the "
        "path, the timeout and only isolation_level=None (no row_factory), the refused statement is RuntimeError "
        "where CPython raises the sqlite3.Error subclasses, and db.close() consumes the handle so a later "
        "statement is the PS4010 compile fence",
        None,
        docs=f"{DOC}#96",
        note="the host lane is byte-identical on the ? binding of an int, a float, a str and a None, the NULL "
        "round trip, cur.rowcount, cur.lastrowid and the description's presence and count "
        "(probes/differential/sqlite3_params.py); the row shape, the description element shape, the lastrowid "
        "None arm, the connect keywords, the exception class and the ownership fences are the rest of the "
        "entry",
    ),
    _row(
        "D97",
        "a fixed-shape tuple default and a fixed-shape tuple class attribute (issue #444) materialize a fresh "
        "record per read, where CPython holds one tuple object: C.pair is C.pair is True in CPython and False "
        "in the lowered program, and a dataclass field's tuple default is one shared tuple there and a fresh "
        "record per construction here",
        "d97_tuple_default_identity.py",
        docs=f"{DOC}#97",
        note="the values are element for element equal, so only an identity comparison tells the two apart",
    ),
    _row(
        "D100",
        "an exception raised inside an asyncio.to_thread worker does not reach the awaiter (issue #400): the "
        "worker record carries the result value and a done flag and no exception slot, so the worker's raise "
        "ends the process where the worker ran, where CPython hands the exception to the awaiting task and "
        "the except clause runs",
        "d100_to_thread_worker_exception.py",
        docs=f"{DOC}#100",
    ),
    _row(
        "D101",
        "the enum member is the value itself, not a member object (issue #451): print(Color.RED) prints the "
        "value where CPython prints the member spelling, an identity compare over the same member twice "
        "compares two values where CPython compares one object, and a flag combination is the integer sum "
        "where CPython answers a member (whose .name has no lowered answer)",
        "d101_enum_member_value_identity.py",
        docs=f"{DOC}#101",
        note="the resolved member surface is the differential probe (probes/differential/enum_members.py); "
        "the value/name reads, the enum methods and properties and the StrEnum and flag bases are byte-identical",
    ),
    _row(
        "D102",
        "a write to a cached property name is a compile fence (issue #440): CPython stores the value into "
        "the instance __dict__ and the name then answers the stored value without ever running the getter, "
        "where the lowered program keeps the cached value in the struct slot and has no instance dictionary "
        "to write; the class-level read C.y returns the descriptor there and fences here, the same rule "
        "every method name follows",
        "d102_cached_property_write.py",
        docs=f"{DOC}#102",
    ),
    _row(
        "D103",
        "a unicodedata argument that is not a one-character string raises ValueError where CPython raises "
        "TypeError naming the length (issue #476): the ABI carries the buffer length, so the one-character "
        "check is the runtime's own raise; the value paths answer over the whole code-point range from the "
        "generated Unicode 16.0.0 tables, so the range the old Latin-1 table could not answer is identical",
        "d103_unicodedata_codepoints.py",
        docs=f"{DOC}#103",
    ),
    _row(
        "D104",
        "the G3b generator shapes (issue #442): a generator def nested inside a plain function lifts "
        "to the enclosing module's scope (the captured names ride the closure environment), a nested "
        "generator drives the send protocol, and a pass-through-decorated generator keeps the "
        "frame-returning callable type, so a for over a call to the decorated name dispatches on the "
        "frame's real __iter__/__next__; a generator def nested inside a resumable and a lambda that "
        "carries yield keep the PS2002 fence, and a decorator that is not a pass-through keeps its own",
        "d104_generator_g3b.py",
        docs=f"{DOC}#104",
    ),
    _row(
        "AUDIT-D11", "json.dumps does not escape non-ASCII (audit D11)", "ad11_json_ascii.py", listed=False, docs=AUDIT
    ),
    _row(
        "AUDIT-D12",
        "a str literal above Latin-1 was emitted as raw bytes (audit D12)",
        "ad12_str_utf8.py",
        listed=False,
        docs=AUDIT,
    ),
    _row(
        "D104",
        "object and Any are not interchangeable (issue #1226): CPython treats the two as aliases for the top "
        "type and pyscriptc gives them two meanings, object lowers as the tagged dynamic value (the closed "
        "sum None | bool | int | float | str | list | dict) while Any keeps the unresolved slot with its "
        "PS1107 read fence and a bare Any annotation keeps the PS1104 fence; a value whose arm is outside "
        "the sum (bytes, a tuple, a set, a user class instance) refuses the box with PS2002 where CPython "
        "accepts everything",
        "d104_object_any_not_interchangeable.py",
        docs=f"{DOC}#104",
        note="the running surface (the container reads, the isinstance narrowing, the class attribute and "
        "the JSON round trip in the probe) is byte-identical; the seams are the compile-time fences, so "
        "the probe runs the compiling side and the fences are reviewed in the prose",
    ),
]


def oracle_bin() -> str:
    """The CPython 3.14 oracle: the pinned uv interpreter when present."""
    pinned = Path.home() / ".local/share/uv/python"
    candidates = sorted(pinned.glob("cpython-3.14.*-*/bin/python3"))
    if candidates:
        return str(candidates[-1])
    found = shutil.which("python3.14")
    return found or sys.executable


_ORACLE_PREFIX: str | None = None


def _oracle_prefix() -> str:
    """The oracle interpreter's install root, for traceback normalization."""
    global _ORACLE_PREFIX
    if _ORACLE_PREFIX is None:
        out = subprocess.run(
            [oracle_bin(), "-c", "import sys; print(sys.base_prefix)"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        _ORACLE_PREFIX = out.stdout.strip()
    return _ORACLE_PREFIX


def _normalize(text: str, probe: Path, workdir: Path) -> str:
    """Make recorded bytes stable: the probe path, scratch cwd and oracle
    install root are volatile (tracebacks name stdlib files)."""
    return (
        text.replace(str(probe), str(probe.relative_to(ROOT)))
        .replace(str(workdir), "<cwd>")
        .replace(_oracle_prefix(), "<oracle>")
    )


def run_oracle(probe: Path, workdir: Path, args: list[str] | None = None) -> dict[str, Any]:
    """Run the probe under the pinned CPython and return the recorded side."""
    proc = subprocess.run(
        [oracle_bin(), *(args or []), str(probe)],
        cwd=workdir,
        capture_output=True,
        timeout=TIMEOUT,
    )
    return {
        "stage": "run",
        "exit": proc.returncode,
        "stdout": _normalize(proc.stdout.decode("utf-8", "replace"), probe, workdir),
        "stderr": _normalize(proc.stderr.decode("utf-8", "replace"), probe, workdir),
    }


def run_pyscriptc(probe: Path, workdir: Path) -> dict[str, Any]:
    """Compile and run the probe and return the recorded side.

    A fence is a stage "compile" row: the compile's exit code and stderr are
    recorded, which pins the refusal the same way a run pins bytes.
    """
    binary = workdir / (probe.stem + ".bin")
    proc = subprocess.run(
        [sys.executable, "-m", "pyscriptc", "compile", str(probe), "--emit", "bin", "-o", str(binary)],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        timeout=TIMEOUT,
    )
    if proc.returncode != 0:
        return {
            "stage": "compile",
            "exit": proc.returncode,
            "stdout": _normalize(proc.stdout.decode("utf-8", "replace"), probe, workdir),
            "stderr": _normalize(proc.stderr.decode("utf-8", "replace"), probe, workdir),
        }
    run = subprocess.run([str(binary)], cwd=workdir, capture_output=True, timeout=TIMEOUT)
    return {
        "stage": "run",
        "exit": run.returncode,
        "stdout": _normalize(run.stdout.decode("utf-8", "replace"), probe, workdir),
        "stderr": _normalize(run.stderr.decode("utf-8", "replace"), probe, workdir),
    }


def run_sides(probe: Path, workdir: Path, oracle_args: list[str] | None = None) -> dict[str, Any]:
    """Run one probe under both sides; the shared entry for gen and test."""
    return {
        "cpython": run_oracle(probe, workdir, oracle_args),
        "pyscriptc": run_pyscriptc(probe, workdir),
    }


def _status(row: dict[str, Any]) -> str:
    if row["probe"] is None:
        return "unprobed"
    if row["pyscriptc"]["stage"] == "compile":
        return "fenced"
    channels = ("stdout", "stderr", "exit")
    if all(row["cpython"][k] == row["pyscriptc"][k] for k in channels):
        return "identical"
    return "divergent"


def build() -> dict[str, Any]:
    """Run every probe under both sides and assemble the ledger document."""
    rows: list[dict[str, Any]] = []
    for spec in ROWS:
        row = {k: spec[k] for k in ("id", "seam", "listed", "docs", "probe")}
        if spec["note"]:
            row["note"] = spec["note"]
        if spec["probe"] is None:
            row["cpython"] = None
            row["pyscriptc"] = None
        else:
            probe = PROBES / spec["probe"]
            with tempfile.TemporaryDirectory(prefix="psc-ledger-") as tmp:
                sides = run_sides(probe, Path(tmp), spec["oracle_args"])
            row["probe"] = f"probes/divergences/{spec['probe']}"
            if spec["oracle_args"]:
                row["oracle_args"] = spec["oracle_args"]
            row["cpython"] = sides["cpython"]
            row["pyscriptc"] = sides["pyscriptc"]
        row["status"] = _status(row)
        rows.append(row)
        print(f"{row['id']:>10}  {row['status']:<9}  {row['seam'][:72]}", file=sys.stderr)
    return {
        "version": 1,
        "generated_by": "scripts/gen_divergence_ledger.py",
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="exit non-zero if the generated ledger differs from the committed file"
    )
    parser.add_argument("--out", type=Path, default=LEDGER)
    args = parser.parse_args()
    doc = build()
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        current = args.out.read_text() if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is stale; run scripts/gen_divergence_ledger.py", file=sys.stderr)
            return 1
        return 0
    args.out.write_text(text)
    print(f"wrote {args.out} ({len(doc['rows'])} rows)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
