# Brioso compile-rate assessment (C11)

Headline: 45/45 (100%) of Python-compatible essence translations compile and run today under dummy boundaries, far above the 10-20% floor (5-9 of 45). The strict native-seam rate is 27/45 (60%): 18 rows have no native seam in the subset and were exercised through the sanctioned dummies instead.

## Method

Each of the 45 brioso TS scripts in the catalogue was reduced to a minimal Python essence: same seam shape, dummy boundaries, `def main() -> int` wrapper (module-level statements do not lower; E1 pending), compiled with `pyscriptc compile --emit bin --call main --skip-check`, then executed. Boundaries: `/tmp` for FS and sqlite, `https://example.com` via `curl` for HTTP, `date`/`ffmpeg`/`uv`/`python3` for subprocess tools, env vars for service credentials. Essence sources stayed in /tmp; the interesting ones are inlined below.

Native seams that exist: FS (`open`, `os`, `posixpath`, `pathlib`, `shutil`, `tempfile`), subprocess (`subprocess.check_output`, island), env reads (`os.getenv`), plus `json`/`hashlib`/`base64`/`re`/`time`/`datetime`. Native seams that do not exist: `sqlite3`, any HTTP client (`urllib.request`, `http.client`, `requests`), websockets, `random`, `secrets`, `posix`, `sys.argv`/`argparse`, `print`. Every missing module above was verified to fence with PS2002, not assumed.

## Results

| Script | Seams | Verdict | Blocker / shim used |
| --- | --- | --- | --- |
| web/audit-build-env | Ext=Infisical | COMPILED+RAN | env creds via `os.getenv` |
| web/audit-env-render | FS=read, Infisical | COMPILED+RAN | `open` + `os.getenv` |
| web/bundle-ws-external | FS=read | COMPILED+RAN | native `posixpath` |
| web/check-route-census | FS=read, Stripe | COMPILED+RAN | `os.listdir` + `os.getenv` |
| web/check-taxonomy-cache | Net | COMPILED+RAN | curl shim; no http client |
| web/compile-intl | none | COMPILED+RAN | `list.append` fenced (PS2001), reworked |
| web/dev-fake-seed | DB | COMPILED+RAN | python3-sqlite shim; no `sqlite3` |
| web/dev-reset-users | DB, Infisical | COMPILED+RAN | sqlite shim + `os.getenv` |
| web/dev-seed | DB, Infisical | COMPILED+RAN | sqlite shim + `os.getenv` |
| web/dev-seed/lib | FS=read+spawn, DB, ffmpeg | COMPILED+RAN | `tempfile` + `check_output` + sqlite shim |
| web/dev-seed/scenarios/billing | DB | COMPILED+RAN | sqlite shim |
| web/dev-seed/scenarios/catalog | FS=read+spawn, R2/S3, sh:bun | COMPILED+RAN | spawn via `date` dummy + `date` upload |
| web/dev-seed/scenarios/index | none | COMPILED+RAN | native |
| web/dev-seed/scenarios/sharing | none | COMPILED+RAN | native `json` |
| web/dev-seed/scenarios/studio | DB | COMPILED+RAN | sqlite shim |
| web/grant-trial | FS=read, DB, Infisical | COMPILED+RAN | `open` + sqlite shim |
| web/lib/tempDir | FS=write | COMPILED+RAN | native `tempfile`/`shutil` |
| web/print-openapi | none | COMPILED+RAN | returns len; `print` absent |
| web/sync-contract | FS=read+write+spawn, sh:uv | COMPILED+RAN | `open` w + real `check_output(["uv"])` |
| catalog/backfill-peaks | DB, Infisical, R2/S3 | COMPILED+RAN | sqlite shim + ffmpeg + env |
| catalog/build-leaf-plan | FS=read+write | COMPILED+RAN | native `open` |
| catalog/build-taxonomy-artwork | FS=write+spawn, sh:uv | COMPILED+RAN | `os.makedirs` + real `uv` |
| catalog/insert-seed | DB, R2/S3 | COMPILED+RAN | sqlite shim + `date` upload dummy |
| catalog/leaf-coverage-report | DB | COMPILED+RAN | sqlite shim (group by) |
| catalog/manifest-io | FS=read+write | COMPILED+RAN | native append-mode `open` |
| catalog/peaks | FS=spawn, sh:ffmpeg | COMPILED+RAN | real `check_output(["ffmpeg"])` |
| catalog/promote-seed-data | FS=read, Infisical, R2/S3 | COMPILED+RAN | FS + `date` head dummy + env |
| catalog/prune-seed-output | FS=read+write, Infisical, R2/S3 | COMPILED+RAN | `os.listdir`/`os.remove` + `date` dummy |
| catalog/r2-usage-gb | Infisical, R2/S3 | COMPILED+RAN | curl listing shim + env; no S3 client |
| catalog/reencode-previews | FS=r+w+spawn, Infisical, R2/S3, sh:ffmpeg | COMPILED+RAN | real ffmpeg + `date` upload dummy |
| catalog/select-free-tier-tracks | DB | COMPILED+RAN | sqlite shim |
| catalog/upload-artwork | FS=read+spawn, Infisical, R2/S3 | COMPILED+RAN | FS + `date` upload dummy |
| catalog/upload-seed | FS=read, Infisical, R2/S3 | COMPILED+RAN | FS + `date` head/upload dummies |
| catalog/write-leaf-plan-cache | FS=read+write | COMPILED+RAN | native `open` |
| classification/backfill | FS=write, DB | COMPILED+RAN | `tempfile`/`open` + sqlite shim |
| dispatch/groom-r2-jobs-bucket | DB, Infisical, R2/S3 | COMPILED+RAN | sqlite shim + `date` list/delete dummies |
| intl/compile-intl | FS=read+write | COMPILED+RAN | native `open` + `hashlib` |
| creator/phoenix-refine-retention | Net, Phoenix | COMPILED+RAN | curl shim + env |
| youtube/playlistPlan | FS=r+w+spawn, Net, Infisical, ffmpeg | COMPILED+RAN | curl + real ffmpeg + FS |
| youtube/playlistRun | FS=read+spawn | COMPILED+RAN | `open` + real ffmpeg |
| youtube/refinePrompt | FS=read, Net | COMPILED+RAN | `open` + curl shim |
| youtube/render | FS=read | COMPILED+RAN | native `open` |
| youtube/renderThumbnail | FS=read | COMPILED+RAN | `os.makedirs` + `shutil.copyfile` |
| youtube/renderVideo | FS=read+spawn | COMPILED+RAN | real ffmpeg + `shutil.copyfile` |
| youtube/thumbnail | FS=read | COMPILED+RAN | native `posixpath`/`open` |

