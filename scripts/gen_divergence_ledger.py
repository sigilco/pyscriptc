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
        "json has no non-ASCII text: non-ASCII emits raw where CPython escapes",
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
        "a user exception stores a code and an optional str without calling __init__",
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
        "socket, ssl, http.client, http.server and selectors do not compile (PS2001)",
        "d29_socket.py",
        docs=f"{DOC}#29",
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
        "AUDIT-D11", "json.dumps does not escape non-ASCII (audit D11)", "ad11_json_ascii.py", listed=False, docs=AUDIT
    ),
    _row(
        "AUDIT-D12",
        "a str literal above Latin-1 was emitted as raw bytes (audit D12)",
        "ad12_str_utf8.py",
        listed=False,
        docs=AUDIT,
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
