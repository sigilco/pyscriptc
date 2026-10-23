"""The pyscriptc-jwt pack: jwt.encode / jwt.decode for the HS* family.

The signing reference is real PyJWT 2.15.1 (`benchmarks/compat/top100/src/jwt`,
pinned); the parity test generates the expected token with the vendored
package under CPython. The closed subset is the header of
`src/pyscriptc/runtime/ps_jwt.c`: HS256, HS384 and HS512, a dict[str, str]
payload, and errors raising ValueError.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.test_gates import run_cli, write_case

ROOT = Path(__file__).resolve().parents[1]
TOP100 = ROOT / "benchmarks" / "compat" / "top100" / "src"

PROGRAM = """import jwt


def enc() -> str:
    token: str = jwt.encode({"sub": "u1"}, "secret")
    return token


def enc_alg() -> str:
    token: str = jwt.encode({"sub": "u1"}, "secretkey", algorithm="HS384")
    return token


def enc512() -> str:
    token: str = jwt.encode({"sub": "u1"}, "secretkey", algorithm="HS512")
    return token


def enc_pos() -> str:
    token: str = jwt.encode({"sub": "u1"}, "secretkey", "HS256")
    return token


def enc_multi() -> str:
    # Sorted-claim order: `role` sorts before `sub`, so the pack's token is
    # byte-identical to PyJWT's here too.
    token: str = jwt.encode({"role": "admin", "sub": "u1"}, "secretkey")
    return token


def dec(token: str) -> str:
    claims: dict[str, str] = jwt.decode(token, "secret", ["HS256"])
    return claims["sub"]


def dec_two(token: str) -> int:
    claims: dict[str, str] = jwt.decode(token, "secret", ["HS256", "HS384"])
    return len(claims["sub"]) * 10 + len(claims["role"])
