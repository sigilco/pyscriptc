"""`pyscriptc verify`: compile the program, run it twice, diff what can be observed.

The native binary is the ceiling and the CPython island is the floor (typed-subset
positioning, 2026-10-25): there is no byte-for-byte parity goal, so verify does not
try to prove equivalence. It runs the same program under the pinned CPython 3.14
oracle and the linked binary with the same argv and stdin, then reports the first
place stdout, stderr or the exit code differ. The receipt is a differential smoke
test over the paths the inputs exercised, not a proof over every path.

The oracle is pinned to the target's minor (3.14) and probed, never assumed: any
3.14.x patch is accepted silently (the scorer's micro stamp is informational),
while an older oracle (3.11-3.13) is opt-in only (`--oracle-version` or
`PYSC_ORACLE_ANY=1`) and warns that parity is then meaningful for the shared
subset, not for the numbered divergences. A 3.15 interpreter is never accepted.
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

SCHEMA_VERSION: int = 1
TARGET_CPYTHON: str = "3.14.9"
TARGET_MINOR: str = "3.14"
TARGET_MINOR_INFO: tuple[int, int] = (3, 14)
ORACLE_PIN: tuple[int, int] = TARGET_MINOR_INFO
ORACLE_NAME: str = "CPython 3.14"
# The oldest oracle an explicit `--oracle-version` / `PYSC_ORACLE_ANY=1` accepts:
# anything older shares too little with the 3.14 target for the comparison to mean.
ORACLE_FLOOR: tuple[int, int] = (3, 11)
ORACLE_FLOOR_NAME: str = "CPython 3.11"
# The exact fix the PS3001 refusal and `pyscriptc doctor` print (issue #1290).
ORACLE_INSTALL_FIX: str = "uv python install 3.14"
DEFAULT_TIMEOUT: float = 60.0
TIMEOUT_EXIT: int = 124

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
    """The probed CPython interpreter the comparison runs against."""

    executable: str
    version: str
    # The oracle's minor, as "3.14". Empty when the probe could not read one.
    minor: str = ""
    # True when the oracle is older than the target: the comparison then only
    # speaks for the subset both interpreters share (issue #1290).
    subset_only: bool = False


@dataclass(frozen=True)
class Observation:
    """What one side of a verify run produced."""

    stdout: bytes
    stderr: bytes
    exit_code: int
    timed_out: int = 0


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
    cache_hit: int
    compile_ms: int
    duration_ms: int
    timeout_seconds: float

    @property
    def match(self) -> bool:
        for check in self.checks:
            m: bool = check["match"]
            if not m:
                return False
        return True

    def as_dict(self) -> dict[str, Any]:
        micro_mismatch: bool = self.oracle.version != TARGET_CPYTHON
        oracle_dict: dict[str, Any] = {}
        oracle_dict["executable"] = self.oracle.executable
        oracle_dict["version"] = self.oracle.version
        oracle_dict["minor"] = self.oracle.minor
        oracle_dict["subsetOnly"] = self.oracle.subset_only
        oracle_dict["targetVersion"] = TARGET_CPYTHON
        oracle_dict["microMismatch"] = micro_mismatch

        native_dict: dict[str, Any] = {}
        native_dict["binary"] = self.binary
        native_dict["bytes"] = self.binary_bytes

        compile_dict: dict[str, Any] = {}
        compile_dict["cacheHit"] = bool(self.cache_hit)
        compile_dict["milliseconds"] = self.compile_ms

        stdin_dict: dict[str, Any] = {}
        stdin_dict["path"] = self.stdin_path
        stdin_dict["bytes"] = self.stdin_bytes

        res: dict[str, Any] = {}
        res["schemaVersion"] = SCHEMA_VERSION
        res["source"] = self.source
        res["oracle"] = oracle_dict
        res["native"] = native_dict
        res["compile"] = compile_dict
        res["argv"] = list(self.argv)
        res["stdin"] = stdin_dict
        res["timeoutSeconds"] = self.timeout_seconds
        res["checks"] = self.checks
        res["call"] = self.call
        res["match"] = self.match
        res["durationMs"] = self.duration_ms
        res["targetVersion"] = TARGET_CPYTHON
        res["microMismatch"] = micro_mismatch
        return res


def _version_of(executable: str) -> str | None:
    """Probe one interpreter's version, or None when it cannot run."""
    try:
        proc = subprocess.run(
            [executable, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return None
        out: str = proc.stdout.strip()
        if not out:
            return None
        return out
    except Exception:
        return None


def _minor_of(version: str | None) -> tuple[int, int] | None:
    """The (major, minor) of a probed version, or None when it is unreadable."""
    if not version:
        return None
    split_parts = version.split(".")
    if len(split_parts) < 2:
        return None
    try:
        major = int(split_parts[0])
        minor = int(split_parts[1])
    except ValueError:
        return None
    return (major, minor)


def _minor_text(minor: tuple[int, int] | None) -> str:
    """The "3.14" spelling of a (major, minor) pair."""
    if minor is None:
        return ""
    return f"{minor[0]}.{minor[1]}"


def _is_pinned(version: str | None) -> bool:
    """True when the version is the target's minor (any patch)."""
    return _minor_of(version) == ORACLE_PIN


def _is_subset_minor(minor: tuple[int, int] | None) -> bool:
    """True when the minor is an older one the opt-in oracle lane accepts."""
    if minor is None:
        return False
    return ORACLE_FLOOR <= minor < ORACLE_PIN


def _env_allows_any() -> bool:
    """True when `PYSC_ORACLE_ANY=1` opts the run into an older oracle."""
    return os.environ.get("PYSC_ORACLE_ANY", "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_oracle_version(oracle_version: str | None) -> tuple[int, int] | None:
    """The minor an `--oracle-version` names, or None when the flag is absent.

    The spelling is "3.12" or "3.12.4"; the patch is dropped, because the
    lane picks an interpreter of that minor, not of that exact build.
    """
    if oracle_version is None:
        return None
    text = oracle_version.strip()
    if not text:
        return None
    parts = text.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (IndexError, ValueError) as exc:
        raise VersionError(
            f"--oracle-version={oracle_version!r} is not a CPython version; "
            f"write it as {ORACLE_FLOOR_NAME} or {ORACLE_NAME} (for example 3.12)"
        ) from exc
    return (major, minor)


def _oracle(version: str, executable: str, *, subset_only: bool = False) -> Oracle:
    """The probed oracle record, with its minor and the subset-only flag."""
    minor = _minor_of(version)
    return Oracle(executable, version, _minor_text(minor), subset_only)


def _warn_subset(oracle: Oracle) -> None:
    """Warn that an older oracle only speaks for the subset both share."""
    warnings.warn(  # noqa: B028
        f"oracle is CPython {oracle.version}, older than the {TARGET_MINOR} target: parity is meaningful "
        f"only for the subset CPython {oracle.minor} and {TARGET_MINOR} share, and the numbered "
        f"divergences are stated against {TARGET_CPYTHON}",
        UserWarning,
        stacklevel=3,
    )


def _candidate_paths(minor: str) -> list[str]:
    """Every place an interpreter of one minor can live, best first."""
    candidates: list[str] = []
    oracle_dir = os.environ.get("PYSC_ORACLE_DIR")
    if oracle_dir:
        candidates += sorted([str(p) for p in Path(oracle_dir).glob(f"cpython-{minor}.*/bin/python3")])
    found = shutil.which(f"python{minor}")
    if found is not None:
        candidates.append(found)
    try:
        # `uv` prints the interpreter it would use; a missing uv is not fatal.
        found_proc = subprocess.run(["uv", "python", "find", minor], capture_output=True, text=True, check=True)
        candidates.append(found_proc.stdout.strip())
    except Exception:
        pass
    return candidates


def _find_minor_oracle(minor: tuple[int, int]) -> Oracle | None:
    """The first interpreter of one minor that answers the version probe."""
    text = _minor_text(minor)
    for candidate in _candidate_paths(text):
        if not candidate:
            continue
        version = _version_of(candidate)
        if _minor_of(version) == minor:
            return _oracle(version or "", candidate)
    if _minor_of(sys.version.split()[0]) == minor:
        return _oracle(sys.version.split()[0], sys.executable)
    return None


def _find_target_oracle() -> Oracle | None:
    """The target's interpreter: any 3.14 patch, the exact target first."""
    chosen: Oracle | None = None
    for candidate in _candidate_paths(TARGET_MINOR):
        if not candidate:
            continue
        version = _version_of(candidate)
        if not _is_pinned(version):
            continue
        oracle = _oracle(version or "", candidate)
        if version == TARGET_CPYTHON:
            return oracle
        if chosen is None:
            chosen = oracle
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found is None:
            continue
        version = _version_of(found)
        if not _is_pinned(version):
            continue
        oracle = _oracle(version or "", found)
        if version == TARGET_CPYTHON:
            return oracle
        if chosen is None:
            chosen = oracle
    if _is_pinned(sys.version.split()[0]):
        oracle = _oracle(sys.version.split()[0], sys.executable)
        if chosen is None:
            chosen = oracle
        if sys.version.split()[0] == TARGET_CPYTHON:
            return oracle
    return chosen


def _find_subset_oracle() -> Oracle | None:
    """The newest older interpreter the opt-in lane accepts, if any."""
    for minor in (3, 13), (3, 12), (3, 11):
        if not _is_subset_minor(minor):
            continue
        oracle = _find_minor_oracle(minor)
        if oracle is not None:
            return oracle
    return None


def _explicit_oracle(explicit: str, wanted: tuple[int, int] | None, allow_any: bool) -> Oracle:
    """The interpreter `PYSC_ORACLE` names, or a `PS3001` refusal.

    An explicit pin is never silently replaced by another interpreter: a
    wrong pin is a refusal, not a fallback. The only exception is an older
    minor under the opt-in lane, which is accepted with a warning.
    """
    if not (os.path.isfile(explicit) and os.access(explicit, os.X_OK)):
        raise VersionError(f"PYSC_ORACLE={explicit!r} is not an executable path; the oracle must be {ORACLE_NAME}")
    version = _version_of(explicit)
    minor = _minor_of(version)
    if minor == ORACLE_PIN:
        # Any 3.14 patch is the oracle: the micro of the declared target is
        # informational, not a mismatch to warn about (issue #1290).
        return _oracle(version or "", explicit)
    if _is_subset_minor(minor):
        accepted = allow_any or (wanted is not None and wanted == minor)
        if not accepted:
            v_str = version if version is not None else "unknown"
            raise VersionError(
                f"PYSC_ORACLE={explicit!r} is CPython {v_str}; the oracle is pinned to {ORACLE_NAME}. "
                f"Opt in to an older oracle with --oracle-version {v_str.split('.')[0]}.{v_str.split('.')[1]} "
                f"or PYSC_ORACLE_ANY=1, or run `{ORACLE_INSTALL_FIX}`"
            )
        oracle = _oracle(version or "", explicit, subset_only=True)
        _warn_subset(oracle)
        return oracle
    v_str = version if version is not None else "unknown"
    raise VersionError(
        f"PYSC_ORACLE={explicit!r} is CPython {v_str}; the oracle is pinned to {ORACLE_NAME}, "
        f"or to an older minor ({ORACLE_FLOOR_NAME} through 3.13) under the opt-in lane; "
        f"run `{ORACLE_INSTALL_FIX}`"
    )


def find_oracle(*, oracle_version: str | None = None, allow_any: bool | None = None) -> Oracle:
    """The oracle interpreter, or a `PS3001` refusal.

    Resolution mirrors the compat2 scorer's shape: `PYSC_ORACLE`,
    `PYSC_ORACLE_DIR`, `python3.14` on PATH, `uv python find 3.14`, then the
    interpreter running pyscriptc. Any 3.14 patch is accepted silently; the
    micro of the declared target is informational.

    An older oracle (3.11 through 3.13) is opt-in: `--oracle-version 3.12`
    names the minor to use, `PYSC_ORACLE_ANY=1` accepts whichever older
    interpreter is around. Either way the run warns that parity is then
    meaningful only for the subset both interpreters share (issue #1290).

    The frozen compat2 scorer keeps its own resolver and stays on 3.14.
    """
    if allow_any is None:
        allow_any = _env_allows_any()
    wanted = _parse_oracle_version(oracle_version)
    if wanted is not None and wanted > ORACLE_PIN:
        raise VersionError(
            f"--oracle-version={oracle_version!r} is newer than the {TARGET_MINOR} target; "
            f"the oracle is pinned to {ORACLE_NAME}"
        )
    explicit = os.environ.get("PYSC_ORACLE")
    if explicit:
        return _explicit_oracle(explicit, wanted, allow_any)
    if wanted is not None and wanted != ORACLE_PIN:
        oracle = _find_minor_oracle(wanted)
        if oracle is None:
            raise VersionError(
                f"no CPython {_minor_text(wanted)} interpreter for the oracle: set "
                f"PYSC_ORACLE=/path/to/python{_minor_text(wanted)}, set PYSC_ORACLE_DIR, "
                f"or run `uv python install {_minor_text(wanted)}`"
            )
        subset_oracle = _oracle(oracle.version, oracle.executable, subset_only=True)
        _warn_subset(subset_oracle)
        return subset_oracle
    if allow_any:
        oracle = _find_subset_oracle()
        if oracle is None:
            raise VersionError(
                f"no {ORACLE_FLOOR_NAME} to {TARGET_MINOR} interpreter for the oracle: set "
                f"PYSC_ORACLE=/path/to/python3.12, set PYSC_ORACLE_DIR, or run "
                f"`{ORACLE_INSTALL_FIX}`"
            )
        subset_oracle = _oracle(oracle.version, oracle.executable, subset_only=True)
        _warn_subset(subset_oracle)
        return subset_oracle
    oracle = _find_target_oracle()
    if oracle is None:
        raise VersionError(
            f"no {ORACLE_NAME} interpreter for the oracle: set PYSC_ORACLE=/path/to/python3.14, "
            f"set PYSC_ORACLE_DIR, or run `{ORACLE_INSTALL_FIX}`"
        )
    return oracle


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except Exception:
        return str(data)


def _line_at(data: bytes, offset: int) -> str:
    """The line of `data` that holds `offset` (the line ending is not part of it)."""
    start: int = 0
    for i in range(offset - 1, -1, -1):
        if data[i] == 10:
            start = i + 1
            break
    end: int = len(data)
    for i in range(offset, len(data)):
        if data[i] == 10:
            end = i
            break
    return _decode(data[start:end])


def first_difference(expected: bytes, actual: bytes) -> dict[str, Any] | None:
    """The first differing byte, the line that holds it, and both lines' text."""
    if expected == actual:
        return None
    limit: int = min(len(expected), len(actual))
    offset: int = limit
    for index in range(limit):
        if expected[index] != actual[index]:
            offset = index
            break
    line_count: int = 1
    for i in range(offset):
        if expected[i] == 10:
            line_count += 1
    res: dict[str, Any] = {}
    res["offset"] = offset
    res["line"] = line_count
    res["oracle"] = _line_at(expected, offset)
    res["native"] = _line_at(actual, offset)
    res["oracleBytes"] = len(expected)
    res["nativeBytes"] = len(actual)
    return res


def compare(oracle: Observation, native: Observation) -> list[dict[str, Any]]:
    """The three observable checks, in the order the issue fixes them."""
    checks: list[dict[str, Any]] = []
    pairs: list[tuple[str, bytes, bytes]] = [
        ("stdout", oracle.stdout, native.stdout),
        ("stderr", oracle.stderr, native.stderr),
    ]
    for name, expected, actual in pairs:
        c: dict[str, Any] = {}
        c["name"] = name
        c["match"] = expected == actual
        c["oracleBytes"] = len(expected)
        c["nativeBytes"] = len(actual)
        c["difference"] = first_difference(expected, actual)
        checks.append(c)
    timed_out: bool = bool(oracle.timed_out) or bool(native.timed_out)
    exit_c: dict[str, Any] = {}
    exit_c["name"] = "exit"
    exit_c["match"] = oracle.exit_code == native.exit_code and not timed_out
    exit_c["oracle"] = oracle.exit_code
    exit_c["native"] = native.exit_code
    exit_c["timedOut"] = timed_out
    exit_c["oracleTimedOut"] = bool(oracle.timed_out)
    exit_c["nativeTimedOut"] = bool(native.timed_out)
    checks.append(exit_c)
    return checks


def _as_bytes(value: bytes | None) -> bytes:
    if value is None:
        return b""
    return value


def run_process(command: list[str], *, stdin: bytes, cwd: Path, timeout: float) -> Observation:
    """Run one side with the same stdin, cwd and wall-clock ceiling."""
    try:
        proc = subprocess.run(command, input=stdin, cwd=cwd, capture_output=True, timeout=timeout)
        code: int = 128 + (-proc.returncode) if proc.returncode < 0 else proc.returncode
        return Observation(proc.stdout, proc.stderr, code)
    except subprocess.TimeoutExpired as exc:
        return Observation(_as_bytes(exc.stdout), _as_bytes(exc.stderr), TIMEOUT_EXIT, timed_out=1)


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
        return [oracle.executable, str(source)] + argv
    try:
        payload: str = json.dumps(call_args)
    except TypeError as exc:
        raise IRError(f"--args must be JSON-serializable for the CPython side: {exc}") from exc
    args_path: Path = scratch / "verify-call-args.json"
    args_path.write_text(payload)
    return [oracle.executable, "-c", CALL_HARNESS, str(source), call, str(args_path)] + argv


def render_text(receipt: Receipt) -> str:
    """The short human receipt: one line per check, the diff when one differs."""
    differing: int = 0
    for check in receipt.checks:
        m: bool = check["match"]
        if not m:
            differing += 1
    verdict: str = "match" if receipt.match else f"divergence ({differing} of {len(receipt.checks)} checks differ)"
    lines: list[str] = [f"verify {receipt.source}: {verdict}"]
    if receipt.oracle.minor and receipt.oracle.minor != TARGET_MINOR:
        lines.append(
            f"  oracle: {receipt.oracle.executable} (CPython {receipt.oracle.version}, target {TARGET_CPYTHON})"
        )
        lines.append(
            f"  note: parity is meaningful only for the subset CPython {receipt.oracle.minor} and "
            f"{TARGET_MINOR} share; the numbered divergences are stated against {TARGET_CPYTHON}"
        )
    else:
        lines.append(f"  oracle: {receipt.oracle.executable} (CPython {receipt.oracle.version})")
    size: str = ""
    bb: int | None = receipt.binary_bytes
    if bb is not None:
        size = f", {bb} bytes"
    cache: str = " (cached)" if receipt.cache_hit else ""
    lines.append(f"  native: {receipt.binary}{size}{cache}")
    call_entry = receipt.call
    if call_entry is not None:
        call_dict: dict[str, Any] = call_entry
        call_fn: str = call_dict["function"]
        args_obj: list[Any] = call_dict["args"]
        pieces: list[str] = []
        index: int = 0
        while index < len(args_obj):
            value: Any = args_obj[index]
            pieces.append(repr(value))
            index += 1
        rendered: str = ", ".join(pieces)
        lines.append(f"  call: {call_fn}({rendered}), return compared in stdout")
    for check in receipt.checks:
        lines.extend(_render_check(check))
    if not receipt.match:
        lines.append(
            "hint: this run covers the paths the inputs exercised; fix or file the divergence, "
            "then add the case to the corpus if it is a compiler bug."
        )
    return "\n".join(lines)


def _render_check(check: dict[str, Any]) -> list[str]:
    name: str = check["name"]
    is_match: bool = check["match"]
    if name == "exit":
        timed_out: bool = check["timedOut"]
        if timed_out:
            ora_timed: bool = False
            nat_timed: bool = False
            if "oracleTimedOut" in check:
                ora_timed = check["oracleTimedOut"]
            if "nativeTimedOut" in check:
                nat_timed = check["nativeTimedOut"]
            if ora_timed and nat_timed:
                return ["  exit: both sides timed out"]
            if ora_timed:
                return ["  exit: the oracle timed out"]
            return ["  exit: the native run timed out"]
        ora_code: int = check["oracle"]
        nat_code: int = check["native"]
        if is_match:
            return [f"  exit: {ora_code}, match"]
        return [f"  exit: oracle {ora_code}, native {nat_code}"]
    ora_bytes: int = check["oracleBytes"]
    nat_bytes: int = check["nativeBytes"]
    if is_match:
        return [f"  {name}: {ora_bytes} bytes, match"]
    diff_dict: dict[str, Any] | None = check["difference"]
    if diff_dict is not None:
        line_num: int = diff_dict["line"]
        offset_num: int = diff_dict["offset"]
        lines: list[str] = [
            f"  {name}: differs at line {line_num}, byte {offset_num} ({ora_bytes} vs {nat_bytes} bytes)"
        ]
        ora_text: str = diff_dict["oracle"]
        nat_text: str = diff_dict["native"]
        if ora_text or nat_text:
            lines.append(f"    oracle: {ora_text}")
            lines.append(f"    native: {nat_text}")
        else:
            lines.append("    (the difference is past the end of one stream)")
        return lines
    return []


def failure_receipt(
    source: str, oracle: Oracle, exit_code: int, output: str, *, timeout_seconds: float
) -> dict[str, Any]:
    """The receipt for a program the compiler refused: fences or a build error."""
    kind: str = "fences" if exit_code == 3 else "build"
    micro_mismatch: bool = oracle.version != TARGET_CPYTHON
    oracle_dict: dict[str, Any] = {}
    oracle_dict["executable"] = oracle.executable
    oracle_dict["version"] = oracle.version
    oracle_dict["minor"] = oracle.minor
    oracle_dict["subsetOnly"] = oracle.subset_only
    oracle_dict["targetVersion"] = TARGET_CPYTHON
    oracle_dict["microMismatch"] = micro_mismatch

    error_dict: dict[str, Any] = {}
    error_dict["kind"] = kind
    error_dict["exitCode"] = exit_code
    error_dict["output"] = output

    res: dict[str, Any] = {}
    res["schemaVersion"] = SCHEMA_VERSION
    res["source"] = source
    res["oracle"] = oracle_dict
    res["match"] = False
    res["error"] = error_dict
    res["timeoutSeconds"] = timeout_seconds
    res["targetVersion"] = TARGET_CPYTHON
    res["microMismatch"] = micro_mismatch
    return res


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
    resolved: Path = source.resolve()
    command: list[str] = oracle_command(oracle, resolved, argv, call, call_args, scratch)
    expected: Observation = run_process(command, stdin=stdin, cwd=resolved.parent, timeout=timeout)
    native_bin: str = str(binary.resolve())
    native_cmd: list[str] = [native_bin] + argv
    native: Observation = run_process(native_cmd, stdin=stdin, cwd=resolved.parent, timeout=timeout)
    checks: list[dict[str, Any]] = compare(expected, native)
    call_entry: dict[str, Any] | None = None
    if call is not None:
        first_check: dict[str, Any] = checks[0]
        first_match: bool = first_check["match"]
        entry: dict[str, Any] = {}
        entry["function"] = call
        entry["args"] = call_args
        entry["match"] = first_match
        entry["comparedIn"] = "stdout"
        call_entry = entry
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
        cache_hit=1 if cache_hit else 0,
        compile_ms=compile_ms,
        duration_ms=int((time.monotonic() - started) * 1000),
        timeout_seconds=timeout,
    )


def render_failure(receipt: dict[str, Any]) -> str:
    error: dict[str, Any] = receipt["error"]
    err_kind: str = error["kind"]
    err_code: int = error["exitCode"]
    kind: str = "the compiler fenced this program" if err_kind == "fences" else "the build failed"
    source_str: str = receipt["source"]
    lines: list[str] = [f"verify {source_str}: {kind} (exit {err_code})"]
    output_str: str = ""
    if "output" in error:
        output_str = error["output"]
    output: str = output_str.rstrip()
    if output:
        for line in output.splitlines():
            lines.append(f"  {line}")
    lines.append("hint: verify runs a program the compiler accepts; `pyscriptc coverage FILE` lists every fence.")
    return "\n".join(lines)
