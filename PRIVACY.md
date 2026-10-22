# Privacy and Telemetry Notice

pyscriptc collects anonymous usage metrics to help the maintainers understand how the compiler is used, find bugs and prioritize what to support next. This page describes exactly what is sent, what is never sent, and how to turn it off. It is not legal advice.

## What is collected

When telemetry is enabled (it is on by default in release builds, off in CI and a no-op in dev checkouts), the tool sends one event per invocation over HTTPS to PostHog EU (hosted in Frankfurt, Germany):

- The subcommand you ran (for example `compile` or `coverage`)
- The pyscriptc version and the CPython version (major.minor only)
- The operating system platform
- For `compile` and `coverage`: the outcome (`ok`, `fences`, `check-failed`, `lower-error`, `build-error`), the duration in milliseconds, and aggregate fence counts with diagnostic family names
- A randomly generated anonymous install id (a uuid4 stored only in `~/.config/pyscriptc/telemetry-id`)

The full field-by-field list is in the [telemetry guide](https://pyscriptc.pages.dev/guide/telemetry/).

## What is never collected

- Personal identity, emails, or usernames
- Source code, file names, or local file system paths
- Git repository URLs or commit metadata
- Imported module names, fence codes with locations, or definition names
- Environment variables, secrets, API keys, or tokens
- Raw user arguments or inputs
- Hardware identifiers, host names, or MAC addresses

Every event asks PostHog not to store the client IP address, not to geolocate it, and not to build a person profile. The network connection itself necessarily exposes your IP address to the server; the project is configured to discard it.

## Retention and processing

Raw events are kept for 90 days and then deleted. Events are transmitted directly to PostHog EU over HTTPS; the data is not sold or shared with third parties. PostHog acts as the data processor under its standard data processing agreement.

## How to opt out

Any one of these disables telemetry completely:

```bash
export DO_NOT_TRACK=1          # the standard environment variable
export PYSC_NO_TELEMETRY=1     # pyscriptc-specific
pyscriptc telemetry disable    # persists to ~/.config/pyscriptc/config.toml
```

`pyscriptc telemetry status` shows the effective state and why, and `pyscriptc telemetry reset-id` rotates the install id, cutting any link to previously sent events.

One stderr line announces that telemetry is enabled on first use.
