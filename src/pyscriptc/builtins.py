"""Closed builtin and method tables.

A name is lowered only when it is listed here. `len` of `str` and `len` of a
list are different operations; both are listed. Anything else stays a fence.
"""

from __future__ import annotations

from typing import Any

# The builtin exception hierarchy. Codes match ps_rt.c and the PS_* constants
# in ps_rt.h; a parent always has a smaller code than its children, so the
# parent chain is a walk towards zero. The root, BaseException, maps to the
# empty string. EnvironmentError and IOError are CPython aliases of OSError
# and share its code; WindowsError does not exist on this platform.
EXCEPTION_PARENTS: dict[str, str] = {
    "BaseException": "",
    "BaseExceptionGroup": "BaseException",
    "ExceptionGroup": "BaseExceptionGroup",
    "GeneratorExit": "BaseException",
    "KeyboardInterrupt": "BaseException",
    "SystemExit": "BaseException",
    "Exception": "BaseException",
    "ArithmeticError": "Exception",
    "FloatingPointError": "ArithmeticError",
    "OverflowError": "ArithmeticError",
    "ZeroDivisionError": "ArithmeticError",
    "AssertionError": "Exception",
    "AttributeError": "Exception",
    "BufferError": "Exception",
    "EOFError": "Exception",
    "ImportError": "Exception",
    "ModuleNotFoundError": "ImportError",
    "LookupError": "Exception",
    "IndexError": "LookupError",
    "KeyError": "LookupError",
    "MemoryError": "Exception",
    "NameError": "Exception",
    "UnboundLocalError": "NameError",
    "OSError": "Exception",
    "BlockingIOError": "OSError",
    "ChildProcessError": "OSError",
    "ConnectionError": "OSError",
    "BrokenPipeError": "ConnectionError",
    "ConnectionAbortedError": "ConnectionError",
    "ConnectionRefusedError": "ConnectionError",
    "ConnectionResetError": "ConnectionError",
    "FileExistsError": "OSError",
    "FileNotFoundError": "OSError",
    "InterruptedError": "OSError",
    "IsADirectoryError": "OSError",
    "NotADirectoryError": "OSError",
    "PermissionError": "OSError",
    "ProcessLookupError": "OSError",
    "TimeoutError": "OSError",
    "ReferenceError": "Exception",
    "RuntimeError": "Exception",
    "NotImplementedError": "RuntimeError",
    "RecursionError": "RuntimeError",
    "StopAsyncIteration": "Exception",
    "StopIteration": "Exception",
    "SyntaxError": "Exception",
    "IndentationError": "SyntaxError",
    "TabError": "IndentationError",
    "SystemError": "Exception",
    "TypeError": "Exception",
    "ValueError": "Exception",
    "UnicodeError": "ValueError",
    "UnicodeDecodeError": "UnicodeError",
    "UnicodeEncodeError": "UnicodeError",
    "UnicodeTranslateError": "UnicodeError",
    "Warning": "Exception",
    "BytesWarning": "Warning",
    "DeprecationWarning": "Warning",
    "EncodingWarning": "Warning",
    "FutureWarning": "Warning",
    "ImportWarning": "Warning",
    "PendingDeprecationWarning": "Warning",
    "ResourceWarning": "Warning",
    "RuntimeWarning": "Warning",
    "SyntaxWarning": "Warning",
    "UnicodeWarning": "Warning",
    "UserWarning": "Warning",
    "JSONDecodeError": "ValueError",
}

# Names that share another class's code. JSONDecodeError is a ValueError
# child, not an alias: CPython raises it on malformed JSON input.
EXCEPTION_ALIASES: dict[str, str] = {
    "EnvironmentError": "OSError",
    "IOError": "OSError",
}

