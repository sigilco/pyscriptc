# MIR coverage gap map (80-85% flip bar)

Date: 2026-11-06. Branch: `work/mir-gap-map`. Baseline: `uv run python scripts/mir_coverage.py` reports 3/31 functions in slice (10%) over the 20 visible corpus scripts. `scripts/mir_coverage.py --all-reasons` (added in this branch) lists every distinct out-of-slice reason per non-fenced function instead of only the first; the data below is its `--json` output (`out_of_slice_all` per script row, `reasons_all` histogram). Reproduce with `uv run python scripts/mir_coverage.py --all-reasons --json`.

Reason vocabulary: `param:`/`local:`/`return:`/`expr:name`/`expr:const`/`expr:call arg:` carry a type kind; `expr:binary OP TYPE`, `expr:compare OP L/R`, `expr:unary OP TYPE` carry operator and operand kinds; `expr:call KIND:NAME` names the callee shape (builtin, module, method, class, value); `stmt:KIND` is an out-of-slice statement kind; `callee:NAME` means the function is structurally in the slice but calls same-module `NAME`, which is not; `callee:NAME (recursive)`, `(arity A/B)` and `(not a module function)` never clear by fixing `NAME`'s own slice reasons.

## Per-function blocker sets


| script | function | in-slice | blockers |
|---|---|---|---|
| bigint_factorial | fact | yes |  |
| bigint_factorial | main | no | expr:call arg:str<br>expr:call builtin:len<br>expr:call builtin:print<br>expr:call builtin:str |
| binary_trees | bottom_up | no | callee:bottom_up (recursive)<br>expr:call arg:class<br>expr:call class:Tree<br>expr:call class:opt_Tree<br>expr:name class<br>local:class<br>return:class<br>stmt:setattr |
| binary_trees | main | no | callee:bottom_up<br>callee:check (not a module function)<br>expr:call arg:class<br>expr:call builtin:print |
| bit_tricks | main | no | callee:popcount<br>expr:binary & int<br>expr:binary << int<br>expr:binary >> int<br>expr:binary ^ int<br>expr:binary | int<br>expr:call arg:list<br>expr:call arg:str<br>expr:call builtin:print<br>expr:call module:builtins.bin<br>expr:call module:builtins.hex<br>expr:call module:builtins.int<br>expr:call module:builtins.list.append<br>expr:call module:builtins.oct<br>expr:call module:builtins.sum<br>expr:list<br>expr:name list<br>expr:unary ~ int<br>local:list |
| bit_tricks | popcount | no | expr:binary & int |
| caesar_cipher | main | no | callee:shift (not a module function)<br>expr:call arg:str<br>expr:call builtin:print |
| collatz | main | no | expr:call builtin:print |
| collatz | steps | yes |  |
| exceptions_flow | divide | no | expr:call builtin:str<br>stmt:try |
| exceptions_flow | parse | no | expr:call arg:str<br>expr:call module:builtins.int<br>stmt:try |
| fib_recursive | fib | no | callee:fib (recursive) |
| fib_recursive | main | no | callee:fib<br>expr:call arg:list<br>expr:call builtin:len<br>expr:call builtin:print<br>expr:list<br>expr:name list<br>expr:subscript<br>local:list |
| gcd_lcm | gcd | no | expr:attr<br>expr:call class:tuple_2_int_int<br>expr:name class<br>local:class<br>stmt:setattr |
| gcd_lcm | main | no | callee:gcd<br>expr:call builtin:print |
| mandelbrot | __entry__ | no | callee:main |
| mandelbrot | main | no | expr:binary + str<br>expr:call arg:str<br>expr:call builtin:print<br>expr:cast |
| mandelbrot | point | yes |  |
| matmul | main | no | callee:matmul<br>expr:call arg:list<br>expr:call builtin:len<br>expr:call builtin:print<br>expr:call module:builtins.list.append<br>expr:call module:builtins.sum<br>expr:list<br>expr:name list<br>expr:subscript<br>local:list |
| matmul | matmul | no | expr:call arg:list<br>expr:call builtin:len<br>expr:call module:builtins.list.append<br>expr:call module:builtins.repeat<br>expr:list<br>expr:name list<br>expr:subscript<br>local:list<br>param:list<br>return:list<br>stmt:setitem |
| nbody | __entry__ | no | callee:main |
| nbody | energy | no | expr:call arg:list<br>expr:call builtin:len<br>expr:call module:math.sqrt<br>expr:name list<br>expr:subscript<br>local:list<br>param:list |
| nbody | main | no | callee:advance (not a module function)<br>callee:energy<br>expr:call arg:list<br>expr:call arg:str<br>expr:call builtin:print<br>expr:call module:builtins.format<br>expr:list<br>expr:name list<br>local:list |
| pi_digits | main | no | callee:pi_digits<br>expr:call arg:str<br>expr:call builtin:print |
| pi_digits | pi_digits | no | expr:attr<br>expr:call arg:list<br>expr:call arg:str<br>expr:call builtin:len<br>expr:call builtin:str<br>expr:call class:tuple_3_int_int_int<br>expr:call class:tuple_6_int_int_int_int_int_int<br>expr:call method:str.join<br>expr:call module:builtins.list.append<br>expr:list<br>expr:name class<br>expr:name list<br>local:class<br>local:list<br>stmt:setattr |
| sieve | main | no | callee:sieve (not a module function)<br>expr:call arg:list<br>expr:call builtin:len<br>expr:call builtin:print<br>expr:call module:builtins.sum<br>expr:name list<br>expr:subscript<br>local:list |
| spectral_norm | __entry__ | no | callee:main |
| spectral_norm | eval_a | no | expr:cast |
| spectral_norm | main | no | callee:times<br>expr:call arg:list<br>expr:call arg:str<br>expr:call builtin:print<br>expr:call module:builtins.format<br>expr:call module:builtins.repeat<br>expr:call module:math.sqrt<br>expr:list<br>expr:name list<br>expr:subscript<br>local:list |
| spectral_norm | times | no | callee:eval_a<br>expr:call arg:list<br>expr:call builtin:len<br>expr:call module:builtins.repeat<br>expr:list<br>expr:name list<br>expr:subscript<br>local:list<br>param:list<br>return:list<br>stmt:setitem |
| word_freq | main | no | expr:call arg:dict<br>expr:call arg:list<br>expr:call arg:str<br>expr:call builtin:len<br>expr:call builtin:print<br>expr:call method:str.split<br>expr:call module:builtins.dict.keys<br>expr:call module:builtins.list.append<br>expr:compare < str/str<br>expr:contains<br>expr:dict<br>expr:list<br>expr:name dict<br>expr:name list<br>expr:subscript<br>local:dict<br>local:list<br>stmt:setitem |


