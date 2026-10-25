---
title: What will fail
description: Known gaps and compiler fences measured on real-world scripts.
---

pyscriptc compiles a typed subset of Python 3.14. When a script uses an unlowered standard library call, an unsupported language construct, or an unannotated variable, compilation halts at a named fence (`PSxxxx`).

This page lists every gap measured on the R0 real-world scripts corpus (39 third-party scripts, measured on 2026-10-24). It documents what fails and why, grouped by root cause. Features that are supported are not listed here: the support state lives on the compat site, which measures it. Each fence code links to its `pyscriptc explain` description.

## Standard library surface

Calls to standard library modules in the closed import table whose specific function or method is not yet lowered to native IR.

| Module | Call or construct | Code | Diagnostic |
| --- | --- | --- | --- |
| `dataclasses` | `dataclasses.asdict` | [`pyscriptc explain PS2001`](cli.md#explain) | `dataclasses.asdict is not in the lowered subset of dataclasses` |
| `itertools` | `itertools.product` | [`pyscriptc explain PS2001`](cli.md#explain) | `itertools.product is not in the lowered subset of itertools` |
| `json` | `json.load` | [`pyscriptc explain PS2001`](cli.md#explain) | `json.load is not in the lowered subset of json` |
| `os` | `os.system` | [`pyscriptc explain PS2001`](cli.md#explain) | `os.system is not in the lowered subset of os` |
| `os` | `os.walk` | [`pyscriptc explain PS2001`](cli.md#explain) | `os.walk is not in the lowered subset of os` |
| `pathlib` | `p.is_dir` | [`pyscriptc explain PS2001`](cli.md#explain) | `p.is_dir is not in the lowered subset of pathlib` |
| `pathlib` | `p.is_file` | [`pyscriptc explain PS2001`](cli.md#explain) | `p.is_file is not in the lowered subset of pathlib` |
| `random` | `random.sample` | [`pyscriptc explain PS2001`](cli.md#explain) | `random.sample is not in the lowered subset of random` |
| `subprocess` | `subprocess.Popen` | [`pyscriptc explain PS2001`](cli.md#explain) | `subprocess.Popen is not in the lowered subset of subprocess` |
| `time` | `time.perf_counter` | [`pyscriptc explain PS2001`](cli.md#explain) | `time.perf_counter is not in the lowered subset of time` |
| `time` | `time.process_time` | [`pyscriptc explain PS2001`](cli.md#explain) | `time.process_time is not in the lowered subset of time` |
| `zipfile` | `zipfile.ZipFile` | [`pyscriptc explain PS2001`](cli.md#explain) | `zipfile.ZipFile is not in the lowered subset of zipfile` |

## Closed import table

Standard library and external modules that are not yet in the closed import table. Importing them halts compilation with a fence.

| Module | Call or construct | Code | Diagnostic |
| --- | --- | --- | --- |
| `pylong` | import pylong | [`pyscriptc explain PS2002`](cli.md#explain) | `_pylong is not in the closed import table` |
| `pyperf` | import pyperf | [`pyscriptc explain PS2002`](cli.md#explain) | `pyperf is not in the closed import table` |
| `sqlite3` | import sqlite3 | [`pyscriptc explain PS2002`](cli.md#explain) | `sqlite3 is not in the closed import table` |

## Call signatures and keyword arguments

Calls that pass keyword arguments, unsupported argument counts, or non-scalar arguments halt at the call table.

| Module | Call or construct | Code | Diagnostic |
| --- | --- | --- | --- |
| `argparse` | `argparse` option/default | [`pyscriptc explain PS2003`](cli.md#explain) | `argparse.add_argument keyword nargs is not lowered` |
| `argparse` | call arity | [`pyscriptc explain PS2003`](cli.md#explain) | `ArgParser.add_mutually_exclusive_group is not lowered` |
| `builtins` | `abs()` on non-number | [`pyscriptc explain PS2003`](cli.md#explain) | `abs is lowered for int and float` |
| `builtins` | `input()` with prompt | [`pyscriptc explain PS2003`](cli.md#explain) | `input expects 0 argument(s)` |
| `builtins` | `open(newline=...)` | [`pyscriptc explain PS2003`](cli.md#explain) | `open keyword newline is not lowered; open lowers encoding= only` |
| `builtins` | `print(list)` | [`pyscriptc explain PS2003`](cli.md#explain) | `print is lowered for str, int, float, bool, and bytes values, not list; wrap it in str(...)` |
| `builtins` | `zip()` with >2 args | [`pyscriptc explain PS2003`](cli.md#explain) | `zip takes 2 lists` |
| `builtins` | call to unlowered function | [`pyscriptc explain PS2002`](cli.md#explain) | `call to batch_rename is not lowered` |
| `builtins` | keyword arguments | [`pyscriptc explain PS2003`](cli.md#explain) | `keyword arguments are not lowered` |
| `hashlib` | `hashlib.sha256()` | [`pyscriptc explain PS2003`](cli.md#explain) | `hashlib.sha256 expects 1 argument(s)` |
| `sys` | `sys.exit()` | [`pyscriptc explain PS2003`](cli.md#explain) | `sys.exit expects 1 argument(s)` |

## Type annotations and inference

pyscriptc requires type annotations on function parameters, return types, and local variable declarations that cannot be inferred from literals or lowered calls.

| Module | Call or construct | Code | Diagnostic |
| --- | --- | --- | --- |
| `builtins` | forward class reference | [`pyscriptc explain PS1105`](cli.md#explain) | `the forward reference 'Config' is not lowered` |
| `builtins` | function without return annotation | [`pyscriptc explain PS1001`](cli.md#explain) | `read needs a return annotation` |
| `builtins` | local variable without annotation | [`pyscriptc explain PS1001`](cli.md#explain) | `state needs an annotation before use` |
| `builtins` | name read after unannotated assignment | [`pyscriptc explain PS2002`](cli.md#explain) | `had_error is not a local or parameter` |
| `builtins` | parameter without annotation | [`pyscriptc explain PS1001`](cli.md#explain) | `parameter work_dir needs an annotation` |
| `collections` | `collections.defaultdict` | [`pyscriptc explain PS1103`](cli.md#explain) | `DefaultDict is not a value type in the IR` |
| `random` | unlowered type annotation | [`pyscriptc explain PS1106`](cli.md#explain) | `the annotation random.Random names a type that is not lowered` |
| `typing` | `typing.Any` | [`pyscriptc explain PS1104`](cli.md#explain) | `Any is not a value type in the IR` |
| `typing` | unlowered type annotation | [`pyscriptc explain PS1001`](cli.md#explain) | `a lambda needs a Callable[[args], ret] annotation` |

## Statements and language constructs

Statement forms, unpack patterns, and language constructs that have not yet been lowered.

| Module | Call or construct | Code | Diagnostic |
| --- | --- | --- | --- |
| `builtins` | `data.sort()` | [`pyscriptc explain PS2001`](cli.md#explain) | `call of attribute is not lowered (data.sort)` |
| `builtins` | `files.remove()` | [`pyscriptc explain PS2001`](cli.md#explain) | `call of attribute is not lowered (files.remove)` |
| `builtins` | `global` statement | [`pyscriptc explain PS2002`](cli.md#explain) | `global pw is not a module slot` |
| `builtins` | `zip()` of nested list | [`pyscriptc explain PS2002`](cli.md#explain) | `zip() of list[list[str]] is not lowered` |
| `builtins` | dataclass bool field | [`pyscriptc explain PS2002`](cli.md#explain) | `bool fields are not lowered` |
| `builtins` | infinite iterators are not lowered | [`pyscriptc explain PS2001`](cli.md#explain) | `infinite iterators are not lowered` |
| `builtins` | nested tuple unpack in loop | [`pyscriptc explain PS2002`](cli.md#explain) | `for unpacks flat names` |
| `builtins` | non-constant module assignment | [`pyscriptc explain PS2002`](cli.md#explain) | `a module assignment must be a constant` |
| `datetime` | `DateTime.strftime` | [`pyscriptc explain PS2001`](cli.md#explain) | `DateTime.strftime is not a method` |
| `pathlib` | `Path.endswith` | [`pyscriptc explain PS2001`](cli.md#explain) | `Path.endswith is not a method` |

## Classes and object model

Class attributes, method declarations, and class decorators outside the lowered object model.

| Module | Call or construct | Code | Diagnostic |
| --- | --- | --- | --- |
| `builtins` | class attribute access | [`pyscriptc explain PS2001`](cli.md#explain) | `Namespace.files is not a field` |

## Operators

Operators applied to unlowered operand type combinations halt with an operator fence.

| Module | Call or construct | Code | Diagnostic |
| --- | --- | --- | --- |
| `builtins` | operator on unlowered types | [`pyscriptc explain PS2004`](cli.md#explain) | `Add on None and opt_str is not lowered` |

## Type checking

The type-checker preflight rejects type mismatches between values and declared annotations before lowering begins.

| Module | Call or construct | Code | Diagnostic |
| --- | --- | --- | --- |
| `builtins` | type annotation mismatch | [`pyscriptc explain PS1003`](cli.md#explain) | `open path is str` |
| `os` | type annotation mismatch | [`pyscriptc explain PS1003`](cli.md#explain) | `os.listdir argument is Path, expected str` |
| `pathlib` | type annotation mismatch | [`pyscriptc explain PS1003`](cli.md#explain) | `posixpath.join arguments are str` |
| `subprocess` | type annotation mismatch | [`pyscriptc explain PS1003`](cli.md#explain) | `subprocess takes a list of str` |
| `sys` | type annotation mismatch | [`pyscriptc explain PS1003`](cli.md#explain) | `sys.exit argument is an int` |

## Dynamic reflection

Dynamic argument unpacking and keyword forwarding that cannot be lowered natively route to the island runtime.

| Module | Call or construct | Code | Diagnostic |
| --- | --- | --- | --- |
| `builtins` | `**kwargs` forwarding | [`pyscriptc explain PS1202`](cli.md#explain) | `update declares **kwargs: keyword forwarding (**kwargs) collects keyword arguments into a mapping at run time, and the native lane keeps no keyword mapping for a parameter; this code routes to the island lane (requires libpython); typed idiom: PEP 692 Unpack[TypedDict] declares the accepted keyword keys` |
