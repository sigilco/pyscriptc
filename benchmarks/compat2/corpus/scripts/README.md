# Scripts corpus

Pure Python programs with deterministic stdout and no third party dependencies. Each directory holds `main.py` (the program), `expected.txt` (stdout captured from CPython 3.14.8 with `python -I`) and `meta.json` (license and origin).

These are original implementations of well known kernels (pyperformance, Benchmarks Game, Shed Skin and Codon style examples). No upstream source was copied, so every program is MIT licensed under `meta.json`.

Regenerate an `expected.txt` only inside the Linux container: `.agents/docker/run.sh /opt/venv/bin/python -I main.py`.

## Applies to

The `scripts` area of compat2, category `program`.
