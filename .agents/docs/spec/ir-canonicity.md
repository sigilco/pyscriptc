# IR canonicity and hashing

Design review for `canonical.py` and the `probes/IR_HASHES.json` baseline. The IR stays `dict[str, Any]` (decision 2026-10-15). Everything here is checked against the code at master 2a07bb8 and against real dumps produced by the CLI on `examples/gates_try.py`, `examples/invoice.py` and `examples/fence_method.py`. One contradiction with the plan's assumptions was found and is called out in section 6: synthetic class order in the IR varies run to run today, so sorting `classes` is not optional.

## 1. Key inventory

Verified by grepping every dict literal in `lower.py` and `restlower.py`, by reading the validators in `ir.py`, and by walking the three dumps recursively. The full key vocabulary the module dict can carry is below. Every nested `span` is set by `span_of` (`errors.py:32`) from `lineno`/`col_offset`, so its shape is always `{file, line, col, endLine, endCol}`.

Module level (built at `lower.py:211` and `lower.py:271`, merged at `package.py:63`):

- `irVersion`: canonical. Constant `1`. Never varies at fixed input. Keeping it in the hash is free and ties `IR_HASHES.json` to the IR contract.
- `subset`: canonical. Constant `"gates-v1"`.
- `cpython`: incidental. `sys.version.split()[0]` at `lower.py:214`, `lower.py:274`, `package.py:66`. It varies with the interpreter running the compiler, not with the compiler or the input. `assert_same_cpython` (`ir.py:351`) already refuses cross-version emission, so the stamp is enforced where it matters. Strip, and record the interpreter version beside the hash in the baseline file instead of inside it.
- `sourceFile`: incidental. `self.filename` at `lower.py:215` and `lower.py:275`, which is the path as passed on the command line, absolute in every real invocation (`cli.py:87` passes `str(source_path)`); `str(root)` at `package.py:67`. It varies with the invocation directory and the checkout location. Strip.
- `entry`: canonical. First lowered function name or `null` (`lower.py:276`).
- `functions`: canonical. List of function objects in AST order, deterministic.
- `classes`: canonical content, unstable order. See section 6: synthetic classes are emitted in the iteration order of the `self.synthetics` set (`lower.py:607` feeds `self.classes`, `lower.py:4796` emits), so the list order flips with `PYTHONHASHSEED`. Canonical form must sort `classes` by `name`.
- `fences`: canonical content, order fixed up by sorting (section 2).
- `_notes`: incidental. Only on package merges (`package.py:74`), carries skip messages that name the package root. The CLI pops it before validation (`cli.py:92`), so it is stderr note material, never IR. Strip.

Function object (`lower.py:257`): `name`, `params`, `returnType`, `locals`, `body`, `span`.

- `params`: list of `{name, type}`, plus optional `default` (a const expr node, `lower.py:1049`, `lower.py:2505`; read back at `llvm.py:164`). Canonical including `default`.
- `locals`: list of `{name, type}` in `self.locals` insertion order: params first (`lower.py:247`), then temps in assignment order. Dict iteration is insertion ordered and the insertions follow AST traversal, so the order is stable at fixed input. Canonical. Verified: six runs of `invoice.py` across hash seeds give one locals order.
- `span`: incidental. Strip.

Class object (`lower.py:944`): `name`, `fields`, `methods`, `span`.

- `fields`: list of `{name, type}`, plus optional `default` for dataclasses (`lower.py:684`). Order is `[...inherited, ...own]` in declaration order (`lower.py:746`). Canonical.
- `methods`: function objects without the module prefix (`lower.py:962`). Canonical.
- `span`: incidental. Strip. Synthetic classes get a fabricated `span` at `lower.py:4806` with the same shape, also stripped.

Statement objects. Common keys `kind`, `type` (absent on statements), `span` (strip). Per kind, all canonical:

- `assign`: `target` (str), `value`.
- `if`: `cond`, `then`, `else`.
- `while`: `cond`, `body`.
- `return`: `value`.
- `expr`: `expr`.
- `pass`: nothing.
- `setitem`: `container`, `index`, `value`.
- `setattr`: `object`, `name`, `value`.
- `try`: `body`, `handlers`. Each handler is `{exc: list[str], codes: list[int], body}` (`lower.py:1662`). `exc` is AST order, canonical. `codes` is derived (`EXCEPTIONS` table plus per class `exc_code`, `lower.py:3250`) and deterministic at fixed input, but its order comes from a scan over `self.classes.items()` whose insertion order includes the set driven synthetics, so it has the same theoretical instability as `classes` (section 6). Canonical form sorts `codes`.
- `raise`: `exc`, optional `message` (expr or `null`), optional `code` (int, user exceptions, `lower.py:1642`). Canonical. The backend reads `code` (`llvm.py:449` reads the handler form; the raise form carries it for the runtime call).