## Reason -> functions needing it

- `callee:advance (not a module function)` (1): nbody.main
- `callee:bottom_up` (1): binary_trees.main
- `callee:bottom_up (recursive)` (1): binary_trees.bottom_up
- `callee:check (not a module function)` (1): binary_trees.main
- `callee:energy` (1): nbody.main
- `callee:eval_a` (1): spectral_norm.times
- `callee:fib` (1): fib_recursive.main
- `callee:fib (recursive)` (1): fib_recursive.fib
- `callee:gcd` (1): gcd_lcm.main
- `callee:main` (3): mandelbrot.__entry__, nbody.__entry__, spectral_norm.__entry__
- `callee:matmul` (1): matmul.main
- `callee:pi_digits` (1): pi_digits.main
- `callee:popcount` (1): bit_tricks.main
- `callee:shift (not a module function)` (1): caesar_cipher.main
- `callee:sieve (not a module function)` (1): sieve.main
- `callee:times` (1): spectral_norm.main
- `expr:attr` (2): gcd_lcm.gcd, pi_digits.pi_digits
- `expr:binary & int` (2): bit_tricks.main, bit_tricks.popcount
- `expr:binary + str` (1): mandelbrot.main
- `expr:binary << int` (1): bit_tricks.main
- `expr:binary >> int` (1): bit_tricks.main
- `expr:binary ^ int` (1): bit_tricks.main
- `expr:binary | int` (1): bit_tricks.main
- `expr:call arg:class` (2): binary_trees.bottom_up, binary_trees.main
- `expr:call arg:dict` (1): word_freq.main
- `expr:call arg:list` (11): bit_tricks.main, fib_recursive.main, matmul.main, matmul.matmul, nbody.energy, nbody.main, pi_digits.pi_digits, sieve.main, spectral_norm.main, spectral_norm.times, word_freq.main
- `expr:call arg:str` (10): bigint_factorial.main, bit_tricks.main, caesar_cipher.main, exceptions_flow.parse, mandelbrot.main, nbody.main, pi_digits.main, pi_digits.pi_digits, spectral_norm.main, word_freq.main
- `expr:call builtin:len` (9): bigint_factorial.main, fib_recursive.main, matmul.main, matmul.matmul, nbody.energy, pi_digits.pi_digits, sieve.main, spectral_norm.times, word_freq.main
- `expr:call builtin:print` (14): bigint_factorial.main, binary_trees.main, bit_tricks.main, caesar_cipher.main, collatz.main, fib_recursive.main, gcd_lcm.main, mandelbrot.main, matmul.main, nbody.main, pi_digits.main, sieve.main, spectral_norm.main, word_freq.main
- `expr:call builtin:str` (3): bigint_factorial.main, exceptions_flow.divide, pi_digits.pi_digits
- `expr:call class:Tree` (1): binary_trees.bottom_up
- `expr:call class:opt_Tree` (1): binary_trees.bottom_up
- `expr:call class:tuple_2_int_int` (1): gcd_lcm.gcd
- `expr:call class:tuple_3_int_int_int` (1): pi_digits.pi_digits
- `expr:call class:tuple_6_int_int_int_int_int_int` (1): pi_digits.pi_digits
- `expr:call method:str.join` (1): pi_digits.pi_digits
- `expr:call method:str.split` (1): word_freq.main
- `expr:call module:builtins.bin` (1): bit_tricks.main
- `expr:call module:builtins.dict.keys` (1): word_freq.main
- `expr:call module:builtins.format` (2): nbody.main, spectral_norm.main
- `expr:call module:builtins.hex` (1): bit_tricks.main
- `expr:call module:builtins.int` (2): bit_tricks.main, exceptions_flow.parse
- `expr:call module:builtins.list.append` (5): bit_tricks.main, matmul.main, matmul.matmul, pi_digits.pi_digits, word_freq.main
- `expr:call module:builtins.oct` (1): bit_tricks.main
- `expr:call module:builtins.repeat` (3): matmul.matmul, spectral_norm.main, spectral_norm.times
- `expr:call module:builtins.sum` (3): bit_tricks.main, matmul.main, sieve.main
- `expr:call module:math.sqrt` (2): nbody.energy, spectral_norm.main
- `expr:cast` (2): mandelbrot.main, spectral_norm.eval_a
- `expr:compare < str/str` (1): word_freq.main
- `expr:contains` (1): word_freq.main
- `expr:dict` (1): word_freq.main
- `expr:list` (9): bit_tricks.main, fib_recursive.main, matmul.main, matmul.matmul, nbody.main, pi_digits.pi_digits, spectral_norm.main, spectral_norm.times, word_freq.main
- `expr:name class` (3): binary_trees.bottom_up, gcd_lcm.gcd, pi_digits.pi_digits
- `expr:name dict` (1): word_freq.main
- `expr:name list` (11): bit_tricks.main, fib_recursive.main, matmul.main, matmul.matmul, nbody.energy, nbody.main, pi_digits.pi_digits, sieve.main, spectral_norm.main, spectral_norm.times, word_freq.main
- `expr:subscript` (8): fib_recursive.main, matmul.main, matmul.matmul, nbody.energy, sieve.main, spectral_norm.main, spectral_norm.times, word_freq.main
- `expr:unary ~ int` (1): bit_tricks.main
- `local:class` (3): binary_trees.bottom_up, gcd_lcm.gcd, pi_digits.pi_digits
- `local:dict` (1): word_freq.main
- `local:list` (11): bit_tricks.main, fib_recursive.main, matmul.main, matmul.matmul, nbody.energy, nbody.main, pi_digits.pi_digits, sieve.main, spectral_norm.main, spectral_norm.times, word_freq.main
- `param:list` (3): matmul.matmul, nbody.energy, spectral_norm.times
- `return:class` (1): binary_trees.bottom_up
- `return:list` (2): matmul.matmul, spectral_norm.times
- `stmt:setattr` (3): binary_trees.bottom_up, gcd_lcm.gcd, pi_digits.pi_digits
- `stmt:setitem` (3): matmul.matmul, spectral_norm.times, word_freq.main
- `stmt:try` (2): exceptions_flow.divide, exceptions_flow.parse

