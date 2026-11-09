# yt-dlp composite: definition before measurement (2026-10-30)

This note defines the composite benchmark for a pyscriptc build of yt-dlp before any numbers exist. The definition here is frozen; results go in `benchmarks/ytdlp_composite/REPORT.md` and reference this note.

## Dimensions

Twelve dimensions in three groups. Lower is better for every timed/sized dimension unless stated.

Performance (wall time, seconds, min of N=7 samples, interleaved, after 2 warmups; N=5 for the download run because it is slower; report 1-minute load average at the start of each timing block):

1. `version_s`: `yt-dlp --version` process wall time.
2. `extractors_s`: `yt-dlp --list-extractors` process wall time.
3. `extract_s`: wall time of an offline generic-extractor info run (`-j --no-warnings --user-agent pinned http://127.0.0.1:PORT/sample.mp4`, output redirected to /dev/null) against a local `python -m http.server`.
4. `download_s`: wall time of an offline generic download (`-o out.%(ext)s --no-warnings --user-agent pinned`, same local server, small fixed mp4), including writing the file.
5. `rss_mb`: peak resident set size of the `extract_s` run, measured with GNU time `-v` as maxrss, in MiB.

Distribution (yes/no, not timed):

6. `files_n`: number of files required to ship the artifact (1 for a single file; counted with `find -type f | wc -l` for trees).
7. `needs_python`: yes if a system Python install is required at run time, no if the artifact carries its own interpreter.
8. `relocatable`: yes if the shipped artifact is a single file or a directory tree that runs after `cp -r` to another path on the same OS, no otherwise.
9. `clean_container`: yes if the artifact runs in a container image with only libc (and CA certs), no otherwise. Checked with docker/podman if present, else `ldd` plus `env -i` on the host.

Disk (bytes, `du -sb` semantics):