Expression objects. Common keys `kind`, `type`, `span` (strip). Per kind, all canonical:

- `name`: `id`.
- `const`: `value`. Always a scalar: ints are stringified (`lower.py:1800`), floats are `repr` (`lower.py:1802`), bytes decode latin1 (`lower.py:1808`), bools and str stay native. Deterministic per interpreter; `repr` of float is stable across CPython 3.x in practice. Canonical.
- `unary`: `op`, `operand`.
- `binary`: `op`, `left`, `right`.
- `compare`: `op`, `left`, `right`.
- `call`: `callee`, `args`, optional `symbol` (backend symbol from the wave tables, `lower.py:2447` and friends; read at `llvm.py:652`). Canonical.
- `callee`: `{kind: "function"|"builtin"|"class", name}` or `{kind: "module", module, name}` or `{kind: "method", receiver, name}` (`ir.py:229`). Canonical.
- `list`: `elems`.
- `dict`: `pairs`, list of `{key, value}` in AST order.
- `subscript`: `value`, `index`.
- `attr`: `value`, `name`.
- `cast`: `op` (always `"int_to_float"`), `value`.
- `contains`: `hay`, `needle`, `negated`.

Type objects (`ir.py:84`): `kind`, plus `elem` for lists, `key`/`value` for dicts, `name` and optional `elem` for classes, optional `set: true` on list kinds used as sets, optional `ordered: true` from `restlower.py:289`. All canonical.

Fence record (`lower.py:83`): `code` (str, always present), `message` (str, always present, may be empty), `function` (str or `null`, `lower.py:76`), `span` (nested `{file, line, col, endLine, endCol}`, `file` is an absolute path). `code`, `message`, `function` canonical; `span` incidental (its `file` varies with invocation, its line numbers are diagnostics).

That is the complete vocabulary. Nothing outside this list appears in any of the three dumps or in any constructor site found by grep.

### Final strip list

1. Every `span` key, recursively: on the module's functions and classes, on every statement and expression node, on fence records.
2. `sourceFile` (module level).
3. `cpython` (module level).
4. `_notes` (module level, package merges only).

Nothing else is stripped. A canonical module is the same dict minus those keys, with `classes` sorted by `name`, each handler's `codes` sorted, and `fences` sorted per section 2.

Stripping spans is codegen safe: `llvm.py` never reads `span` (grep for `span` in that file returns nothing outside comments), and fences block emission entirely (`llvm.py:31`), so fence spans never reach the backend.

## 2. Fence ordering

The plan sorts by `(code, message, function)`. Field checks:

- `code`: always present, str, validated at `ir.py:340`.
- `message`: always present as a key (`ir.py:342`), always a str from `Fence.__init__` or `package.py:53`. Scalar.
- `function`: `str | None` (`lower.py:76`). Scalar but optional in value: module level fences and package fences carry `null` (`lower.py:1079` area, `package.py:55`). A raw `(code, message, function)` tuple sort raises `TypeError` the first time it compares `None` with a str, so the rule as written crashes on almost every real module.

Total order for canonical form: sort by `(code, message, function or "", span.line, span.col)`, then strip the span. The line and col tiebreakers give a true total order over the pre strip records, and `sorted` is stable so even fully tied records (same code, message, function, position) keep their deterministic AST traversal order. After the strip, two fences that differ only by position collapse to identical dicts; that is the intended semantics, since a line move with the same message is diagnostic noise, not a compiler behaviour change. This mirrors the existing precedent in `coverage.py:260`, which already sorts fences by `(file, line, col, code)`.

## 3. Hash choice: SHA-256

`xxhash` is not a dependency: it appears nowhere in `pyproject.toml` or `uv.lock`, and `import xxhash` fails in the project environment. Picking it adds a third party wheel to every CI job that runs `irscore.py`, for a speed win that does not matter: the largest IR in the corpus is a few hundred KB of JSON, and SHA-256 through `hashlib` is microseconds at that size. Measured on ~3.6 KB of canonical JSON: blake2b ~8.7 us, sha256 ~3.0 us (CPython's OpenSSL SHA-256 is hardware accelerated; blake2b's win is software only or on large inputs). SHA-256 is stdlib, is what `cache.py` already uses for `probe_hash` and `compiler_hash` (`cache.py:45`, `cache.py:54`), and keeps one digest everywhere. Pick: SHA-256, `hashlib.sha256` over the canonical JSON serialization (section 5), decided once.