## Greedy cumulative ordering

Model: a function becomes in-slice when every reason in its blocker set is fixed, except `callee:NAME` edges, which clear when `NAME` itself joins the slice (dependencies expanded transitively). `callee:* (recursive|arity|not a module function)` reasons are treated as unfixable by slice work. At each step the greedy pick is the reason that unlocks the most functions; when no single reason unlocks anything, the pick maximizes progress on the functions closest to done (sum of 1/remaining-blockers). Rows show the cumulative in-slice count after that reason is fixed.

| # | reason fixed | functions newly in-slice | cumulative | % of corpus |
|---|---|---|---|---|
| 1 | `expr:cast` | eval_a | 4/31 | 12.9% |
| 2 | `expr:call builtin:print` | main | 5/31 | 16.1% |
| 3 | `expr:binary & int` | popcount | 6/31 | 19.4% |
| 4 | `expr:call arg:str` | - | 6/31 | 19.4% |
| 5 | `expr:binary + str` | __entry__, main | 8/31 | 25.8% |
| 6 | `expr:call builtin:len` | - | 8/31 | 25.8% |
| 7 | `expr:call builtin:str` | main | 9/31 | 29.0% |
| 8 | `stmt:try` | divide | 10/31 | 32.3% |
| 9 | `expr:call module:builtins.int` | parse | 11/31 | 35.5% |
| 10 | `local:list` | - | 11/31 | 35.5% |
| 11 | `expr:name list` | - | 11/31 | 35.5% |
| 12 | `expr:call arg:list` | - | 11/31 | 35.5% |
| 13 | `expr:subscript` | - | 11/31 | 35.5% |
| 14 | `param:list` | - | 11/31 | 35.5% |
| 15 | `expr:call module:math.sqrt` | energy | 12/31 | 38.7% |
| 16 | `expr:list` | - | 12/31 | 38.7% |
| 17 | `stmt:setitem` | - | 12/31 | 38.7% |
| 18 | `return:list` | - | 12/31 | 38.7% |
| 19 | `expr:call module:builtins.repeat` | times | 13/31 | 41.9% |
| 20 | `expr:call module:builtins.format` | __entry__, main | 15/31 | 48.4% |
| 21 | `expr:call module:builtins.list.append` | matmul | 16/31 | 51.6% |
| 22 | `expr:call module:builtins.sum` | main | 17/31 | 54.8% |
| 23 | `stmt:setattr` | - | 17/31 | 54.8% |
| 24 | `local:class` | - | 17/31 | 54.8% |
| 25 | `expr:name class` | - | 17/31 | 54.8% |
| 26 | `expr:attr` | - | 17/31 | 54.8% |
| 27 | `expr:call class:tuple_2_int_int` | gcd, main | 19/31 | 61.3% |
| 28 | `expr:call method:str.join` | - | 19/31 | 61.3% |
| 29 | `expr:call class:tuple_6_int_int_int_int_int_int` | - | 19/31 | 61.3% |
| 30 | `expr:call class:tuple_3_int_int_int` | main, pi_digits | 21/31 | 67.7% |
| 31 | `local:dict` | - | 21/31 | 67.7% |
| 32 | `expr:name dict` | - | 21/31 | 67.7% |
| 33 | `expr:dict` | - | 21/31 | 67.7% |
| 34 | `expr:contains` | - | 21/31 | 67.7% |
| 35 | `expr:compare < str/str` | - | 21/31 | 67.7% |
| 36 | `expr:call module:builtins.dict.keys` | - | 21/31 | 67.7% |
| 37 | `expr:call method:str.split` | - | 21/31 | 67.7% |
| 38 | `expr:call arg:dict` | main | 22/31 | 71.0% |
| 39 | `expr:unary ~ int` | - | 22/31 | 71.0% |
| 40 | `expr:call module:builtins.oct` | - | 22/31 | 71.0% |
| 41 | `expr:call module:builtins.hex` | - | 22/31 | 71.0% |
| 42 | `expr:call module:builtins.bin` | - | 22/31 | 71.0% |
| 43 | `expr:binary | int` | - | 22/31 | 71.0% |
| 44 | `expr:binary ^ int` | - | 22/31 | 71.0% |
| 45 | `expr:binary >> int` | - | 22/31 | 71.0% |
| 46 | `expr:binary << int` | main | 23/31 | 74.2% |

