# pyscriptc

pyscriptc compiles one annotated Python file to a native binary. The file has to stay inside the `gates-v1` subset. Anything outside that subset stops the compile and names the reason.

- [Getting started](getting-started.md)
- [What is supported](supported.md) (status tables, generated from the compiler)
- [Top-100 library compatibility](../.agents/docs/compat-top100.md)
- [Example scripts](examples.md)
- [Import classification](import-classifier.md)
- [Config](config.md)
- [Cross-checking with mypy and pyright](external-oracles.md)

The design of the compiler is in [`architecture.md`](architecture.md).
