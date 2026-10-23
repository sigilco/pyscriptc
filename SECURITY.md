# Security Policy

## Supported versions

Only the latest minor release receives security fixes. Upgrade to the newest `pyscriptc` on PyPI before reporting.

## Reporting

Report vulnerabilities privately via [GitHub private vulnerability reporting](https://github.com/sigilco/pyscriptc/security/advisories/new). Do not open a public issue. Please include the pyscriptc and CPython versions, the offending input program, and the produced output or error.

Expect an acknowledgement within 7 days and a status update within 30 days. Fixes ship in the next release, with a hotfix for breakages that block installation or first run.

## Scope

- The compiler parses and lowers source to LLVM IR; it does not execute the programs it compiles at compile time. Constant folding and probing operate on the AST/IR, not by running your code.
- The optional `--fallback=cpython` tier ("the island") is the one component that links and runs CPython. Bugs in island isolation or the `ps_island_*` runtime are in scope and highest priority.
- Telemetry is opt-out and documented in `PRIVACY.md`; unexpected collection or transmission of source text is a vulnerability.
- Supply chain: releases publish to PyPI via [trusted publishing](https://docs.pypi.org/trusted-publishers/) from `release.yml` with a required-reviewer environment. Report anything that could compromise the publish path (workflow injection, artifact tampering).

## Out of scope

- Vulnerabilities in programs compiled by pyscriptc.
- Issues that require write access to the machine running the compiler.
