"""E2 M2: the builtin exception hierarchy.

The runtime keeps a parent chain for every builtin class, so `except
ArithmeticError` catches a `ZeroDivisionError`, `except LookupError` catches
a `KeyError`, and `except BaseException` catches what `except Exception`
does not. A user class inherits the chain too: `class Sub(AppError)` is
caught by `except AppError` and by `except Exception`, and the runtime
registers the class at module init.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.test_gates import compile_run, oracle, write_case


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_builtin_parent_catches_child(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "parents.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    caught: int = 0",
                "    try:",
                '        raise ZeroDivisionError("z")',
                "    except ArithmeticError:",
                "        caught = caught + 1",
                "    try:",
                '        raise KeyError("k")',
                "    except LookupError:",
                "        caught = caught + 2",
                "    try:",
                '        raise OverflowError("o")',
                "    except ArithmeticError:",
                "        caught = caught + 4",
                "    try:",
                '        raise FileNotFoundError("f")',
                "    except OSError:",
                "        caught = caught + 8",
                "    try:",
                '        raise ConnectionResetError("c")',
                "    except ConnectionError:",
                "        caught = caught + 16",
                "    try:",
                '        raise UnicodeError("u")',
                "    except ValueError:",
                "        caught = caught + 32",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "63\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_exception_does_not_catch_base_exception_only(tmp_path: Path) -> None:
    """`except Exception` must not swallow a `BaseException` descendant."""
    path = write_case(
        tmp_path,
        "split.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    caught: int = 0",
                "    try:",
                '        raise KeyboardInterrupt("k")',
                "    except Exception:",
                "        caught = caught + 100",
                "    except BaseException:",
                "        caught = caught + 1",
                "    try:",
                '        raise SystemExit("s")',
                "    except Exception:",
                "        caught = caught + 100",
                "    except BaseException:",
                "        caught = caught + 2",
                "    try:",
                '        raise ValueError("v")',
                "    except BaseException:",
                "        caught = caught + 4",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "7\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_user_class_inherits_the_chain(tmp_path: Path) -> None:
    path = write_case(
        tmp_path,
        "user.py",
        "\n".join(
            [
                "class AppError(Exception):",
                "    pass",
                "",
                "class SubError(AppError):",
                "    pass",
                "",
                "def measure() -> int:",
                "    caught: int = 0",
                "    try:",
                '        raise SubError("deep")',
                "    except AppError:",
                "        caught = caught + 1",
                "    try:",
                '        raise SubError("deep")',
                "    except Exception:",
                "        caught = caught + 2",
                "    try:",
                '        raise AppError("flat")',
                "    except Exception:",
                "        caught = caught + 4",
                "    try:",
                '        raise AppError("flat")',
                "    except ValueError:",
                "        caught = caught + 100",
                "    except Exception:",
                "        caught = caught + 8",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "15\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_user_class_on_a_builtin_base(tmp_path: Path) -> None:
    """A user class on a builtin base matches that base's parents too."""
    path = write_case(
        tmp_path,
        "onbuiltin.py",
        "\n".join(
            [
                "class AppLookup(LookupError):",
                "    pass",
                "",
                "def measure() -> int:",
                "    caught: int = 0",
                "    try:",
                '        raise AppLookup("l")',
                "    except LookupError:",
                "        caught = caught + 1",
                "    try:",
                '        raise AppLookup("l")',
                "    except Exception:",
                "        caught = caught + 2",
                "    try:",
                '        raise AppLookup("l")',
                "    except KeyError:",
                "        caught = caught + 100",
                "    except Exception:",
                "        caught = caught + 4",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "7\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_oserror_aliases_share_the_class(tmp_path: Path) -> None:
    """`EnvironmentError` and `IOError` are CPython aliases of `OSError`."""
    path = write_case(
        tmp_path,
        "aliases.py",
        "\n".join(
            [
                "def measure() -> int:",
                "    caught: int = 0",
                "    try:",
                '        raise OSError("o")',
                "    except EnvironmentError:",
                "        caught = caught + 1",
                "    try:",
                '        raise OSError("o")',
                "    except IOError:",
                "        caught = caught + 2",
                "    try:",
                '        raise IOError("o")',
                "    except OSError:",
                "        caught = caught + 4",
                "    return caught",
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == "7\n"
    assert got.stdout == oracle(path, "measure")


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_uncaught_user_class_names_itself(tmp_path: Path) -> None:
    """The uncaught report names the user class, not a builtin."""
    path = write_case(
        tmp_path,
        "boom.py",
        "\n".join(
            [
                "class AppError(Exception):",
                "    pass",
                "",
                "def measure() -> int:",
                '    raise AppError("boom")',
                "",
            ]
        ),
    )
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 1, got.stderr
    assert "AppError: boom" in got.stderr


@pytest.mark.linux_container  # HEAVY=1 .agents/docker/run.sh
def test_every_builtin_name_lowers(tmp_path: Path) -> None:
    """Every builtin exception name raises and catches under its own name."""
    names = [
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "BaseException",
        "BufferError",
        "EOFError",
        "Exception",
        "FloatingPointError",
        "ImportError",
        "IndexError",
        "KeyError",
        "LookupError",
        "MemoryError",
        "NameError",
        "NotImplementedError",
        "OSError",
        "OverflowError",
        "RecursionError",
        "ReferenceError",
        "RuntimeError",
        "StopIteration",
        "SyntaxError",
        "SystemError",
        "TypeError",
        "UnboundLocalError",
        "ValueError",
        "Warning",
        "ZeroDivisionError",
    ]
    source = ["def measure() -> int:", "    caught: int = 0"]
    for name in names:
        source.append("    try:")
        source.append(f"        raise {name}()")
        source.append(f"    except {name}:")
        source.append("        caught = caught + 1")
    source.append("    return caught")
    source.append("")
    path = write_case(tmp_path, "names.py", "\n".join(source))
    got = compile_run(tmp_path, path, "measure")
    assert got.returncode == 0, got.stderr
    assert got.stdout == f"{len(names)}\n"
    assert got.stdout == oracle(path, "measure")
