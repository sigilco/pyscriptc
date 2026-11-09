# yt-dlp composite benchmark

The definition, the baselines, the normalization and the pass rule are frozen
in `.agents/notes/2026-10-30-ytdlp-composite.md` and were committed before any
number was measured.

| Path | What it holds |
| --- | --- |
| `run.py` | the runner: builds nothing, measures the candidates and baselines, prints the dimension table and the composite |
| `_rss.py` | the peak-RSS helper (GNU time is not installed; the helper reports `RUSAGE_CHILDREN.ru_maxrss`) |
| `results.json` | the measured numbers of the 2026-10-30 run on the shared VPS |

## Running it

The runner expects the artifacts to exist and points at them with environment
variables, because a compile of the yt-dlp tree is a build step the benchmark
does not own (see the note for the two compile commands):

```bash
YTDLP_BUNDLE=/tmp/ytbench/outbundle/yt_dlp/bin/yt_dlp \
YTDLP_BUNDLE_ROOT=/tmp/ytbench/outbundle/yt_dlp \
YTDLP_B1=/tmp/ytbench/b1venv/bin/yt-dlp \
YTDLP_B1_ROOT=/tmp/ytbench/b1venv \
YTDLP_B2=/tmp/ytbench/dl/yt-dlp_linux \
YTDLP_B2Z=/tmp/ytbench/dl/yt-dlp.zipapp \
YTDLP_B3=/opt/mise/installs/python/3.14.9/bin/python3.14 \
python benchmarks/ytdlp_composite/run.py --json results.json
```

Every timing is the min of 7 interleaved samples after 2 warmups (2 for the
download run), and the 1-minute load average is recorded with each timing
block, because the VPS is shared.
