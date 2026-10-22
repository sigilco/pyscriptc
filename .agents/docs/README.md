# Development notes

These pages are for someone changing the compiler. User-facing docs live in [`docs/`](../../docs/README.md). The design overview is [`ARCHITECTURE.md`](../../ARCHITECTURE.md).

- [Invariants](invariants.md) — rules a change has to keep
- [Debug mode](debug.md) — `compile --debug`, `PYSC_DEBUG`, `--debug-dir` artifacts
- [Map](map.md) — which file owns a fence, a runtime call, or a test
- [Toolchain](toolchain.md) — C compiler driver resolution, Zig preference, and `ziglang`
- [Roadmap](roadmap.md) — language order after the stdlib queue, and where native `.so` files sit