## 4. Cache-key extension sketch

Contradiction with the brief first: the compiler-files component already exists. `cache_key` is at `benchmarks/compat2/cache.py:95` (not line 25), and its third component is exactly the A4 item: `hashes.of(coverage.get(probe.id))`, a hash of the compiler files on the probe's coverage map path, with the file layout hashed in (`FileHashes.layout`, `cache.py:66`) so a renamed or new compiler file invalidates every narrowed key. The fallback for probes with no map entry is the whole tree hash (`cache.py:100`). Nothing needs adding for the A4 design; what `irscore.py` should do is reuse the same primitives.

Sketch for the IR hash world, keeping the miss path unchanged:

```
def cache_key(probe, coverage, hashes: FileHashes | str) -> str:
    ...unchanged components...
    # optional, only if probe results ever embed an IR hash:
    # "\0".join((probe_hash(probe), oracle_version(), chash, IR_HASHES_VERSION))
```

Miss path behaviour: `load` (`cache.py:106`) returns `None` for a missing or unparseable file, so any key change simply misses once and recomputes; `store` (`cache.py:115`) writes the new key atomically. Entries keyed under the old component set are never read again, they are dead files until the cache directory is cleaned, which is a disk cost and not a correctness risk. A hit with the old key cannot serve a stale result because any compiler change changes `chash` (contents plus layout). The one rule to keep: `SCORER_VERSION` or the IR canonicalization version must bump whenever the hashed payload's meaning changes, so a canonicity change costs one full recompute instead of comparing incomparable hashes.

## 5. Serialization for the hash

Hash the canonical module as `json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()`. `sort_keys=True` makes dict key insertion order irrelevant, which is cheap insurance against two structurally equal dicts built in different orders. Lists stay ordered (they are semantic). The hash is over the canonical form only, never the raw dump, because the raw form is not reproducible (section 6). Since schema `canonical-schema/2` (decision 2026-10-16), canonicalization also replaces every substring of an environment absolute path (`sys.executable`, `sys._base_executable`, `sys.exec_prefix`, `sys.prefix`, cwd, the checkout root) in any IR string value with `<PATH>`: the stdlib probes for `sys.prefix` / `sys.exec_prefix` / `sys.executable` lower to constants carrying the machine's venv and checkout paths, and the baseline must reproduce byte-identically from any clone location.

## 6. Nondeterminism failure modes

- Set iteration feeding IR order, `lower.py:607` not `llvm.py:115`. The brief points at `llvm.py` lines 115 and 120, but those lines are clean: `self.needed` is only ever membership tested (`_declarations`, `llvm.py:1640`, `_helpers`, `llvm.py:1778`, both use `in` checks with hardcoded emission order), so LLVM text has no set leak. The real leak is in lowering: `self.synthetics` is a `set[str]` (`lower.py:122`), the loop at `lower.py:607` inserts synthetic classes into `self.classes` in set iteration order, and `_synthetic_classes` (`lower.py:4796`) emits them in dict insertion order into `module["classes"]`. Measured: `examples/add.py` lowers to classes `(TextIO, BytesIO)` under one `PYTHONHASHSEED` and `(BytesIO, TextIO)` under another; `invoice.py` produced three distinct class and method orders across six seeds. Canonicalization kills it iff `classes` is sorted by `name`; without that, `IR_HASHES.json` bakes in one arbitrary order and the A/B gate flakes. The handler `codes` scan at `lower.py:3256` walks the same dict and can inherit the same instability when a caught exception has several user subclasses; sorting `codes` removes it.
- Counter SSA names. Temp locals are `.ps_<hint>_<n>` with `temp_n` reset per function (`lower.py:242`, `lower.py:918`), and LLVM registers are `%t<n>`/`L<n>.<hint>` reset per function (`llvm.py:1533`). Numbering follows AST traversal order, which is deterministic; the only way counters drift is if the set leak above reorders emission, which the class sort removes. Verdict: canonical, keep in the hash, no kill needed. A counter shift after a real compiler change is signal, not noise.
- Dict insertion order across CPython versions. Dicts are insertion ordered by language guarantee on every supported version, so the same code on 3.10 through 3.14 produces the same dict order. The cross version risks are the `cpython` stamp (stripped), span attributes from newer `ast` parsers (spans stripped), and float `repr` inside const values (stable across 3.x in practice). Verdict: no canonicalization needed; the residual float repr risk is accepted, and the baseline is regenerated per interpreter anyway since the stamp moves with it.
- What canonicalization does not kill: nothing else found. The two sorted structures (`classes`, `codes`) plus the fence sort close every run to run divergence observed, and the strip list removes the invocation dependent paths. One process level caveat: `irscore.py` must hash the canonical form and never a raw dump, and any golden dump comparison in review must run both sides under the same `PYTHONHASHSEED` or through canonicalization.

