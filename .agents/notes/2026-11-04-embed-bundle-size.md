# 2026-11-04 embed bundle size measurement (issue #885)

Measured on `work/embed-size` (origin/master 2ba78c62) on Linux x86_64,
pack `cp3149-linux-x86_64-20261021` (CPython 3.14.9, PBS install_only_stripped).

Bundles built with the same commands as the embed acceptance step:

- stdlib: `uv run pyscriptc compile --island tests/fixtures/embed/island_fractions.py --emit bin --embed-python=bundle`
- sitedep: same for `island_dependency.py`
- datafile: `uv run pyscriptc compile --program tests/fixtures/embed/datafile --island --fallback=cpython --emit bin --embed-python=bundle`

## Per-directory du (KB, `du -sk`) of the produced bundles

stdlib bundle (largest of the three is sitedep at 83116 KB):

| path | KB |
| --- | --- |
| stdlib/prog (total) | 82848 |
| bin/ | 544 |
| lib/ | 82152 |
| lib/libpython3.14.so | 32860 |
| lib/libpython3.14.so.1.0 | 32860 |
| lib/python314.zip | 16432 |
| lib/python3.14/ (lib-dynload landmark) | 0 |
| share/ | 152 |

sitedep bundle: 83116 KB total; the extra 268 KB over stdlib is
`lib/python3.14/site-packages/` (248 KB: pathspec + its dist-info) plus
licenses. datafile bundle: 82852 KB total (4 KB staged data in bin/).

## What makes it 83 MB

1. `libpython3.14.so` is a 32.8 MB **copy** of `libpython3.14.so.1.0`
   (identical md5). `embed.stage_bundle` copies every `libpython3.14.so*`
   glob entry with `shutil.copy2`, dereferencing the pack's symlink
   (`src/pyscriptc/embed.py:518-523`). The ELF loader only needs the
   real file plus one symlink, so ~32.8 MB is a pure duplicate.
2. `lib/python314.zip` is 16.4 MB (stored, uncompressed `.pyc` bodies of
   the safe trim). Top contributors: `_pydecimal` 222 KB, `turtle` 181 KB,
   `typing` 168 KB, `pdb` 157 KB, `pydoc` 143 KB, `email` 449 KB,
   `asyncio` 678 KB, `_pyrepl` 357 KB, `unittest` 318 KB, `xml` 380 KB,
   `multiprocessing` 415 KB, `http` 236 KB, `logging` 208 KB,
   `urllib` 168 KB, `wsgiref` 76 KB, `curses` 32 KB.
3. `bin/prog` ~544 KB (compiled program + island runtime).
4. `share/licenses` 152-172 KB (CPython + third-party texts + NOTICE).

## Trim options considered

- Drop the duplicate libpython copy (symlink instead of copy):
  saves ~32.8 MB -> bundle ~50 MB, under the 60 MB Linux gate.
  macOS unaffected (single `.dylib`, no symlink family).
- Extend `SAFE_TRIM` with developer-facing stdlib (`pdb`, `pydoc`,
  `doctest`, `unittest`, `turtledemo` already trimmed, `_pyrepl`,
  `curses`, `wsgiref`, `mailbox`, `turtle`...): risky without a
  documented reason; the safe trim contract says grow it only with a
  documented reason, never to chase a smaller zip.
- The pack's `lib/python3.14/site-packages/pip` (6.6 MB) never reaches
  the bundle; the sitedep bundle's site-packages only carries what the
  program imports (pathspec).
- No `__pycache__` duplicates found in the produced bundles (stdlib is
  zipped from fresh `.pyc` compiles; program data skips `__pycache__`).

## Conclusion

The single safe trim is the libpython symlink duplicate: ~32.8 MB off
every Linux bundle, no behavior change, the three acceptance programs
still match CPython. Remaining size ~50 MB < 60 MB Linux gate.