The ordering reaches 23/31 (74.2%) after fixing all 46 slice-level reasons. It never reaches 80% (25/31) or 85% (27/31) on slice work alone: 8 functions stay blocked by non-slice causes (see below).

## Residual blockers outside the slice predicates

Eight functions cannot join the slice no matter how the `supported_*` tables grow:

- `fib_recursive.fib` and `fib_recursive.main`: `fib` is self-recursive; `supported_module_fn` rejects recursion cycles, and `main` depends on `fib`. Recursive-call support is a slice-semantics change, not a table entry.
- `binary_trees.bottom_up`: self-recursive plus `class:Tree`/`class:opt_Tree` constructor calls, `local:class`, `return:class`, `stmt:setattr`, `expr:call arg:class`, `expr:name class`. `binary_trees.main` additionally needs `check`, which is fenced in the direct lowerer (`PS2001 Tree.left is not a field`), so `callee:check (not a module function)` is really "unfence `check` first".
- `caesar_cipher.main`: calls `shift`, fenced in the direct lowerer (`PS1001`/`PS2002` annotation and local errors). Its own slice reasons (`expr:call arg:str`, `expr:call builtin:print`) are fixed by step 46.
- `sieve.main`: calls `sieve`, fenced in the direct lowerer (`PS1001`/`PS2002` on `flags`). Its own slice reasons are all fixed by step 46.
- `nbody.main` and `nbody.__entry__`: `main` calls `advance`, fenced in the direct lowerer (`PS2002` augmented assignment on a non-name target, 9 sites). `main`'s own reasons are fixed by step 46 and `energy` joins at step 15, so unfencing `advance` yields both `main` and `__entry__`.

