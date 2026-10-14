# Build benchmarks

scriptc's `benchmarks/builds` holds two multi-module programs that the runtime suite also runs on large inputs:

- `log-summary` reads a log file, filters by status, and prints a summary (`options`, `parse`, `aggregate`, `format`, `main`).
- `inventory-report` loads a catalog, selects a category, and renders a report (`load`, `select`, `report`, and the modules around them).

Both are the shape a later pyscriptc should compile: several modules, a `main`, and a stdout that matches the host language byte for byte. They do not lower under `gates-v1`. A directory compile and a plain f-string do lower now. These programs still depend on imports and syntax outside the surface.

Until those lowerings exist, the programs stay on CPython. This directory is the placeholder for that suite, next to the same path in scriptc.
