---
title: Beta testing
description: Run your own scripts on pyscriptc every week and tell us what stops.
---

# Beta testing

pyscriptc 0.7.0 is the "island is the floor, native is the ceiling" release. Any typed script you point it at should run: the native tier lowers what it can prove, and `--island` routes everything else to an embedded CPython. Each release moves more of the typed subset from island to native.

On the 45 script corpus (`benchmarks/scripts_corpus`), 45 of 45 match CPython with `--island` and 21 of 45 run fully native, measured on master after 0.7.0.

## The weekly loop

Once a week, take one script you actually use and run:

```sh
uv tool upgrade pyscriptc || uv tool install pyscriptc
pyscriptc coverage your_script.py
pyscriptc compile your_script.py --emit bin -o ./your_script --island
./your_script
```

`coverage` tells you which parts compile to native code and which are unsupported. The binary should print what `python your_script.py` prints.

## What to report

Open an issue at https://github.com/sigilco/pyscriptc/issues with the script (or a reduced version, `pyscriptc reduce` helps), the command, the output of `pyscriptc doctor`, and one of:

- the binary output differs from CPython (the most useful report)
- `--island` fails to compile (a `PSxxxx` code and its message)
- an unsupported construct you expected to compile

Native-only reports are welcome too, but a wrong answer under `--island` is the bug that matters most.
