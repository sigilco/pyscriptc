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


def _popen_case(tmp_path: Path, name: str, body: list[str]) -> Path:
    """A `run()` that opens with `import subprocess` and the given body."""
    lines = ["import subprocess", "", "def run() -> int:", *[f"    {line}" for line in body], "    return 0", ""]
    return write_case(tmp_path, name, "\n".join(lines))


def test_popen_env_replaces_the_child_environment(tmp_path: Path) -> None:
    # `env=` is the whole environment: a variable the parent has but the
    # map lacks is gone, and the map's own entries arrive.
    _oracle(
        tmp_path,
        "popen_env.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    env: dict[str, str] = {'PATH': '/usr/bin:/bin', 'PYSC_ONE': 'alpha', 'PYSC_TWO': 'b c'}",
                "    p: subprocess.Popen = subprocess.Popen(",
                "        ['sh', '-c', 'echo \"$PYSC_ONE|$PYSC_TWO|${HOME:-unset}\"'],",
                "        env=env,",
                "        stdout=subprocess.PIPE,",
                "    )",
                "    print(p.stdout.readline())",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_env_none_inherits_and_empty_env_clears(tmp_path: Path) -> None:
    _oracle(
        tmp_path,
        "popen_env_none.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    a: subprocess.Popen = subprocess.Popen(",
                "        ['sh', '-c', 'test -n \"$PATH\" && echo has-path'], env=None, stdout=subprocess.PIPE",
                "    )",
                "    print(a.stdout.read())",
                "    print(a.wait())",
                "    empty: dict[str, str] = {}",
                "    b: subprocess.Popen = subprocess.Popen(",
                "        ['/bin/sh', '-c', 'echo \"[${HOME:-none}${JOB_ID:-none}]\"'],",
                "        env=empty,",
                "        stdout=subprocess.PIPE,",
                "    )",
                "    print(b.stdout.read())",
                "    print(b.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_env_path_lookup_uses_the_child_path(tmp_path: Path) -> None:
    # A bare program name resolves through the PATH the env carries.
    tool = tmp_path / "bin" / "pysc-tool"
    tool.parent.mkdir()
    tool.write_text("#!/bin/sh\necho from-tool\n")
    tool.chmod(0o755)
    _oracle(
        tmp_path,
        "popen_env_path.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                f"    env: dict[str, str] = {{'PATH': '{tool.parent}'}}",
                "    p: subprocess.Popen = subprocess.Popen(['pysc-tool'], env=env, stdout=subprocess.PIPE)",
                "    print(p.stdout.read())",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_cwd_str_path_and_none(tmp_path: Path) -> None:
    work = tmp_path / "work dir"
    work.mkdir()
    _oracle(
        tmp_path,
        "popen_cwd.py",
        "\n".join(
            [
                "import subprocess",
                "from pathlib import Path",
                "",
                "def run() -> int:",
                f"    here: str = '{work}'",
                "    a: subprocess.Popen = subprocess.Popen(['pwd'], cwd=here, stdout=subprocess.PIPE)",
                "    print(a.stdout.read() == (here + '\\n').encode())",
                "    print(a.wait())",
                "    where: Path = Path(here)",
                "    b: subprocess.Popen = subprocess.Popen(['pwd'], cwd=where, stdout=subprocess.PIPE)",
                "    print(b.stdout.read() == (here + '\\n').encode())",
                "    print(b.wait())",
                "    c: subprocess.Popen = subprocess.Popen(['true'], cwd=None)",
                "    print(c.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_spawn_failures_raise_like_cpython(tmp_path: Path) -> None:
    # A missing program and a missing working directory raise in the
    # parent (FileNotFoundError), a file as the cwd is NotADirectoryError,
    # and a non-executable file is PermissionError; the messages match.
    blocker = tmp_path / "plain-file"
    blocker.write_text("x")
    _oracle(
        tmp_path,
        "popen_spawn_fail.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    try:",
                "        subprocess.Popen(['pysc-no-such-program-xyz'])",
                "    except FileNotFoundError as e:",
                "        print('missing program', e)",
                "    try:",
                "        subprocess.Popen(['true'], cwd='/pysc/no/such/dir')",
                "    except FileNotFoundError as e:",
                "        print('missing cwd', e)",
                "    try:",
                f"        subprocess.Popen(['true'], cwd='{blocker}')",
                "    except NotADirectoryError as e:",
                "        print('file cwd', e)",
                "    try:",
                f"        subprocess.Popen(['{blocker}'])",
                "    except PermissionError as e:",
                "        print('not executable', e)",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_start_new_session_detaches_the_child(tmp_path: Path) -> None:
    # The child is its own session leader: its process-group id is its pid,
    # where an ordinary child shares the parent's session.
    path = write_case(
        tmp_path,
        "popen_session.py",
        "\n".join(
            [
                "import os",
                "import subprocess",
                "",
                "def run() -> int:",
                "    a: subprocess.Popen = subprocess.Popen(",
                "        ['sh', '-c', 'echo $$; ps -o pgid= -p $$'], start_new_session=True, stdout=subprocess.PIPE",
                "    )",
                "    out: bytes = a.stdout.read()",
                "    print(a.wait())",
                "    fields: list[str] = out.decode().split()",
                "    print(fields[0] == fields[1])",
                "    b: subprocess.Popen = subprocess.Popen(",
                "        ['sh', '-c', 'echo $$; ps -o pgid= -p $$'], start_new_session=False, stdout=subprocess.PIPE",
                "    )",
                "    out2: bytes = b.stdout.read()",
                "    print(b.wait())",
                "    fields2: list[str] = out2.decode().split()",
                "    print(fields2[0] == fields2[1])",
                "    return 0",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode == 0, got.stderr
    assert got.stdout == oracle(path, "run")
    # The new-session child is a group leader; the ordinary one is not.
    assert got.stdout == "0\nTrue\n0\nFalse\n0\n", got.stdout


def test_popen_stdin_pipe_with_env_and_cwd(tmp_path: Path) -> None:
    # The consumer's shape: per-job env and cwd, stdin and stdout piped,
    # a new session.
    work = tmp_path / "job"
    work.mkdir()
    _oracle(
        tmp_path,
        "popen_job.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    env: dict[str, str] = {'PATH': '/usr/bin:/bin', 'JOB': 'j1'}",
                "    p: subprocess.Popen = subprocess.Popen(",
                "        ['sh', '-c', 'read line; echo \"$JOB:$line:$(basename \"$(pwd)\")\"'],",
                f"        env=env, cwd='{work}', start_new_session=True,",
                "        stdin=subprocess.PIPE, stdout=subprocess.PIPE,",
                "    )",
                "    p.stdin.write(b'hello\\n')",
                "    p.stdin.flush()",
                "    print(p.stdout.readline())",
                "    p.stdin.close()",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_env_must_be_a_str_dict(tmp_path: Path) -> None:
    path = _popen_case(
        tmp_path,
        "popen_env_bad.py",
        [
            "env: dict[str, int] = {'A': 1}",
            "p: subprocess.Popen = subprocess.Popen(['true'], env=env)",
            "print(p.wait())",
        ],
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    # The checker preflight refuses the int values before the lowering.
    assert "error" in got.stderr and "env" in got.stderr, got.stderr


def test_popen_start_new_session_computed_fences(tmp_path: Path) -> None:
    path = _popen_case(
        tmp_path,
        "popen_session_bad.py",
        [
            "flag: bool = True",
            "p: subprocess.Popen = subprocess.Popen(['true'], start_new_session=flag)",
            "print(p.wait())",
        ],
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS2003" in got.stderr, got.stderr


def test_popen_shell_and_encoding_keywords_still_fence(tmp_path: Path) -> None:
    for keyword in ("shell=True", "encoding='utf-8'"):
        path = _popen_case(
            tmp_path,
            f"popen_{keyword.split('=')[0]}.py",
            [f"p: subprocess.Popen = subprocess.Popen(['true'], {keyword})", "print(p.wait())"],
        )
        got = compile_run(tmp_path, path, "run")
        assert got.returncode != 0
        assert "PS2003" in got.stderr, got.stderr


def test_popen_text_mode_reads_and_writes_str(tmp_path: Path) -> None:
    # text=True: the pipes speak str. The child echoes a line back with a
    # CRLF ending (translated to \n) and a non-ASCII character.
    _oracle(
        tmp_path,
        "popen_text.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p: subprocess.Popen[str] = subprocess.Popen(",
                "        ['sh', '-c', 'read a; printf \"got:%s\\\\r\\\\n\" \"$a\"; echo second; echo tail'],",
                "        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,",
                "    )",
                "    print(p.stdin.write('h\u00e9llo\\n'))",
                "    p.stdin.flush()",
                "    print(repr(p.stdout.readline()))",
                "    for line in p.stdout:",
                "        print(repr(line))",
                "    p.stdin.close()",
                "    print(p.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_text_mode_read_all_and_inference(tmp_path: Path) -> None:
    # The unannotated handle takes its type from `text=True`, and the
    # read-all answers the whole decoded stream.
    _oracle(
        tmp_path,
        "popen_text_all.py",
        "\n".join(
            [
                "import subprocess",
                "",
                "def run() -> int:",
                "    p = subprocess.Popen(['printf', 'a\\\\r\\\\nb\\\\nc'], stdout=subprocess.PIPE, text=True)",
                "    print(repr(p.stdout.read()))",
                "    print(p.wait())",
                "    q = subprocess.Popen(['echo', 'x'], stdout=subprocess.PIPE, universal_newlines=True)",
                "    print(repr(q.stdout.read()))",
                "    print(q.wait())",
                "    return 0",
                "",
            ]
        ),
    )


def test_popen_text_mode_counted_read_fences(tmp_path: Path) -> None:
    path = _popen_case(
        tmp_path,
        "popen_text_count.py",
        [
            "p: subprocess.Popen[str] = subprocess.Popen(['echo', 'x'], stdout=subprocess.PIPE, text=True)",
            "print(p.stdout.read(2))",
            "print(p.wait())",
        ],
    )
    got = compile_run(tmp_path, path, "run")
    assert got.returncode != 0
    assert "PS2003" in got.stderr, got.stderr


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
