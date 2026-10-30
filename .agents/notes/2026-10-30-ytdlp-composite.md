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
