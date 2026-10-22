# E1 entry-point detector: results

Implements `docs/e1-entrypoint-detector-spec.md` as `benchmarks/compat2/entrypoints.py`, runnable as `python -m benchmarks.compat2.entrypoints`. Replaces the spec's throwaway scan (`docs/e1-entrypoint-scan.py`, deleted in the same change).

Repro:

```
uv run python -m benchmarks.compat2.entrypoints --out /tmp/entrypoints.json
```

`--out` writes the full record list (one spec record per scanned file, sorted by path, sorted keys, trailing newline, byte-identical across runs). stdout prints the summary table. `--no-fences` skips the compiler validation pass.

## Counts (master `343cad5`, 2026-10-16)

Reproduces the spec tables exactly.

| Directory | Files | Scripts | Modules |
| --- | --- | --- | --- |
| `examples/` | 26 | 3 | 23 |
| `benchmarks/compat2/corpus/scripts/*/main.py` | 20 | 20 | 0 |

58 relevant statements across the 20 scripts, 25 of them `print` calls. Statement-kind tally matches the spec, with one correction: the spec row "If (all non-guard): 3" in `examples/` is really the three `__main__` guards themselves (their bodies are inlined as `Expr`); there are no non-guard top-level `If`s in either directory.

## Validation against the current compiler

The spec was written before E1's entry lowering landed (`3958cbf` module-level entry, `464e849` print). On current master the validation changes:

- The three example scripts (`floor_div.py`, `longer.py`, `sum_squares.py`) now compile with zero fences. They already flipped.
- 18 of 20 corpus scripts still fail, but their earliest fence sits inside a `def` body (PS1001 annotations, PS1101 unions, PS1104 dynamic types, ...). Entry lowering alone cannot flip them; they need the later gates.
- The tool marks `predicted_flip` only when a file is a script AND its earliest fence falls inside a top-level entry statement - the literal "fences at the first non-definition statement" the brief asks for.

Predicted-flip list today:

| Path | First fence | Note |
| --- | --- | --- |
| `benchmarks/compat2/corpus/scripts/fib_recursive/main.py` | PS2002 line 7 `main` | top-level `for` over `fib(...)` call result is not lowerable |
| `benchmarks/compat2/corpus/scripts/word_freq/main.py` | PS2001 line 4 `main` | `counts.get(word, 0)` inside the top-level `for` body |

If E1's flip is judged as "file that entry work alone un-fences", this is the list. If the epic is judged against the full E1-relevant set, filter `--out` on `is_script` (23 records). Each record also carries `fences_today`, `fences_at_entry` (entry-statement lines that fence) and `first_fence` so either reading is re-derivable without recompiling.

## Per-script fence map

Earliest fence per corpus script, and whether it sits inside an entry statement (entry spans include guard bodies and statement end lines).

| Script | entry stmts | prints | first fence | at entry |
| --- | --- | --- | --- | --- |
| bigint_factorial | 4 | 3 | PS1001 line 2 | no |
| binary_trees | 1 | 0 | PS1101 line 2 | no |
| bit_tricks | 3 | 3 | PS1001 line 2 | no |
| caesar_cipher | 4 | 2 | PS1001 line 2 | no |
| collatz | 4 | 1 | PS1001 line 2 | no |
| exceptions_flow | 3 | 2 | PS2003 line 3 | no |
| fib_recursive | 1 | 0 | PS2002 line 7 | yes |
| gcd_lcm | 4 | 2 | PS2002 line 3 | no |
| generators_comp | 5 | 5 | PS1001 line 1 | no |
| json_like_dump | 1 | 1 | PS1104 line 1 | no |
| linked_list | 6 | 1 | PS2001 line 3 | no |
| mandelbrot | 1 | 0 | PS1001 line 2 | no |
| matmul | 5 | 1 | PS1001 line 2 | no |
| nbody | 1 | 0 | PS1001 line 7 | no |
| nqueens | 1 | 0 | PS2002 line 1 | no |
| pi_digits | 1 | 1 | PS2002 line 2 | no |
| quicksort | 6 | 2 | PS1001 line 3 | no |
| sieve | 2 | 1 | PS1001 line 2 | no |
| spectral_norm | 1 | 0 | PS2004 line 5 | no |
| word_freq | 4 | 0 | PS2001 line 4 | yes |
