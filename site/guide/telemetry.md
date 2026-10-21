---
title: Telemetry and privacy
description: What pyscriptc collects, and how to turn it off.
---

Release builds of pyscriptc send pseudonymous usage events to PostHog (EU cloud, `eu.i.posthog.com`).
Telemetry exists to answer one question: which unsupported constructs block real users the most, so the roadmap follows demand.
See the [privacy notice](/guide/privacy/) for the legal basis, retention and your rights.

## What is sent

Every field of every event, in full:

| Event | Field | Example | Why |
| --- | --- | --- | --- |
| all | `distinct_id` | `3f2b8c1e-...` (random uuid4) | counts distinct installs, not people; see below |
| all | `pyscriptc_version` | `0.4.0` | which release has which problem |
| all | `python_version` | `3.14` (major.minor only) | which Python versions to support |
| all | `platform` | `darwin`, `linux` | which targets matter |
| all | `$ip` (null), `$geoip_disable` (true), `$process_person_profile` (false) | | ask PostHog not to store the IP, not to geolocate and not to build a person profile |
| `cli.invoked` | `subcommand` | `compile` | which commands are used |
| `compile.finished` | `duration_ms` | `412` | performance regressions |
| `compile.finished` | `outcome` | `fences` | `ok`, `fences`, `check-failed`, `lower-error` or `build-error` |
| `compile.finished` | `fence_count` | `3` | how often programs are blocked |
| `compile.finished` | `fence_families` | `{"union-types": 2, "stdlib": 1}` | which kinds of constructs block users (fixed vocabulary, `stdlib:<module>` collapsed to `stdlib`) |
| `compile.finished` | `checker`, `emit`, `driver` | `pyrefly`, `bin`, `clang` | which toolchain options are used (flag choices from a fixed list) |
| `compile.finished` | `skip_check` | `false` | explains an absent `checker` |
| `coverage.reported` | `definitions`, `fencedDefinitions`, `fences`, `rootFences` | `40`, `3`, `5`, `2` | aggregate coverage only |
| `coverage.reported` | `byFamily` | `{"union-types": 5}` | family to fence count |

Not sent, ever: file names or paths, user names, host names, machine ids, MAC addresses, source text or snippets, imported module names, fence codes, per-fence locations, definition names, argument values, environment variables.
The server connection necessarily exposes your IP address to the host; the project is configured to discard it (see the privacy notice).

## The install id

The id is a random uuid4 created the first time an event is sent and stored in `telemetry-id` next to the config file (`~/.config/pyscriptc/telemetry-id`, or under `$XDG_CONFIG_HOME`), with mode 0600.
Nothing about your machine is read to make it: no machine id, MAC address or hostname.
It is still a persistent identifier for the install, so it counts as pseudonymous data, not anonymous data.
Run `pyscriptc telemetry reset-id` (or delete the file) to throw it away; a new unrelated id is created at the next send.
Older releases derived the id from a hash of machine identifiers; this release stops using it and does not migrate it.

## Opting out

```bash
pyscriptc telemetry disable   # persistent off
pyscriptc telemetry status    # current state
pyscriptc telemetry reset-id  # new random id
```

Or set `PYSC_NO_TELEMETRY=1` or `DO_NOT_TRACK=1`.
Telemetry is always silent on CI and in development builds built without `POSTHOG_PROJECT_TOKEN`.

## First-run notice

The first time you run pyscriptc in an interactive terminal, it prints a notice on stderr listing what is collected, the retention period and how to opt out, then records `notice_seen = true` in `~/.config/pyscriptc/config.toml` (or under `$XDG_CONFIG_HOME`).
No event is sent before that notice was shown.
The notice never prints, and so nothing is sent, when stderr is not a TTY, when `CI` is set, or when telemetry is disabled.
The `pyscriptc telemetry` command itself sends nothing.
