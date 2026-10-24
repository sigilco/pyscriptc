"""`pyscriptc verify`: compile the program, run it twice, diff what can be observed.

The native binary is the ceiling and the CPython island is the floor (typed-subset
positioning, 2026-10-25): there is no byte-for-byte parity goal, so verify does not
try to prove equivalence. It runs the same program under the pinned CPython 3.14
oracle and the linked binary with the same argv and stdin, then reports the first
place stdout, stderr or the exit code differ. The receipt is a differential smoke
test over the paths the inputs exercised, not a proof over every path.

The oracle is pinned and probed, never assumed: comparing a binary to CPython 3.13
or 3.15 would report divergences that belong to the wrong interpreter.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyscriptc.errors import IRError, VersionError
from pyscriptc.target import TARGET_CPYTHON, TARGET_MINOR, TARGET_MINOR_INFO

SCHEMA_VERSION = 1
ORACLE_PIN = TARGET_MINOR_INFO
ORACLE_NAME = f"CPython {TARGET_MINOR}"
DEFAULT_TIMEOUT = 60.0
TIMEOUT_EXIT = 124

# The `--call` harness for the oracle: load the file as `__main__` (so sibling
# imports and `__file__` behave), then print the return value with the same
# spelling the emitter's generated main uses (int as `%lld`, float as `%.17g`,
# str as its bytes plus a newline, nothing for None). `--call` runs only the
# global initializers, not the module's top-level statements, so the import's
# own output is swallowed: the comparison is about the call.
CALL_HARNESS = """
import contextlib
import importlib.util
import io
import json
import sys

source, function, args_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.argv = [source, *sys.argv[4:]]
with open(args_path, encoding="utf-8") as handle:
    arguments = json.load(handle)
spec = importlib.util.spec_from_file_location("__main__", source)
module = importlib.util.module_from_spec(spec)
sys.modules["__main__"] = module
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(module)
result = getattr(module, function)(*arguments)
if isinstance(result, float):
    print(format(result, ".17g"))
elif result is not None:
    print(result)