Paths to the bar, in increasing scope:

- 80% (25/31): all slice reasons through step 46 plus self-recursion support for `fib`, which adds `fib` and `fib_recursive.main`.
- 85% (27/31): two routes. (a) Recursion plus unfence `shift` (caesar_cipher.main) and `sieve` (sieve.main): 23 + 2 + 1 + 1 = 27. (b) No recursion: unfence `advance` (adds both `nbody.main` and `nbody.__entry__`), `shift` and `sieve`: 23 + 2 + 1 + 1 = 27. Each unfence is a direct-lowerer fix (PS1001/PS2002), not MIR work, and should be filed as its own issue.
- Ceiling without `binary_trees`: 29/31 (93.5%) with recursion and all three unfences. `binary_trees` is genuinely last: `bottom_up` needs recursion plus the class/tuple slice (`class:Tree`, `class:opt_Tree`, `expr:call arg:class` are not covered by steps 1-46) and `main` additionally needs the `check` unfence.

## Minimum value types and statement kinds

Value types the corpus needs beyond the current int/float/bool/str slice:

- `list`: the dominant gap (11 functions carry `local:list`/`expr:name list`/`expr:call arg:list`/`expr:list`/`expr:subscript`). Needs the type in params, locals, returns, call args and name reads, plus `expr:list` construction, `expr:subscript` reads, and the `len`, `list.append`, `sum`, `repeat`, `int`, `format` builtin/module callees that consume it.
- `str` through calls: `expr:call arg:str` (10 functions) plus `builtin:print`/`builtin:str`/`builtin:len`, `module:builtins.int/format/bin/hex/oct`, `method:str.split`/`str.join`, `expr:binary + str`, `expr:compare < str/str` and `expr:contains`. The `MIR_S` str tag already exists; this is admitting str in more positions, not a new type.
- `dict`: `local:dict`, `expr:dict`, `expr:name dict`, `expr:call arg:dict`, `module:builtins.dict.keys`, sharing `stmt:setitem` with the list work.
- `class` (tuples are `class:tuple_N_*` constructor calls): `local:class`, `return:class`, `expr:name class`, `expr:call arg:class`, `expr:call class:*`, `expr:attr`, `stmt:setattr`. Needed by `gcd_lcm`, `pi_digits`, `binary_trees`.
- No corpus function needs `bytes`, `set`, `func`, `exc`, `unknown` or `any` as a slice value type.