"""


def oracle(*lines: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", "\n".join(lines)],
        env={"PYTHONPATH": str(TOP100), "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


CASES = [
    ("enc", {"sub": "u1"}, "secret", "HS256"),
    ("enc_alg", {"sub": "u1"}, "secretkey", "HS384"),
    ("enc512", {"sub": "u1"}, "secretkey", "HS512"),
    ("enc_pos", {"sub": "u1"}, "secretkey", "HS256"),
    ("enc_multi", {"role": "admin", "sub": "u1"}, "secretkey", "HS256"),
]


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
@pytest.mark.parametrize("func,payload,key,alg", CASES)
def test_jwt_encode_matches_pyjwt(tmp_path: Path, func: str, payload: dict, key: str, alg: str) -> None:
    path = write_case(tmp_path, "jwt_case.py", PROGRAM)
    got = run_cli("compile", str(path), "--run", "--call", func, "-o", str(tmp_path / func))
    assert got.returncode == 0, got.stderr
    items = ", ".join(repr(k) + ": " + repr(v) for k, v in sorted(payload.items()))
    want = oracle("import jwt", f"print(jwt.encode({{{items}}}, {key!r}, algorithm={alg!r}))")
    assert got.stdout == want


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_jwt_decode_round_trips(tmp_path: Path) -> None:
    path = write_case(tmp_path, "jwt_dec.py", PROGRAM)
    token = oracle("import jwt", "print(jwt.encode({'sub': 'u1'}, 'secret'))").strip()
    got = run_cli("compile", str(path), "--run", "--call", "dec", "--args", repr([token]), "-o", str(tmp_path / "dec"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "u1\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_jwt_decode_checks_the_allow_list(tmp_path: Path) -> None:
    path = write_case(tmp_path, "jwt_two.py", PROGRAM)
    hs384 = oracle("import jwt", "print(jwt.encode({'sub': 'ab', 'role': 'cd'}, 'secretkey', algorithm='HS384'))")
    hs384 = hs384.strip()
    got = run_cli(
        "compile", str(path), "--run", "--call", "dec_two", "--args", repr([hs384]), "-o", str(tmp_path / "two")
    )
    assert got.returncode == 1
    assert "ValueError" in got.stderr
    source = PROGRAM.replace(
        'jwt.decode(token, "secret", ["HS256", "HS384"])', 'jwt.decode(token, "secretkey", ["HS384"])'
    )
    path = write_case(tmp_path, "jwt_384.py", source)
    got = run_cli(
        "compile", str(path), "--run", "--call", "dec_two", "--args", repr([hs384]), "-o", str(tmp_path / "384")
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout == "22\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_jwt_bad_signature_raises(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "bad.py",
        (
            "import jwt\n\n\n"
            "def f(token: str) -> str:\n"
            "    claims: dict[str, str] = {}\n"
            "    try:\n"
            "        claims = jwt.decode(token, 'other', ['HS256'])\n"
            "    except ValueError:\n"
            "        return 'refused'\n"
            "    return claims['sub']\n"
        ),
    )
    token = oracle("import jwt", "print(jwt.encode({'sub': 'u1'}, 'secret'))").strip()
    got = run_cli("compile", str(path), "--run", "--call", "f", "--args", repr([token]), "-o", str(tmp_path / "bad"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "refused\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_jwt_malformed_token_raises(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "mal.py",
        (
            "import jwt\n\n\n"
            "def f(token: str) -> str:\n"
            "    claims: dict[str, str] = {}\n"
            "    try:\n"
            "        claims = jwt.decode(token, 'secret', ['HS256'])\n"
            "    except ValueError:\n"
            "        return 'refused'\n"
            "    return claims['sub']\n"
        ),
    )
    got = run_cli("compile", str(path), "--run", "--call", "f", "--args", "['abc.def']", "-o", str(tmp_path / "mal"))
    assert got.returncode == 0, got.stderr
    assert got.stdout == "refused\n"


@pytest.mark.linux_container  # HEAVY=1 docker/run.sh
def test_jwt_pyjwt_accepts_our_token(tmp_path: Path) -> None:
    """The token the pack produces verifies under real PyJWT."""
    path = write_case(tmp_path, "jwt_x.py", PROGRAM)
    got = run_cli("compile", str(path), "--run", "--call", "enc_multi", "-o", str(tmp_path / "multi"))
    assert got.returncode == 0, got.stderr
    token = got.stdout.strip()
    want = oracle(
        "import jwt",
        "t = " + repr(token),
        "c = jwt.decode(t, 'secretkey', algorithms=['HS256'])",
        "print(c['sub'] + ':' + c['role'])",
    )
    assert want == "u1:admin\n"


BAD_ALGS = ["RS256", "HS128", "none"]


@pytest.mark.parametrize("alg", BAD_ALGS)
def test_jwt_encode_refuses_outside_the_family(tmp_path: Path, alg: str) -> None:
    path = write_case(
        tmp_path,
        "alg.py",
        "import jwt\n\n\n"
        f"def f() -> str:\n    token: str = jwt.encode({{'sub': 'u1'}}, 'secret', algorithm={alg!r})\n"
        "    return token\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2001" in got.stderr + got.stdout


def test_jwt_other_members_stay_fenced(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "other.py",
        "import jwt\n\n\ndef f(token: str) -> dict[str, str]:\n    return jwt.get_unverified_header(token)\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS2001" in got.stderr + got.stdout
    assert "jwt.get_unverified_header" in got.stderr + got.stdout


def test_jwt_payload_is_dict_str(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "int.py",
        "import jwt\n\n\ndef f() -> str:\n    token: str = jwt.encode({'exp': 2}, 'secret')\n    return token\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS1003" in got.stderr + got.stdout


def test_jwt_decode_needs_the_annotation(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "bare.py",
        "import jwt\n\n\ndef f(token: str) -> int:\n    return len(jwt.decode(token, 'secret', ['HS256']))\n",
    )
    got = run_cli("compile", str(path), "--emit", "ir", "--skip-check")
    assert got.returncode != 0
    assert "PS1001" in got.stderr + got.stdout


def test_jwt_lowers_onto_the_pack_symbols(tmp_path: Path) -> None:
    path = write_case(tmp_path, "ok.py", PROGRAM)
    got = run_cli("compile", str(path), "--emit", "llvm", "--skip-check", "-o", str(tmp_path / "ok.ll"))
    assert got.returncode == 0, got.stderr
    ll = (tmp_path / "ok.ll").read_text()
    assert "@ps_jwt_encode" in ll
    assert "@ps_jwt_decode" in ll