10. `install_bytes`: total installed bytes of the distribution (venv tree for B1, single file for B2, bundle tree for pyscriptc).
11. `artifact_bytes`: bytes of the shipped artifact itself (the file you copy to another machine; for B1 the venv's yt-dlp entry plus its import closure is not shippable as one unit, so artifact_bytes for B1 is the wheel install size, same as install_bytes).

## Baselines

- B1: `yt-dlp==2026.10.26` installed with `uv pip` into a CPython 3.14.9 venv on this VPS. needs_python=yes, files_n=venv file count, relocatable=no (venv paths are baked).
- B2: the official `yt-dlp_linux` PyInstaller onefile binary from the matching GitHub release (2026.10.26). Also the `yt-dlp` zipapp from the same release as a secondary reference (B2z); it needs a system python3.
- B3: CPython 3.14.9 running yt-dlp from a source checkout (output oracle only; not part of the composite).

Candidate variants: pyscriptc `--embed-python bundle` (V-bundle) and `--embed-python host` (V-host), both with `--island --fallback=cpython --program --skip-check`, built from the same yt-dlp 2026.10.26 tree. V-host is expected to fail `relocatable` and `clean_container`; it is reported for completeness.

## Normalization and weights

Each dimension is normalized as a ratio to the same dimension of the baseline (candidate / baseline for timed and sized dimensions; for yes/no dimensions the ratio is 1.0 when the candidate matches the baseline answer and 2.0 when it is worse, 0.5 when it is better in the yes/no sense). Geometric mean over all 11 dimensions (B3 excluded) gives the composite per baseline. Weights are uniform; the yes/no dimensions therefore carry 4/11 of the composite, performance 5/11, disk 2/11.

## Pass rule

PASS if composite(V-bundle) < composite(B1) AND composite(V-bundle) < composite(B2), AND no single dimension is worse than 2.0x its baseline value. The same rule is reported for V-host but the pass/fail verdict is on V-bundle.

## Size levers

Allowed: strip, stdlib pruning to modules actually imported, compiler optimizing flags, omitting debug info. Not allowed: zstd, upx, or any executable compressor, unless a baseline ships compressed (none do; PyInstaller onefile uses zlib only, which is not counted against us).

## Honesty clause

If the candidate does not beat a baseline, the report names the losing dimensions and the lever that would fix each one, with an estimate of the cost of that lever.

## Results (measured 2026-10-30, VPS, CPython 3.14.9, load average 1.0 at the end of the run)

Machine: 6 cores, 11.6 GiB RAM, Linux 6.12, Debian 13, another session was running throughout (load average 1.0 to 3.2 during the run, so every timing is the min of 7 interleaved samples after 2 warmups).

Corpus: yt-dlp 2026.10.26 source tree (1044 Python files) plus a generated `yt_dlp/extractor/lazy_extractors.py`. Sample media: a 20 021 byte ffmpeg `testsrc` mp4 served by `python -m http.server 8614` on 127.0.0.1.

Output correctness: the compiled bundle's `-v --simulate -j` output on the local sample is byte-identical to CPython's with the epoch, timestamp and user agent pinned, and `--list-extractors` is identical (1755 lines) to B1, B2 and B3.

| Variant | `--version` s | `--list-extractors` s | extract s | download s | RSS MiB | files | installed bytes | shipped bytes | needs Python | relocatable | clean container |
|---|---|---|---|---|---|---|---|---|---|---|---|
| V-bundle (safe trim, deflated zip) | 1.079 | 1.513 | 2.325 | 2.399 | 163.0 | 152 | 87 230 329 | 87 230 329 | no | yes | yes |
| V-minimal (minimal trim, deflated zip) | 1.044 | 1.504 | 2.285 | 2.353 | 162.2 | 152 | 84 185 290 | 84 185 290 | no | yes | yes |
| V-host (host libpython) | 0.971 | 1.540 | 2.294 | 2.226 | 159.6 | 1 | 11 273 696 | 11 273 696 | yes | no | no |
| B1 (uv venv) | 0.270 | 0.383 | 1.131 | 1.173 | 49.8 | 2999 | 163 721 989 | 163 721 989 | yes | no | no |
| B2 (`yt-dlp_linux`, PyInstaller onefile) | 1.277 | 1.492 | 2.288 | 2.208 | 74.1 | 1 | 41 166 968 | 41 166 968 | no | yes | yes |
| B2z (`yt-dlp` zipapp) | 1.125 | 1.906 | 2.643 | 2.425 | 65.3 | 1 | 12 010 686 | 3 073 072 | yes | yes | no |
| B3 (CPython oracle, `python -m yt_dlp`) | 0.337 | 0.461 | 1.350 | 1.241 | 49.6 | n/a | n/a | 32 527 384 | yes | n/a | n/a |

Composite (geometric mean over the 11 dimensions, lower is better):

| Candidate | vs B1 | vs B2 |
|---|---|---|
| V-bundle | 1.375 | 1.467 |
| V-minimal | 1.357 | 1.447 |

PASS rule: the candidate must be below 1.0 against both baselines and no dimension may be worse than 2.0x. The verdict is FAIL: the candidate does not beat either baseline, and four dimensions are worse than 2x against B1.

Losing dimensions and the lever for each:

- `rss_mb` is 3.28x B1 and 2.20x B2: the island bootstrap holds all 1044 module sources as Python strings in one dict and the CPython interpreter beside it. The lever is to ship the program as a zip of `.pyc` bodies instead of source text, which is the same lever the stdlib zip already uses.
- `version_s` and `extractors_s` are 3.7x to 4.0x B1 and 0.82x to 1.01x B2: the cost is the bootstrap compile-and-exec of a 10.6 MB source blob (measured 0.24 s to compile and 0.72 s to exec on CPython 3.14). The lever is to precompile the island seed to marshalled code objects, which measures 10.1 MB and 8 ms to load, and to deflate the stored blob, which measures 2.4 MB compressed and 67 ms to decompress.
- `relocatable` is 2.0x B1 by the note's rule (a venv is relocatable in the sense that it moves, but it is not a single self-contained artifact). Against B2 the candidate matches (both are relocatable trees or files), so this dimension is not what loses the composite.
- `files_n` is 7.27x B2: the bundle is a 152 file tree where B2 is one file. The lever is to fold the tree into the binary, which the onefile mode of PyInstaller does and pyscriptc does not have.
- `install_bytes` is 2.12x B2: the bundle carries a 32 MiB libpython plus a 6.8 MiB stdlib zip plus the 10.8 MiB binary. The minimal trim and the deflated zip brought this from 96.9 MB down to 84.2 MB.

Dimensions the candidate wins: `needs_python` (0.5x, no system Python needed), `install_bytes` and `artifact_bytes` against B1 (0.51x to 0.53x), and the timing dimensions against B2 (0.82x to 1.09x).

The clean-container claim was checked with docker against `debian:trixie-slim` (glibc 2.41, no Python): the bundle runs (`/app/bin/yt_dlp --version` prints 2026.10.26) and so does B2; the zipapp fails (`env: 'python3': No such file or directory`) and V-host fails (`libpython3.14.so.1.0: cannot open shared object file`). The bundle needs glibc 2.38 or newer.

zstd and upx were not used, and the note forbids them: no baseline ships a compressed executable (PyInstaller's onefile uses zlib for its own payload, which is not an executable compressor applied to the artifact).

The lazy extractor question from the earlier session: it is not ours. The compiled binary has `yt_dlp/extractor/lazy_extractors.py` in its module table and `LAZY_EXTRACTORS.value` is True, so the extractor set loads lazily exactly as CPython does. The earlier "Lazy loading extractors is disabled" line came from a source tree that did not have the generated `lazy_extractors.py`; generating it with upstream's own `devscripts/make_lazy_extractors.py` fixed it.