Statement kinds: `stmt:setitem` (3), `stmt:setattr` (3), `stmt:try` (2). `for`/`with`/augmented assignment already desugar before the IR; `raise` appears only inside `try` handlers here, and the current `ValueError`-only raise rule is not a blocker for this corpus.

Expression kinds beyond the slice: `expr:list`, `expr:dict`, `expr:subscript`, `expr:attr`, `expr:cast`, `expr:contains`. Call callee shapes needed: `builtin` (print, len, str), `module` (builtins.*, math.sqrt), `method` (str.split, str.join), `class` (tuple constructors, Tree). Remaining operator gaps: int bitwise `& | ^ << >>`, unary `~`, str `+` and str ordering compares.

## Proposed work split

Four parallel work items. Caveat: every item widens `supported_*` in `src/pyscriptc/mirlower.py`, so strict file-disjointness is impossible; the split below is by predicate/emit region and each item should land as its own PR touching different functions and tables. `src/pyscriptc/mir.py` (op codes) and `src/pyscriptc/miremit.py` (emission) are shared second files; runtime helper additions go to `src/pyscriptc/runtime/`.

1. **List and sequence builtins** (effort L). Scope: `list` as a value type in `supported_fn`/`supported_expr`/`_supported_call`, `expr:list`, `expr:subscript`, name reads of list type, and callee admission for `builtin:len`, `module:builtins.list.append`, `module:builtins.sum`, `module:builtins.repeat`, `module:builtins.int`, `module:builtins.format`, `module:math.sqrt`. Primary files: `mirlower.py` (type tables, `_supported_call`, new expr kinds), `mir.py` (list/subscript op ids if new ops are needed), `miremit.py`, runtime list helpers, `tests/`. Unblocks the list half of 11 functions; alone reaches roughly the step-22 plateau (17/31) once callees cascade.
2. **Str-through-calls and str builtins** (effort L). Scope: `str` call args (`expr:call arg:str`), `builtin:print`/`str`/`len`, `module:builtins.int/format/bin/hex/oct`, `method:str.split`/`str.join`, str `+` concat, str ordering compares, `expr:contains` on str. Primary files: `mirlower.py` (`_supported_call` arg kinds, `_supported_compare`/`_supported_binary` str rows), `miremit.py` str emit paths, `ps_str` runtime helpers, `tests/`. With item 1 this covers the step-46 plateau (23/31, 74.2%).
3. **Class/tuple values, attr and container statements** (effort XL). Scope: `class` value type end-to-end, `class:tuple_N_*` constructors, `expr:attr`, `expr:name class`, `stmt:setattr`, `stmt:setitem`, `stmt:try`. Primary files: `mirlower.py` (class type admission, new stmt kinds), `mir.py`/`miremit.py` (attr/setitem/try ops), runtime support for tuples/classes or a documented boxed fallback, `tests/`. Needed for `gcd_lcm`, `pi_digits`, `binary_trees`, `word_freq` tails; highest risk item.
4. **Dict values, remaining int ops, cast, recursion decision** (effort M). Scope: `dict` value type, `expr:dict`, `expr:name dict`, `module:builtins.dict.keys`, int bitwise `& | ^ << >>`, unary `~`, `expr:cast`, plus the recursion-policy decision (admit self-calls in `supported_module_fn` or document the exclusion) and coordination with direct-lowerer unfences (`shift`, `sieve`, `advance`, `check`). Primary files: `mirlower.py` (op tables, `_calls_in_slice` recursion rule), `mir.py`/`miremit.py` (bitwise/cast ops), `tests/`, plus `.agents/notes/` record of the fence-lift asks. Items 1 and 2 are the critical path to 74%; item 4's recursion half is the cheapest route past 80%.

Ordering suggestion: land 1 and 2 first (they share the `_supported_call` arg-type table, so sequence them or pair them), then 4's dict/op tail, then 3 last since it is the largest and least certain. The 80% bar needs item 4's recursion decision; the 85% bar additionally needs the direct-lowerer unfences, which are outside MIR scope and should be filed as their own issues.