"""


@dataclass(frozen=True)
class Oracle:
    """The probed CPython 3.14 interpreter."""

    executable: str
    version: str


@dataclass(frozen=True)
class Observation:
    """What one side of a verify run produced."""

    stdout: bytes
    stderr: bytes
    exit_code: int
    timed_out: bool = False


@dataclass
class Receipt:
    """The verify receipt: one file, one oracle, one binary, the observed checks."""

    source: str
    oracle: Oracle
    binary: str
    binary_bytes: int | None
    argv: list[str]
    stdin_path: str | None
    stdin_bytes: int
    checks: list[dict[str, Any]]
    call: dict[str, Any] | None
    cache_hit: bool
    compile_ms: int
    duration_ms: int
    timeout_seconds: float

    @property
    def match(self) -> bool:
        return all(check["match"] for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        micro_mismatch = self.oracle.version != TARGET_CPYTHON
        return {
            "schemaVersion": SCHEMA_VERSION,
            "source": self.source,
            "oracle": {
                "executable": self.oracle.executable,
                "version": self.oracle.version,
                "targetVersion": TARGET_CPYTHON,
                "microMismatch": micro_mismatch,
            },
            "native": {"binary": self.binary, "bytes": self.binary_bytes},
            "compile": {"cacheHit": self.cache_hit, "milliseconds": self.compile_ms},
            "argv": list(self.argv),
            "stdin": {"path": self.stdin_path, "bytes": self.stdin_bytes},
            "timeoutSeconds": self.timeout_seconds,
            "checks": self.checks,
            "call": self.call,
            "match": self.match,
            "durationMs": self.duration_ms,
            "targetVersion": TARGET_CPYTHON,
            "microMismatch": micro_mismatch,
        }


def _version_of(executable: str) -> str | None:
    """Probe one interpreter's version, or None when it cannot run."""
    try:
        proc = subprocess.run(
            [executable, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def find_oracle() -> Oracle:
    """The pinned CPython 3.14 executable, or a `PS3001` refusal.

    Resolution mirrors the compat2 scorer: `PYSC_ORACLE`, `PYSC_ORACLE_DIR`,
    `python3.14` on PATH, `uv python find 3.14`, then the interpreter running
    pyscriptc. An explicit `PYSC_ORACLE` is never silently replaced by another
    interpreter: a wrong pin is a refusal, not a fallback.
    """
    explicit = os.environ.get("PYSC_ORACLE")
    if explicit:
        if not (os.path.isfile(explicit) and os.access(explicit, os.X_OK)):
            raise VersionError(f"PYSC_ORACLE={explicit!r} is not an executable path; the oracle must be {ORACLE_NAME}")
        version = _version_of(explicit)
        if not _is_pinned(version):
            raise VersionError(
                f"PYSC_ORACLE={explicit!r} is CPython {version or 'unknown'}; the oracle is pinned to {ORACLE_NAME}"
            )
        if version != TARGET_CPYTHON:
            warnings.warn(
                f"oracle is CPython {version}, declared target is {TARGET_CPYTHON}",
                UserWarning,
                stacklevel=2,
            )
        return Oracle(explicit, version or "")
    candidates: list[str] = []
    oracle_dir = os.environ.get("PYSC_ORACLE_DIR")
    if oracle_dir:
        candidates += [str(p) for p in sorted(Path(oracle_dir).glob("cpython-3.14.*/bin/python3"))]
    for name in ("python3.14", "python3", "python"):
        found = shutil.which(name)
        if found is not None:
            candidates.append(found)
    try:
        # `uv` prints the interpreter it would use; a missing uv is not fatal.
        found = subprocess.run(["uv", "python", "find", "3.14"], capture_output=True, text=True, timeout=30, check=True)
        candidates.append(found.stdout.strip())
    except OSError, subprocess.SubprocessError:
        pass
    candidates.append(sys.executable)
    chosen: Oracle | None = None
    for candidate in dict.fromkeys(candidate for candidate in candidates if candidate):
        version = _version_of(candidate)
        if _is_pinned(version):
            oracle = Oracle(candidate, version or "")
            if version == TARGET_CPYTHON:
                return oracle
            if chosen is None:
                chosen = oracle
    if chosen is not None:
        warnings.warn(
            f"oracle is CPython {chosen.version}, declared target is {TARGET_CPYTHON}",
            UserWarning,
            stacklevel=2,
        )
        return chosen
    raise VersionError(
        f"no {ORACLE_NAME} interpreter for the oracle: set PYSC_ORACLE=/path/to/python3.14, "
        f"set PYSC_ORACLE_DIR, or run `uv python install 3.14`"
    )


def _is_pinned(version: str | None) -> bool:
    if not version:
        return False
    try:
        parts = tuple(int(part) for part in version.split(".")[:2])
    except ValueError:
        return False
    return parts == ORACLE_PIN


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "backslashreplace")


def _line_at(data: bytes, offset: int) -> str:
    """The line of `data` that holds `offset` (the line ending is not part of it)."""
    start = data.rfind(b"\n", 0, offset) + 1
    end = data.find(b"\n", offset)
    if end < 0:
        end = len(data)
    return _decode(data[start:end])


def first_difference(expected: bytes, actual: bytes) -> dict[str, Any] | None:
    """The first differing byte, the line that holds it, and both lines' text."""
    if expected == actual:
        return None
    limit = min(len(expected), len(actual))
    offset = next((index for index in range(limit) if expected[index] != actual[index]), limit)
    return {
        "offset": offset,
        "line": expected.count(b"\n", 0, offset) + 1,
        "oracle": _line_at(expected, offset),
        "native": _line_at(actual, offset),
        "oracleBytes": len(expected),
        "nativeBytes": len(actual),
    }


def compare(oracle: Observation, native: Observation) -> list[dict[str, Any]]:
    """The three observable checks, in the order the issue fixes them."""
    checks: list[dict[str, Any]] = []
    for name, expected, actual in (
        ("stdout", oracle.stdout, native.stdout),
        ("stderr", oracle.stderr, native.stderr),
    ):
        checks.append(
            {
                "name": name,
                "match": expected == actual,
                "oracleBytes": len(expected),
                "nativeBytes": len(actual),
                "difference": first_difference(expected, actual),
            }
        )
    timed_out = oracle.timed_out or native.timed_out
    checks.append(
        {
            "name": "exit",
            "match": oracle.exit_code == native.exit_code and not timed_out,
            "oracle": oracle.exit_code,
            "native": native.exit_code,
            "timedOut": timed_out,
            "oracleTimedOut": oracle.timed_out,
            "nativeTimedOut": native.timed_out,
        }
    )
    return checks


def _as_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    return value.encode() if isinstance(value, str) else value


def run_process(command: list[str], *, stdin: bytes, cwd: Path, timeout: float) -> Observation:
    """Run one side with the same stdin, cwd and wall-clock ceiling."""
    try:
        proc = subprocess.run(command, input=stdin, cwd=cwd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return Observation(_as_bytes(exc.stdout), _as_bytes(exc.stderr), TIMEOUT_EXIT, timed_out=True)
    code = 128 + (-proc.returncode) if proc.returncode < 0 else proc.returncode
    return Observation(proc.stdout, proc.stderr, code)


def oracle_command(
    oracle: Oracle, source: Path, argv: list[str], call: str | None, call_args: list[Any], scratch: Path
) -> list[str]:
    """The CPython command for the same program, argv and optional call.

    `--call` needs a small harness so the module body runs as `__main__` and the
    return value is printed the way the emitter's generated main prints it. The
    arguments travel through a file in `scratch`, so quoting or huge literals
    cannot change what reaches the function.
    """
    if call is None:
        return [oracle.executable, str(source), *argv]
    try:
        payload = json.dumps(call_args)
    except TypeError as exc:
        raise IRError(f"--args must be JSON-serializable for the CPython side: {exc}") from exc
    args_path = scratch / "verify-call-args.json"
    args_path.write_text(payload, encoding="utf-8")
    return [oracle.executable, "-c", CALL_HARNESS, str(source), call, str(args_path), *argv]


def render_text(receipt: Receipt) -> str:
    """The short human receipt: one line per check, the diff when one differs."""
    differing = sum(1 for check in receipt.checks if not check["match"])
    verdict = "match" if receipt.match else f"divergence ({differing} of {len(receipt.checks)} checks differ)"
    lines = [f"verify {receipt.source}: {verdict}"]
    if receipt.oracle.version != TARGET_CPYTHON:
        lines.append(
            f"  oracle: {receipt.oracle.executable} (CPython {receipt.oracle.version}, target {TARGET_CPYTHON})"
        )
    else:
        lines.append(f"  oracle: {receipt.oracle.executable} (CPython {receipt.oracle.version})")
    size = f", {receipt.binary_bytes} bytes" if receipt.binary_bytes is not None else ""
    cache = " (cached)" if receipt.cache_hit else ""
    lines.append(f"  native: {receipt.binary}{size}{cache}")
    if receipt.call is not None:
        rendered = ", ".join(repr(value) for value in receipt.call["args"])
        lines.append(f"  call: {receipt.call['function']}({rendered}), return compared in stdout")
    for check in receipt.checks:
        lines.extend(_render_check(check))
    if not receipt.match:
        lines.append(
            "hint: this run covers the paths the inputs exercised; fix or file the divergence, "
            "then add the case to the corpus if it is a compiler bug."
        )
    return "\n".join(lines)


def _render_check(check: dict[str, Any]) -> list[str]:
    name = check["name"]
    if name == "exit":
        if check["timedOut"]:
            if check.get("oracleTimedOut") and check.get("nativeTimedOut"):
                return ["  exit: both sides timed out"]
            return [
                "  exit: the oracle timed out" if check.get("oracleTimedOut") else "  exit: the native run timed out"
            ]
        if check["match"]:
            return [f"  exit: {check['oracle']}, match"]
        return [f"  exit: oracle {check['oracle']}, native {check['native']}"]
    if check["match"]:
        return [f"  {name}: {check['oracleBytes']} bytes, match"]
    difference = check["difference"] or {}
    line = difference.get("line", 1)
    offset = difference.get("offset", 0)
    lines = [
        f"  {name}: differs at line {line}, byte {offset} ({check['oracleBytes']} vs {check['nativeBytes']} bytes)"
    ]
    if difference.get("oracle") or difference.get("native"):
        lines.append(f"    oracle: {difference.get('oracle', '')}")
        lines.append(f"    native: {difference.get('native', '')}")
    else:
        lines.append("    (the difference is past the end of one stream)")
    return lines


def failure_receipt(
    source: str, oracle: Oracle, exit_code: int, output: str, *, timeout_seconds: float
) -> dict[str, Any]:
    """The receipt for a program the compiler refused: fences or a build error."""
    kind = "fences" if exit_code == 3 else "build"
    micro_mismatch = oracle.version != TARGET_CPYTHON
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": source,
        "oracle": {
            "executable": oracle.executable,
            "version": oracle.version,
            "targetVersion": TARGET_CPYTHON,
            "microMismatch": micro_mismatch,
        },
        "match": False,
        "error": {"kind": kind, "exitCode": exit_code, "output": output},
        "timeoutSeconds": timeout_seconds,
        "targetVersion": TARGET_CPYTHON,
        "microMismatch": micro_mismatch,
    }


def _binary_size(binary: Path) -> int | None:
    try:
        return binary.stat().st_size
    except OSError:
        return None


def measure(
    source: Path,
    oracle: Oracle,
    binary: Path,
    *,
    argv: list[str],
    stdin: bytes,
    stdin_path: str | None,
    call: str | None,
    call_args: list[Any],
    timeout: float,
    scratch: Path,
    cache_hit: bool,
    compile_ms: int,
    started: float,
) -> Receipt:
    """Run both sides with the same inputs and build the receipt.

    The compile itself stays in the CLI (it shares the `run` cache); this
    function owns everything after the binary exists, so the receipt shape and
    the diff stay together in one module.
    """
    resolved = source.resolve()
    command = oracle_command(oracle, resolved, argv, call, call_args, scratch)
    expected = run_process(command, stdin=stdin, cwd=resolved.parent, timeout=timeout)
    native = run_process([str(binary.absolute()), *argv], stdin=stdin, cwd=resolved.parent, timeout=timeout)
    checks = compare(expected, native)
    call_entry = None
    if call is not None:
        call_entry = {"function": call, "args": call_args, "match": checks[0]["match"], "comparedIn": "stdout"}
    return Receipt(
        source=str(source),
        oracle=oracle,
        binary=str(binary),
        binary_bytes=_binary_size(binary),
        argv=list(argv),
        stdin_path=stdin_path,
        stdin_bytes=len(stdin),
        checks=checks,
        call=call_entry,
        cache_hit=cache_hit,
        compile_ms=compile_ms,
        duration_ms=int((time.monotonic() - started) * 1000),
        timeout_seconds=timeout,
    )


def render_failure(receipt: dict[str, Any]) -> str:
    error = receipt["error"]
    kind = "the compiler fenced this program" if error["kind"] == "fences" else "the build failed"
    lines = [f"verify {receipt['source']}: {kind} (exit {error['exitCode']})"]
    output = str(error["output"]).rstrip()
    if output:
        lines.extend(f"  {line}" for line in output.splitlines())
    lines.append("hint: verify runs a program the compiler accepts; `pyscriptc coverage FILE` lists every fence.")
    return "\n".join(lines)
