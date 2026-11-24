"""subprocess: the native run, call and check_call subset.

`subprocess.run(cmd, capture_output=True)` returns the CompletedProcess
record with `returncode` and the captured stdout bytes, `subprocess.call`
returns the exit code, and `subprocess.check_call` raises RuntimeError on a
non-zero code. The command is a `list[str]`, the shape scripts use. Each
probe diffs stdout against the CPython oracle.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_gates import compile_run, oracle, write_case

from pyscriptc.lower import lower_source


def _oracle(tmp_path: Path, name: str, source: str, func: str = "run") -> None:
    path = write_case(tmp_path, name, source)
    got = compile_run(tmp_path, path, func)
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, func)


def test_subprocess_run_captures_stdout(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "run.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    r: subprocess.CompletedProcess = subprocess.run(['echo', 'hello'], capture_output=True)",
                "    print(r.returncode)",
                "    print(r.stdout)",
                "    return 0",
                "",
            ]
        ),
    )


def test_subprocess_run_without_capture(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "nocapture.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    r: subprocess.CompletedProcess = subprocess.run(['true'])",
                "    print(r.returncode)",
                "    return 0",
                "",
            ]
        ),
    )


def test_subprocess_call_return_codes(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "call.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    print(subprocess.call(['true']))",
                "    print(subprocess.call(['false']))",
                "    print(subprocess.check_call(['true']))",
                "    return 0",
                "",
            ]
        ),
    )


def test_subprocess_check_call_raises(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "checkfail.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    subprocess.check_call(['false'])",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "non-zero exit code" in got.stderr, got.stderr


def test_subprocess_run_check_true_accepts_zero_and_raises_nonzero(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "checkok.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    r: subprocess.CompletedProcess = subprocess.run(",
                "        ['true'], capture_output=True, text=True, check=True",
                "    )",
                "    print(r.returncode)",
                "    return 0",
                "",
            ]
        ),
    )
    path = write_case(
        tmp_path,
        "checkfail.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    subprocess.run(['false'], check=True)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0, got.stdout
    assert "subprocess returned a non-zero exit code" in got.stderr, got.stderr
    # check=False is the CPython default: the record wins either way.
    _oracle(
        tmp_path,
        "checkoff.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    r: subprocess.CompletedProcess = subprocess.run(['false'], capture_output=True, check=False)",
                "    print(r.returncode)",
                "    return 0",
                "",
            ]
        ),
    )

    # cwd= keyword with str or Path runs child in that working directory
    _oracle(
        tmp_path,
        "cwd_run.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    r: subprocess.CompletedProcess = subprocess.run(['pwd'], cwd='/tmp', capture_output=True)",
                "    print(len(r.stdout) > 0)",
                "    return 0",
                "",
            ]
        ),
    )


def test_subprocess_run_text_mode_matches_cpython(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "text.py",
        "\n".join(
            [
                "import subprocess",
                "import sys",
                "",
                "def run() -> int:",
                "    r = subprocess.run(",
                "        [sys.executable, '-c', 'print(1+1)'], capture_output=True, text=True",
                "    )",
                "    print(r.stdout.strip())",
                "    print(r.stderr == '')",
                "    return 0",
                "",
            ]
        ),
    )


def test_subprocess_run_text_mode_nonzero_exit(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "textfail.py",
        "\n".join(
            [
                "import subprocess",
                "import sys",
                "",
                "def run() -> int:",
                "    r = subprocess.run(",
                "        [sys.executable, '-c', 'import sys; sys.exit(3)'], capture_output=True, text=True",
                "    )",
                "    print(r.returncode)",
                "    return 0",
                "",
            ]
        ),
    )


def test_subprocess_run_text_mode_encoding_latin1(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "enc.py",
        "\n".join(
            [
                "import subprocess",
                "import sys",
                "",
                "def run() -> int:",
                "    r = subprocess.run(",
                "        [sys.executable, '-c', \"print(bytes([200]).decode('latin-1'))\"],",
                "        capture_output=True, text=True, encoding='latin-1', errors='replace'",
                "    )",
                "    print(r.stdout.strip())",
                "    return 0",
                "",
            ]
        ),
    )


def test_subprocess_run_text_mode_keyword_values_fence_with_named_codes() -> None:
    mod = lower_source("import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], encoding=5)\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "encoding takes a string literal" in mod["fences"][0]["message"]
    mod = lower_source(
        "import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], encoding='cp1252')\n"
    )
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "encoding 'cp1252' is not lowered" in mod["fences"][0]["message"]
    mod = lower_source(
        "import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], errors='xmlcharrefreplace')\n"
    )
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "errors 'xmlcharrefreplace' is not lowered" in mod["fences"][0]["message"]


def test_subprocess_run_keyword_values_fence_with_named_codes() -> None:
    mod = lower_source("import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], shell=True)\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "subprocess.run keyword shell is not lowered" in mod["fences"][0]["message"]
    mod = lower_source("import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], text='yes')\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "text takes a True or False literal" in mod["fences"][0]["message"]
    mod = lower_source("import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], cwd=123)\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "subprocess.run cwd must be a str or Path" in mod["fences"][0]["message"]


def test_subprocess_run_timeout_lower_and_fence_with_named_codes() -> None:
    # The literal number loweres; the fence cases name what is wrong.
    mod = lower_source("import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], timeout=5)\n")
    assert mod["fences"] == []
    mod = lower_source("import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], timeout=1.5)\n")
    assert mod["fences"] == []
    mod = lower_source("import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], timeout=None)\n")
    assert mod["fences"] == []
    mod = lower_source("import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], timeout=0)\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "timeout takes a positive number of seconds" in mod["fences"][0]["message"]
    mod = lower_source("import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], timeout=-1)\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "timeout takes a positive number of seconds" in mod["fences"][0]["message"]
    mod = lower_source("import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], timeout=t)\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "timeout takes a number literal (seconds)" in mod["fences"][0]["message"]
    mod = lower_source("import subprocess\nr: subprocess.CompletedProcess = subprocess.run(['true'], timeout='5')\n")
    assert [f["code"] for f in mod["fences"]] == ["PS2003"]
    assert "timeout takes a number literal (seconds)" in mod["fences"][0]["message"]


def test_subprocess_run_timeout_inside_the_bound_keeps_the_record(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "timeout_ok.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    r: subprocess.CompletedProcess = subprocess.run(['true'], timeout=30, capture_output=True)",
                "    print(r.returncode)",
                "    return 0",
                "",
            ]
        ),
    )


def test_subprocess_run_timeout_raises_the_timeout_path(tmp_path: Path) -> None:
    # The sleeping child outlives the bound: the run raises
    # subprocess.TimeoutExpired, the way CPython's does (issue #1223).
    path = write_case(
        tmp_path,
        "timeout_expired.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    try:",
                "        subprocess.run(['sleep', '5'], timeout=1)",
                "        print('no timeout')",
                "    except subprocess.TimeoutExpired as exc:",
                "        print('timed out', str(exc))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    # The same program under the CPython oracle prints the same bytes (the
    # oracle appends the function's return value).
    assert got.stdout == oracle(path, "run")
    assert got.stdout.startswith("timed out Command '['sleep', '5']' timed out after 1 seconds\n"), got.stdout


def test_subprocess_run_timeout_is_not_the_builtin_timeout_error(tmp_path: Path) -> None:
    # CPython's TimeoutExpired derives from SubprocessError, not from the
    # builtin TimeoutError: `except TimeoutError` does not catch it.
    path = write_case(
        tmp_path,
        "timeout_wrong_class.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    try:",
                "        subprocess.run(['sleep', '5'], timeout=1)",
                "        print('no timeout')",
                "    except TimeoutError:",
                "        print('caught the builtin TimeoutError')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0, got.stdout
    assert "TimeoutExpired" in got.stderr, got.stderr


# --- subprocess.Popen: the child-handle surface (issue #1345) ---------------


def test_popen_spawn_and_exit_code(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "popen_run.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(['true'])",
                "    print(p.pid > 0)",
                "    print(p.wait())",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_exit_code_matches_oracle(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "popen_rc.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(['sh', '-c', 'exit 3'])",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_poll_and_returncode_are_none_while_running(tmp_path: Path) -> None:
    # CPython's poll() and returncode answer None while the child runs.
    _oracle(
        tmp_path,
        "popen_poll.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(['sleep', '5'])",
                "    print(p.poll() is None)",
                "    print(p.returncode is None)",
                "    p.kill()",
                "    print(p.wait())",
                "    print(str(p.returncode))",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_kill_and_terminate(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "popen_kill.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    a: subprocess.Popen = subprocess.Popen(['sleep', '5'])",
                "    a.kill()",
                "    print(a.wait(timeout=5))",
                "    b: subprocess.Popen = subprocess.Popen(['sleep', '5'])",
                "    b.terminate()",
                "    print(b.wait(timeout=5))",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_send_signal(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "popen_signal.py",
        "\n".join(
            [
                "import signal",
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(['sleep', '5'])",
                "    p.send_signal(signal.SIGKILL)",
                "    print(p.wait(timeout=5))",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_wait_timeout_expires(tmp_path: Path) -> None:
    # The bound passes: TimeoutExpired, the child stays alive, the caller
    # kills it, and the wait reads the killed code.
    _oracle(
        tmp_path,
        "popen_timeout.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(['sleep', '5'])",
                "    try:",
                "        p.wait(timeout=0.3)",
                "        print('no timeout')",
                "    except subprocess.TimeoutExpired as exc:",
                "        print('expired', str(exc))",
                "    p.kill()",
                "    print(p.wait(timeout=5))",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_wait_timeout_is_not_the_builtin_timeout_error(tmp_path: Path) -> None:
    # CPython's TimeoutExpired derives from SubprocessError, not from the
    # builtin TimeoutError: `except TimeoutError` does not catch it.
    path = write_case(
        tmp_path,
        "popen_timeout_wrong_class.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(['sleep', '5'])",
                "    try:",
                "        p.wait(timeout=0.3)",
                "    except TimeoutError:",
                "        print('caught the builtin TimeoutError')",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0, got.stdout
    assert "TimeoutExpired" in got.stderr, got.stderr


def test_popen_pipe_roundtrip(tmp_path: Path) -> None:
    # The stdin write / stdout read pair: the interactive protocol shape.
    _oracle(
        tmp_path,
        "popen_pipe.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(",
                "        ['cat'], stdin=subprocess.PIPE, stdout=subprocess.PIPE",
                "    )",
                "    p.stdin.write(b'ping\\n')",
                "    p.stdin.flush()",
                "    print(p.stdout.readline())",
                "    p.stdin.close()",
                "    print(p.stdout.read())",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_stdout_iteration(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "popen_lines.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(",
                "        ['sh', '-c', 'echo one; echo two'], stdout=subprocess.PIPE",
                "    )",
                "    for line in p.stdout:",
                "        print('line', line)",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_stderr_pipe(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "popen_err.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(",
                "        ['sh', '-c', 'echo o; echo e >&2'],",
                "        stdout=subprocess.PIPE, stderr=subprocess.PIPE,",
                "    )",
                "    print(p.stdout.readline())",
                "    print(p.stderr.readline())",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_read_n_keeps_the_surplus(tmp_path: Path) -> None:
    # read(n) parks the surplus in the buffer: the next read carries on.
    _oracle(
        tmp_path,
        "popen_readn.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(",
                "        ['printf', 'abcdefgh'], stdout=subprocess.PIPE",
                "    )",
                "    print(p.stdout.read(3))",
                "    print(p.stdout.read(3))",
                "    print(p.stdout.read())",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_unpiped_stream_read_fences(tmp_path: Path) -> None:
    # The lowered handle always answers a handle; the read on a stream
    # that was not piped raises ValueError at run time. CPython answers
    # None for the stream and the read on the None raises AttributeError,
    # so the reference run is expected to fail with that AttributeError
    # (the divergence the probe records).
    path = write_case(
        tmp_path,
        "popen_unpiped.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(['true'])",
                "    try:",
                "        p.stdout.read()",
                "    except ValueError:",
                "        print('no pipe')",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "no pipe\n0\n0\n", got.stdout
    runner = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('mod', r'{path}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "mod.run()\n"
    )
    ref = subprocess.run([sys.executable, "-c", runner], capture_output=True, text=True, encoding="latin-1")
    assert ref.returncode != 0
    assert "AttributeError: 'NoneType' object has no attribute 'read'" in ref.stderr


def test_popen_environment_keywords_fence(tmp_path: Path) -> None:
    # The spawn environment is issue #1344's surface: it fences with
    # PS2003 naming the issue.
    path = write_case(
        tmp_path,
        "popen_env.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(['true'], env={})",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS2003" in got.stderr, got.stderr
    assert "#1344" in got.stderr, got.stderr


def test_popen_computed_wait_timeout_fences(tmp_path: Path) -> None:
    # The bound is a literal: a computed bound has no lowering.
    path = write_case(
        tmp_path,
        "popen_timeout_computed.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    bound: float = 1.0",
                "    p: subprocess.Popen = subprocess.Popen(['sleep', '5'])",
                "    print(p.wait(timeout=bound))",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS2003" in got.stderr, got.stderr


def test_popen_computed_read_n_fences(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "popen_readn_computed.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    n: int = 3",
                "    p: subprocess.Popen = subprocess.Popen(",
                "        ['printf', 'abc'], stdout=subprocess.PIPE",
                "    )",
                "    print(p.stdout.read(n))",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS2003" in got.stderr, got.stderr


def test_popen_computed_signal_fences(tmp_path: Path) -> None:
    # The signal is a signal.SIG* constant; a bare int fences.
    path = write_case(
        tmp_path,
        "popen_signal_computed.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    sig: int = 9",
                "    p: subprocess.Popen = subprocess.Popen(['sleep', '5'])",
                "    p.send_signal(sig)",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS2003" in got.stderr, got.stderr


def test_popen_communicate_fences(tmp_path: Path) -> None:
    # communicate() drains both pipes to EOF: outside the incremental
    # surface, refused by name.
    path = write_case(
        tmp_path,
        "popen_communicate.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen = subprocess.Popen(",
                "        ['true'], stdout=subprocess.PIPE",
                "    )",
                "    out: bytes",
                "    err: bytes",
                "    out, err = p.communicate()",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS2001" in got.stderr, got.stderr
