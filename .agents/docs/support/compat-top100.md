# Top-100 library compatibility

pinned pure-Python libraries: 2 🟢 · 6 🟡 · 10 🔴

🟢 compiles with no fence · 🟡 at most half of the definitions fence · 🔴 more than half fence. Counts are live `pyscriptc coverage` runs over `benchmarks/compat/top100/src` (pins and hashes in `pins.json`). A library only counts toward the score when one of its own functions runs and matches CPython. Fence codes are per construct, see [supported.md](supported.md). Tiers T0 to T3: [refused-features.md](../spec/refused-features.md). Deliberate differences: [divergences.md](../../../docs/reference/divergences.md).

| library | version | status | fenced defs | top blocking features (fences) | top codes |
| --- | --- | --- | --- | --- | --- |
| `colorama` | 0.4.6 | 🔴 | 48/77 | class-body (51), param-inference (29), construct-other (13) | PS1204, PS2002, PS1001 |
| `h11` | 0.16.0 | 🔴 | 76/133 | generic-alias (17), unlowered-type-name (17), reflection (16) | PS2002, PS1103, PS1106 |
| `idna` | 3.20 | 🔴 | 37/56 | keyword-args (16), construct-other (10), call-arity (8) | PS2002, PS2003, PS1101 |
| `jmespath` | 1.1.0 | 🔴 | 107/200 | param-inference (78), return-inference (15), class-body (9) | PS1001, PS2002, PS1204 |
| `mdurl` | 0.1.2 | 🔴 | 8/12 | name-resolution (6), construct-other (3), unlowered-call (3) | PS2002, PS2003, PS2001 |
| `packaging` | 26.3 | 🔴 | 281/558 | unlowered-type-name (90), name-resolution (84), class-body (52) | PS2002, PS1106, PS2001 |
| `pathspec` | 1.1.1 | 🔴 | 93/132 | name-resolution (27), unlowered-type-name (22), decorators (21) | PS2002, PS1106, PS1201 |
| `python-dotenv` | 1.2.4 | 🔴 | 43/75 | unlowered-call (25), construct-other (21), name-resolution (17) | PS2002, PS1106, PS1001 |
| `shellingham` | 1.5.4 | 🔴 | 19/25 | module-value (19), param-inference (12), return-inference (6) | PS2002, PS1001, PS2001 |
| `zipp` | 4.1.1 | 🔴 | 57/75 | param-inference (32), return-inference (18), module-value (3) | PS1001, PS2002, PS2001 |
| `filelock` | 4.1.1 | 🟡 | 240/641 | name-resolution (147), construct-other (96), generic-alias (57) | PS2002, PS2001, PS1103 |
| `iniconfig` | 2.3.1 | 🟡 | 13/34 | type-mismatch (5), generic-alias (4), union-types (4) | PS2002, PS1003, PS1103 |
| `platformdirs` | 4.13.0 | 🟡 | 154/388 | construct-other (62), name-resolution (51), optional-types (33) | PS2002, PS1102, PS2001 |
| `pyjwt` | 2.15.1 | 🟡 | 65/171 | name-resolution (21), dynamic-annotation (20), union-types (13) | PS2002, PS1104, PS1101 |
| `pytz` | 2026.5 | 🟡 | 32/102 | param-inference (16), return-inference (2), stdlib:datetime (2) | PS2002, PS1001, PS2001 |
| `trove-classifiers` | 2026.9.21.13 | 🟡 | 0/0 | module-assign (1) | PS2002 |
| `certifi` | 2026.7.22 | 🟢 | 0/5 |  |  |
| `tzdata` | 2026.5 | 🟢 | 0/0 |  |  |

Regenerate: `mise run support:table`.