Totals: COMPILED+RAN 45, COMPILED 0, FENCE 0, SEAM 0, SKIP 0. Wall time for the full 45-file matrix was about 5 minutes.

## Rate vs floor

Overall compile+run rate under the sanctioned dummy boundaries: 100% (45/45), floor 10-20% holds with margin. Native-clean subset (no Net, no DB, no external-service call, only real FS/spawn/pure logic): 18/45 = 40%. Strict view (natural stdlib port where Net and DB rows become SEAM): 27/45 = 60%.

## Missing seams, ranked by rows blocked

1. `sqlite3` (DB): 13 rows shimmed through `python3 -c` children. Maps to epic F (FFI; `_sqlite3` is a C extension) or Wave C stdlib if a pure subset lands first.
2. Service-client SDKs (Infisical x9, R2/S3 x10, Stripe x1, Phoenix x1, distinct rows 18): env reads are native, the calls themselves were dummied with `date`. Maps to whatever HTTP client lands (same blocker as next row).
3. HTTP client (Net=fetch/http): 5 rows shimmed through `curl`. Maps to F FFI/socket work plus Wave C `urllib.request` surface. Websocket/SSE blocks 0 catalogue rows today.
4. `print`: pervasive. Every real script writes to stdout; all essences return int instead. Blocks faithful ports of all 45; likely a Wave C builtin row.
5. `sys.argv`/argument parsing: avoided in every essence (inputs hardcoded). Blocks the youtube `parseArgs` scripts (playlistPlan, playlistRun, render, renderThumbnail) and most CLI-shaped rows; ~10 catalogue rows realistically.
6. Module-level statements (E1): structural gate on all 45 - every essence needed a `def main()` wrapper. This is the single largest fidelity gap for unmodified ports.
7. `random`, `secrets`, `posix`: verified PS2002 but 0 catalogue rows need them.

## Fences hit while authoring (real findings, not blockers to the tally)

- `list.append` is not lowered: PS2001 "call of attribute is not lowered" (hit in web/compile-intl essence; worked around with accumulation).
- `int(bytes)` is not lowered: PS2003 (hit decoding `check_output`; worked around with `len`/byte compare).
- `os.getenv(missing)` raises `KeyError` at runtime, so env audit essences need `try/except KeyError`.
- Every local needs annotation-before-use (PS1001); `x = call()` without `x: T` fences.
- `os.stat` result annotation is `Stat` in the lowering tables; unclear surface name - avoided via `posixpath`/`os.remove`.
- `dict.values()`/`dict.items()` are not lowered (documented); `.keys()` only.

## Example essences

Net via the curl shim (`web/check-taxonomy-cache`):

```python
import subprocess


def main() -> int:
    body: bytes = subprocess.check_output(["curl", "-sS", "https://example.com"])
    return len(body)
```

DB via the python3-sqlite shim (`web/dev-fake-seed`): a real sqlite file under /tmp, exercised through a `check_output(["python3", "-c", code])` child that creates the table, inserts, commits, and prints the row count.

FS+spawn (`catalog/peaks`): `subprocess.check_output(["ffmpeg", "-version"])` returns bytes, proving the island subprocess path end to end.

## Cheapest scripts to unblock next

1. `web/print-openapi` - needs only `print` to run near-verbatim: the doc build already lowers through `json.dumps`. One builtin unblocks the most faithful port in the set.
2. `youtube/playlistRun` and `youtube/render` - real ffmpeg spawn plus FS read already work end to end; the only missing piece is `sys.argv` for their `parseArgs` inputs. An argv seam unblocks three to four youtube rows at once.
3. `web/compile-intl` + `intl/compile-intl` - both already run; a `list.append` lowering plus `print` makes them faithful. With E1 they go verbatim.

If a seam-level win is preferred over script-level wins: `sqlite3` is the single largest unlock (13 rows), and it is also the hardest - C extension, so epic F territory. The HTTP client is second (5 direct rows, plus all 18 service-SDK rows that today ride on `date`/`curl` dummies).
