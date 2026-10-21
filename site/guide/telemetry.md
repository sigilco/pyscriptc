---
title: Telemetry and privacy
description: What pyscriptc collects, and how to turn it off.
---

Release builds of pyscriptc send pseudonymous usage events to PostHog.
Telemetry exists to answer one question: which unsupported constructs block real users the most, so the roadmap follows demand.

## What is sent

- `cli.invoked` with the subcommand name and version
- `compile.finished` with `duration_ms`, `outcome` (`ok`, `fences`, `check-failed`, `lower-error` or `build-error`), `fence_count`, `fence_families` (diagnostic family name to count, with `stdlib:<module>` collapsed to `stdlib`), the `checker`, `emit` and `driver` flags, `skip_check`, and `binary_bytes` when a binary was linked
- `coverage.reported` when you run `pyscriptc coverage`, with aggregate counts only: `files`, `definitions`, `fencedDefinitions`, `fences`, `rootFences`, `byFamily` (family to fence count) and `coverage_ratio` (fenced definitions over definitions)

Every event also carries the pyscriptc version, Python version, platform and a CI flag.
Fence codes, per-fence locations, definition names and imported module names are not sent.

Events carry a stable machine hash: a SHA-256 of a fixed salt, the OS machine id (where present), the network adapter address and the hostname.
It is not random and it is not reversible, but it is the same on every run, so one machine's events can be linked together.
The raw hostname and machine id are not sent.
No source code, file paths, usernames, or argument values are collected.

## Opting out

```bash
pyscriptc telemetry disable   # persistent off
pyscriptc telemetry status    # current state
```

Or set `PYSC_NO_TELEMETRY=1` or `DO_NOT_TRACK=1`.
Telemetry is always silent on CI and in development builds built without `POSTHOG_PROJECT_TOKEN`.

## First-run notice

The first time you run pyscriptc in an interactive terminal, it prints a notice on stderr listing what is collected and how to opt out, then records `notice_seen = true` in `~/.config/pyscriptc/config.toml` (or under `$XDG_CONFIG_HOME`).
No event is sent before that notice was shown.
The notice never prints, and so nothing is sent, when stderr is not a TTY, when `CI` is set, or when telemetry is disabled.
The `pyscriptc telemetry` command itself sends nothing.