# Code per builtin exception name, in hierarchy order.
EXCEPTIONS: dict[str, int] = {
    "BaseException": 1,
    "BaseExceptionGroup": 2,
    "ExceptionGroup": 3,
    "GeneratorExit": 4,
    "KeyboardInterrupt": 5,
    "SystemExit": 6,
    "Exception": 7,
    "ArithmeticError": 8,
    "FloatingPointError": 9,
    "OverflowError": 10,
    "ZeroDivisionError": 11,
    "AssertionError": 12,
    "AttributeError": 13,
    "BufferError": 14,
    "EOFError": 15,
    "ImportError": 16,
    "ModuleNotFoundError": 17,
    "LookupError": 18,
    "IndexError": 19,
    "KeyError": 20,
    "MemoryError": 21,
    "NameError": 22,
    "UnboundLocalError": 23,
    "OSError": 24,
    "BlockingIOError": 25,
    "ChildProcessError": 26,
    "ConnectionError": 27,
    "BrokenPipeError": 28,
    "ConnectionAbortedError": 29,
    "ConnectionRefusedError": 30,
    "ConnectionResetError": 31,
    "FileExistsError": 32,
    "FileNotFoundError": 33,
    "InterruptedError": 34,
    "IsADirectoryError": 35,
    "NotADirectoryError": 36,
    "PermissionError": 37,
    "ProcessLookupError": 38,
    "TimeoutError": 39,
    "ReferenceError": 40,
    "RuntimeError": 41,
    "NotImplementedError": 42,
    "RecursionError": 43,
    "StopAsyncIteration": 44,
    "StopIteration": 45,
    "SyntaxError": 46,
    "IndentationError": 47,
    "TabError": 48,
    "SystemError": 49,
    "TypeError": 50,
    "ValueError": 51,
    "UnicodeError": 52,
    "UnicodeDecodeError": 53,
    "UnicodeEncodeError": 54,
    "UnicodeTranslateError": 55,
    "Warning": 56,
    "BytesWarning": 57,
    "DeprecationWarning": 58,
    "EncodingWarning": 59,
    "FutureWarning": 60,
    "ImportWarning": 61,
    "PendingDeprecationWarning": 62,
    "ResourceWarning": 63,
    "RuntimeWarning": 64,
    "SyntaxWarning": 65,
    "UnicodeWarning": 66,
    "UserWarning": 67,
    "EnvironmentError": 24,
    "IOError": 24,
    "JSONDecodeError": 68,
}

# User exception classes take codes from here up; matches PS_USER_BASE in
# ps_rt.h. The runtime registers each class at module init, so every code at
# or above it matches `except Exception` through the parent chain.
USER_EXC_BASE = 256

# Reserved for the coroutine exceptions (issue #162) and the queue
# exceptions (issue #172). Mirrors PS_RESERVED_BASE and friends in ps_rt.h:
# the top of the i32 code space, so USER_EXC_BASE and user class codes do not
# shift. Deliberately not in EXCEPTIONS: nothing lowers or raises them yet,
# and adding names there would change handler lowering.
RESERVED_EXC_BASE = 0x7FFFFFF0
RESERVED_EXCEPTIONS: dict[str, int] = {
    "GeneratorExit": 0x7FFFFFF0,
    "StopAsyncIteration": 0x7FFFFFF1,
    "CancelledError": 0x7FFFFFF2,
    "TimeoutError": 0x7FFFFFF3,
    "Empty": 0x7FFFFFF4,
    "Full": 0x7FFFFFF5,
}

# params are type kinds. return is a type kind, or "self" for the receiver's type.
STR_METHODS: dict[str, dict[str, Any]] = {
    "strip": {"params": [], "return": "self"},
    "lstrip": {"params": [], "return": "self"},
    "rstrip": {"params": [], "return": "self"},
    "upper": {"params": [], "return": "self"},
    "lower": {"params": [], "return": "self"},
    "startswith": {"params": ["self"], "return": "bool"},
    "endswith": {"params": ["self"], "return": "bool"},
    "find": {"params": ["self"], "return": "int"},
    "replace": {"params": ["self", "self"], "return": "self"},
    "count": {"params": ["self"], "return": "int"},
    "index": {"params": ["self"], "return": "int"},
    "rfind": {"params": ["self"], "return": "int"},
    "isdigit": {"params": [], "return": "bool"},
    "isalpha": {"params": [], "return": "bool"},
    "isalnum": {"params": [], "return": "bool"},
    "isspace": {"params": [], "return": "bool"},
    "isupper": {"params": [], "return": "bool"},
    "islower": {"params": [], "return": "bool"},
    "isascii": {"params": [], "return": "bool", "kinds": {"str"}},
    "title": {"params": [], "return": "self"},
    "capitalize": {"params": [], "return": "self"},
    # casefold is str-only in CPython; the "kinds" gate keeps bytes.casefold fenced.
    "casefold": {"params": [], "return": "self", "kinds": {"str"}},
    "swapcase": {"params": [], "return": "self"},
    "zfill": {"params": ["int"], "return": "self"},
    "removeprefix": {"params": ["self"], "return": "self"},
    "removesuffix": {"params": ["self"], "return": "self"},
    "splitlines": {"params": [], "return": "self_list"},
}

