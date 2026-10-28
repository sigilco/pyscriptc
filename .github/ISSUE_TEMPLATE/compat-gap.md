---
name: Compat gap
about: A construct, builtin, stdlib module or PyPI package does not compile or behaves differently from CPython
labels: compat-gap
---

The type is set by the label, so the title needs no "Compat gap:" prefix.
For a general bug (crash, wrong output on code that does compile), use the bug report template instead.

## The code that does not compile

Paste the smallest annotated script that shows the gap.

## `pyscriptc coverage` output

Run `pyscriptc coverage FILE` on the script and paste the output. `coverage` needs no C toolchain.

## The fence code

The `PSxxxx` code `coverage` or `compile` reports (leave empty if none).

## CPython output versus pyscriptc output

For a wrong-output report: what `python3 FILE` prints, and what the compiled binary prints.

## Environment

pyscriptc version (`pyscriptc --version`), OS, CPython version used for comparison.
