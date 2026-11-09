# probes/

Committed probe sources for the differential lanes: small programs, one construct each, that a lane compiles with the current compiler, runs next to the pinned CPython and byte-compares on stdout, stderr and exit code.

`probes/differential/` is where a per-PR probe for a newly lowered construct lands (the AGENTS.md differential probe rule).

`probes/divergences/` is where the divergence-ledger probes land, one per ledger row.

These sources are inputs only; the runner and the byte comparison are the nightly lane's job, and nothing here is a frozen compat2 gate file.