## 7. PROPOSED (2026-11-02, pending owner sign-off): the streaming canonical serializer and the TIR gate

This section is the spec the draft recorded decision `.agents/plans/decisions/2026-11-02-tir-gate-streaming-serializer.md` points at. Nothing in sections 1-6 changes: `canonical.py` is frozen, `IR_HASHES.json` and `irscore.py` keep running, and the new gate is added beside them. Retirement of the old gate is the R5 decision, not this one.

### 7.1 The serializer

`pyscriptc/tirserde.py` (landed on `work/tirgate` as the R2 bootstrap) serializes the canonical payload

    {"schema": CANONICAL_SCHEMA_VERSION, "ir": <canonicalize(module)>}

directly into a `hashlib.sha256` sink: object keys in Python string sort order (exactly what `json.dumps(sort_keys=True)` produces), json `ensure_ascii=False` string escaping (short escapes for `\b\t\n\f\r`, `\uXXXX` lower-case hex for the remaining C0 controls, raw UTF-8 bytes otherwise), `repr` for floats with `allow_nan=False` semantics (a non-finite float reaching IR is a compiler bug and raises), and no dict materialization of the payload at any point.

The contract for the typed-IR migration (R3): per node kind, the serializer emits the node id first, then the schema field order of that kind, then children. Field order in the serializer replaces `sort_keys` as the canonicity mechanism; a field reorder is a schema change and moves every digest through the repin ritual, never silently.

### 7.2 Byte identity with today's canonical bytes

The streaming bytes are byte-identical to the `json.dumps` payload section 5 specifies. This is a checked property, not an assumption: `tirscore --verify-bytes` lowers each probe and `cmp`s the two byte streams per probe. Bootstrap receipt (2026-11-02, Linux, CPython 3.14.9): 13,692 probes, 0 byte mismatches; streaming 4.31s vs dict 2.31s over the corpus (~1.9x wall time, accepted; the dict path retains the payload).

### 7.3 The probe file: probes/TIR_HASHES.json

Format (mirrors `IR_HASHES.json`'s header discipline):

    {"canonical_schema": "canonical-schema/2", "hash_algo": "sha256",
     "oracle": "<oracle_version()>", "tir_schema": "tir/1",
     "probes": {"<probe_id>": "<64-hex>"}}

- `tir_schema` versions the probe file itself; a serializer semantic change bumps it and forces regeneration.
- `canonical_schema` is inherited from section 5; the digests are only valid under the schema version they were generated with.
- The header carries the oracle version once, so a stale baseline is exit 2 (regenerate, do not hand edit), same as `irscore.py`.
- When R4 lands, MIR hashes go in a `mir` section of this file or a sibling file under the same header discipline and the same ritual.

### 7.4 The regen ritual (inherited unchanged)

Exactly the 2026-10-22 ritual (`benchmarks/compat2/irscore.py` and note `.agents/notes/2026-10-22-ir-hashes-regen.md`), with `tirscore` in place of `irscore`: two independent runs A and B (`uv run python -m benchmarks.compat2.tirscore benchmarks/compat2/probes --out A.json`, then B), `cmp A B` must be byte-identical, then one copy lands in `probes/TIR_HASHES.json`. Generated on Linux only (ubuntu-24.04, CPython 3.14.x); never by hand on macOS, because canonical IR folds host constants and the macOS baseline differs by hundreds of hashes. Header mismatch (exit 2) means regenerate, never hand-edit.

### 7.5 The Stage-2 fixed-point lane

`benchmarks/compat2/fixpoint.py` lowers every importable module under `src/pyscriptc` (95 modules today) and hashes each canonical IR, twice, and reports per-module divergence. First state is self-vs-self (seed interpreter, both runs); receipt 2026-11-02: 95 compared, 95 matched, 0 divergent (`benchmarks/compat2/fixpoint.json`). When the self-compiled binary can compile the tree (R6), the same `compare_runs` driver reports seed-vs-self, and the per-module divergence count is the headline self-host receipt trending to zero as R3/R4 land.
