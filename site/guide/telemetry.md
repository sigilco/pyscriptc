---
title: Telemetry and privacy
description: What pyscriptc collects, and how to turn it off.
---

Release builds of pyscriptc send anonymous usage events to PostHog.
Telemetry exists to answer one question: which unsupported constructs block real users the most, so the roadmap follows demand.

## What is sent

- `cli.invoked` with the subcommand name and version
- `compile.finished` with `duration_ms`, `outcome` (`ok`, `fences`, `check-failed`, `lower-error` or `build-error`), `fence_count`, `fence_families` (diagnostic family name to count, with `stdlib:<module>` collapsed to `stdlib`), the `checker`, `emit` and `driver` flags, `skip_check`, and `binary_bytes` when a binary was linked
- `coverage.reported` when you run `pyscriptc coverage`, with aggregate counts only: `files`, `definitions`, `fencedDefinitions`, `fences`, `rootFences`, `byFamily` (family to fence count) and `coverage_ratio` (fenced definitions over definitions)

Every event also carries the pyscriptc version, Python version, platform and a CI flag.
Fence codes, per-fence locations, definition names and imported module names are not sent.

Events carry a randomly generated, salted machine hash.
No source code, file paths, hostnames, usernames, or argument values are ever collected.

## Opting out

```bash
pyscriptc telemetry disable   # persistent off
pyscriptc telemetry status    # current state
```

Or set `PYSC_NO_TELEMETRY=1` or `DO_NOT_TRACK=1`.
Telemetry is always silent on CI and in development builds built without `POSTHOG_PROJECT_TOKEN`.

On first use the CLI prints a one-line notice that telemetry is on and how to disable it.