LEN_KINDS = {"str", "bytes", "list", "dict"}


def _len_ok(typ: dict[str, Any]) -> bool:
    if typ["kind"] in LEN_KINDS:
        return True
    return typ["kind"] == "class" and typ.get("name") == "Deque"


def builtin_spec(name: str, arg_types: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the result type of a builtin call, or None when it is not lowered."""

    if name == "len" and len(arg_types) == 1 and _len_ok(arg_types[0]):
        return {"kind": "int"}
    if name == "int" and len(arg_types) == 1 and arg_types[0]["kind"] in {"float", "bytes", "str"}:
        return {"kind": "int"}
    if name == "float" and len(arg_types) == 1 and arg_types[0]["kind"] in {"int", "str"}:
        return {"kind": "float"}
    if (
        name == "str"
        and len(arg_types) == 1
        and arg_types[0]["kind"]
        in {
            "str",
            "int",
            "float",
            "bool",
            "bytes",
            "none",
            "exc",
        }
    ):
        return {"kind": "str"}
    if name == "str" and len(arg_types) == 1 and arg_types[0]["kind"] == "class" and arg_types[0].get("name") == "Path":
        return {"kind": "str"}
    if (
        name == "id"
        and len(arg_types) == 1
        and arg_types[0]["kind"] in {"list", "dict", "class", "exc", "str", "bytes"}
    ):
        return {"kind": "int"}
    return None


def method_spec(receiver: dict[str, Any], name: str, arg_types: list[dict[str, Any]]) -> dict[str, Any] | None:
    if receiver["kind"] not in {"str", "bytes"}:
        return None
    if name == "encode":
        # One optional str encoding argument; the value is checked in _call_attr.
        if receiver["kind"] != "str" or len(arg_types) > 1:
            return None
        if arg_types and arg_types[0]["kind"] != "str":
            return None
        return {"kind": "bytes"}
    if name == "decode":
        if receiver["kind"] != "bytes" or len(arg_types) > 1:
            return None
        if arg_types and arg_types[0]["kind"] != "str":
            return None
        return {"kind": "str"}
    if name in {"split", "rsplit"}:
        # split([sep [, maxsplit]]); sep None is not a lowered type, so the
        # whitespace form stays the 0-arg call.
        if len(arg_types) > 2:
            return None
        if len(arg_types) >= 1 and arg_types[0]["kind"] != receiver["kind"]:
            return None
        if len(arg_types) == 2 and arg_types[1]["kind"] != "int":
            return None
        return {"kind": "list", "elem": {"kind": receiver["kind"]}}
    if name in {"ljust", "rjust", "center"}:
        if not 1 <= len(arg_types) <= 2:
            return None
        if arg_types[0]["kind"] != "int":
            return None
        if len(arg_types) == 2 and arg_types[1]["kind"] != receiver["kind"]:
            return None
        return {"kind": receiver["kind"]}
    if name == "expandtabs":
        if len(arg_types) > 1 or (arg_types and arg_types[0]["kind"] != "int"):
            return None
        return {"kind": receiver["kind"]}
    if name == "join":
        if len(arg_types) != 1 or arg_types[0]["kind"] != "list":
            return None
        if arg_types[0]["elem"]["kind"] != receiver["kind"]:
            return None
        return {"kind": receiver["kind"]}
    if name not in STR_METHODS:
        return None
    spec = STR_METHODS[name]
    if receiver["kind"] not in spec.get("kinds", {"str", "bytes"}):
        return None
    if len(arg_types) != len(spec["params"]):
        return None
    for got, want in zip(arg_types, spec["params"], strict=True):
        if want == "self":
            if got["kind"] != receiver["kind"]:
                return None
        elif got["kind"] != want:
            return None
    if spec["return"] == "self":
        return {"kind": receiver["kind"]}
    if spec["return"] == "self_list":
        return {"kind": "list", "elem": {"kind": receiver["kind"]}}
    return {"kind": spec["return"]}
