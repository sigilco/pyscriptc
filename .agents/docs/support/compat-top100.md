# Top-100 library compatibility

pinned pure-Python libraries: 3 🟢 · 5 🟡 · 10 🔴

🟢 compiles with no fence · 🟡 at most half of the definitions fence · 🔴 more than half fence. Counts are live `pyscriptc coverage` runs over `benchmarks/compat/top100/src` (pins and hashes in `pins.json`). A library only counts toward the score when one of its own functions runs and matches CPython. Fence codes are per construct, see [supported.md](supported.md). Tiers T0 to T3: [refused-features.md](../spec/refused-features.md). Deliberate differences: [divergences.md](../../../docs/reference/divergences.md).

| library | version | status | fenced defs | top blocking features (fences) | top codes |
| --- | --- | --- | --- | --- | --- |
| `colorama` | 0.4.6 | 🔴 | 60/77 | param-inference (38), return-inference (16), class-body (13) | PS1001, PS2002, PS1204 |
| `h11` | 0.16.0 | 🔴 | 76/133 | generic-alias (17), unlowered-type-name (17), class-keywords (15) | PS2002, PS1103, PS1106 |
| `idna` | 3.20 | 🔴 | 40/56 | name-resolution (55), local-inference (30), keyword-args (17) | PS2002, PS1001, PS2003 |
| `jmespath` | 1.1.0 | 🔴 | 111/200 | param-inference (80), return-inference (18), class-body (9) | PS1001, PS2002, PS1204 |
| `mdurl` | 0.1.2 | 🔴 | 9/12 | local-inference (11), name-resolution (11), construct-other (2) | PS2002, PS1001, PS2003 |
| `packaging` | 26.3 | 🔴 | 283/558 | name-resolution (113), unlowered-type-name (90), class-body (52) | PS2002, PS1106, PS1001 |
| `pathspec` | 1.1.1 | 🔴 | 94/132 | name-resolution (45), local-inference (24), unlowered-type-name (22) | PS2002, PS1001, PS1106 |
| `python-dotenv` | 1.2.4 | 🔴 | 46/75 | unlowered-call (24), name-resolution (21), construct-other (17) | PS2002, PS1001, PS1106 |
| `shellingham` | 1.5.4 | 🔴 | 19/25 | module-value (19), param-inference (13), return-inference (5) | PS2002, PS1001, PS2001 |
| `zipp` | 4.1.1 | 🔴 | 57/75 | param-inference (32), return-inference (18), module-value (3) | PS1001, PS2002, PS2001 |
| `filelock` | 4.1.1 | 🟡 | 243/641 | name-resolution (172), construct-other (82), local-inference (78) | PS2002, PS1001, PS2001 |
| `iniconfig` | 2.3.1 | 🟡 | 14/34 | local-inference (7), name-resolution (7), generic-alias (4) | PS2002, PS1001, PS1103 |
| `platformdirs` | 4.13.0 | 🟡 | 160/388 | name-resolution (87), construct-other (60), local-inference (48) | PS2002, PS1001, PS1102 |
| `pyjwt` | 2.15.1 | 🟡 | 64/171 | name-resolution (24), dynamic-annotation (20), union-types (13) | PS2002, PS1104, PS1101 |
| `pytz` | 2026.5 | 🟡 | 32/102 | param-inference (16), return-inference (2), stdlib:datetime (2) | PS2002, PS1001, PS2001 |
| `certifi` | 2026.7.22 | 🟢 | 0/5 |  |  |
| `trove-classifiers` | 2026.9.21.13 | 🟢 | 0/0 |  |  |
| `tzdata` | 2026.5 | 🟢 | 0/0 |  |  |

Regenerate: `uv run python scripts/gen_support_table.py`.
