# E1 entry-point detector spec

How to tell a script from a module by AST, and what a detector records so E1 can predict which corpus files flip once module-level statements lower into an entry function. Spec only: the permanent detector is built inside E1. The counts below come from a throwaway scan (`docs/e1-entrypoint-scan.py`, marked as throwaway, delete freely) run over `examples/` and the scripts corpus.

Path note: the E1 plan names `benchmarks/compat2/corpus/scripts/src/`. That directory does not exist. The scripts corpus is `benchmarks/compat2/corpus/scripts/*/main.py` (20 directories, category `program`, weight 3.0 per `benchmarks/compat2/outcomes.py:48`). This spec scans those 20 `main.py` files. `corpus/src/` is vendored third-party packages (top100 trees), not scripts, and is out of scope.

## How a file enters the compiler today

There is no script-vs-module distinction in the pipeline. `pyscriptc compile` (`src/pyscriptc/cli.py:82`) dispatches on path kind: a directory goes to `lower_directory` (`src/pyscriptc/package.py:24`), a file to `lower_file`. Both end in `Lowerer.module` (`src/pyscriptc/lower.py:155`), which walks module-level statements and keeps only four groups:

- `Import` / `ImportFrom`: bound for names, not lowered.
- `FunctionDef` / `ClassDef`: lowered as definitions.
- `Assign` / `AnnAssign`: bound as module constants.
- `If` matching `__name__ == "__main__"` (`_is_main_guard`, `src/pyscriptc/lower.py:5303`) and a leading string `Expr` (`_is_docstring`): skipped silently.

Everything else appends a `PS2002` fence: `{type} at module scope is not lowered`. The corpus README confirms the result: none of the 25 scripts compiles; the first module-level `for` or call statement fences. So today's "script" is a file whose every non-definition, non-import, non-guard top-level statement is absent. The detector predicts which files stop being fenced once E1 lowers them.

## Scan definition

Given a parsed file (`ast.parse`, stdlib `ast` only), walk `tree.body` once and inventory every statement by kind. For each statement record the AST class name (`Expr`, `Assign`, `AnnAssign`, `AugAssign`, `If`, `For`, `While`, `FunctionDef`, `ClassDef`, `Import`, `ImportFrom`, `Try`, `With`, `Pass`, ...), its line number, and where it was found (`top` or `guard`).

One level deep inside guards only. A statement of the form

```python
if __name__ == "__main__":
    <body>
```

recognized structurally (left is the name `__name__`, single `Eq`, single constant comparator `"__main__"`, same rule as `_is_main_guard`) has its body inventoried one level deep and tagged `where: "guard"`. Statements nested deeper (inside the guard's own `if`/`for`, inside function or class bodies) are not inventoried. An `If` whose test is not a main guard stays one `If` record; its body is not flattened.

Docstring rule. A leading string constant `Expr` at position 0 of the module body is recorded as kind `Docstring`, not `Expr`. A string expression anywhere else is an ordinary `Expr`.

Inert kinds. `Import`, `ImportFrom`, `FunctionDef`, `AsyncFunctionDef`, `ClassDef`, `Docstring`, and `Pass` never make a file entry-relevant on their own. They still appear in `statement_kinds` so the counts stay auditable.

## Per-kind fields

Minimal, enough to feed a predicted-flip count, not a full indexer:

| Kind | Extra fields |
| --- | --- |
| `Assign`, `AnnAssign`, `AugAssign` | `targets`: names bound (plain names, tuple elts flattened, dotted names for attribute/subscript targets) |
| `Expr` | `call`: dotted name of `value` when the expression is a call (`print`, `main`, `os.getenv`) |
| `For`, `AsyncFor`, `With`, `AsyncWith` | `targets`: loop or with bindings |
| `FunctionDef`, `AsyncFunctionDef`, `ClassDef` | `decorators`: dotted names; `bases` for classes |
| `If` | `main_guard`: bool |
| everything | `kind`, `line`, `where` |

Anything the field list does not name (comparators, argument expressions, comprehension structure) is out of scope. One worked example in the record shape section shows the mapping.

## Classification rule

A file is a **script** (E1-relevant) when its inventory holds at least one statement that is not inert and not a non-guard `If` (the `If` itself is inert; the statements inside a guard are what count). Otherwise it is a **module**: a file the compiler already handles as definitions and constants.

Rationale: a non-guard top-level `If` is a compound statement. Flattening it would double-count its body, and E1 lowers statements, not control flow at module scope, so the body statements are what predict the flip. If E1 later decides compound statements at module scope stay fenced, the classification rule changes in one place: count the `If` itself.

## Predicted-flip record

One JSON object per E1-relevant file:

```json
{
  "path": "benchmarks/compat2/corpus/scripts/pi_digits/main.py",
  "statement_kinds": ["FunctionDef", "Expr"],
  "entry_stmt_count": 1,
  "print_calls": 1,
  "has_main_guard": false,
  "is_script": true
}
```

- `path`: repo-relative POSIX path.
- `statement_kinds`: kinds in source order, `Docstring` for the leading string, guard-body entries inlined where the guard sat. This is the audit trail; `entry_stmt_count` and `print_calls` derive from it.
- `entry_stmt_count`: number of non-inert, non-If statements.
- `print_calls`: statements whose `call` is `print`, at top level or one level inside a guard.
- `has_main_guard`, `is_script`: as defined above.

Worked example, `pi_digits/main.py`:

```python
def pi_digits(count: int) -> str: ...   # FunctionDef, inert

print(pi_digits(60))                    # Expr, call=print -> relevant
```

Inventory: `FunctionDef` (inert), `Expr` with `call == "print"` (relevant). `entry_stmt_count` is 1, `print_calls` is 1, no guard, script.

The detector emits one file, a JSON list of these records, sorted by path. Files that fail `ast.parse` get `{"path": ..., "error": "...", "is_script": false}` and are excluded from the script count.

## Edge cases

- `__main__` guard variants. Only the exact structural form above counts as a guard. `if __name__ == '__main__':` (single quotes) matches, the constant is compared by value. `if "__main__" == __name__:` does not match (left is not the name); it stays a non-guard `If` and its body is not flattened. `sys.argv` checks, `if __name__ in ...`, and negated forms are non-guards too. None of the scanned files has a variant.
- Bare `pass` at module level. Recorded as kind `Pass` with `where`, counted in `statement_kinds`, inert: there is nothing to lower, so it never flips a file on its own. Same for `pass` inside a guard.
- Multi-file imports of scripts. The scan is per file and does not resolve imports. A module importing another module is classified on its own body; `import helpers` is inert. If E1 makes `pyscriptc compile dir/` run the package's entry script, that policy lands in the detector later, not in the classification rule. The corpus is one `main.py` per directory, so no script imports another script; `examples/` likewise.
- Files with only comments or whitespace. `ast.parse` yields an empty body, `statement_kinds` is `[]`, `entry_stmt_count` is 0, classified module. `examples/bad_annotation.py` is close to this: a single `FunctionDef`, module.
- Syntax errors. Reported with an `error` field, `is_script: false`, counted as neither. The scan must not abort the directory walk.
- Non-`main.py` files in the scripts corpus. Out of scope: the corpus is exactly one `main.py` per directory, and `expected.txt` / `meta.json` are not Python.

## Validation counts

Ran `uv run python docs/e1-entrypoint-scan.py` over both directories (2026-10-15, master at `2a07bb8`):

| Directory | Files | Scripts | Modules |
| --- | --- | --- | --- |
| `examples/` | 26 | 3 | 23 |
| `benchmarks/compat2/corpus/scripts/*/main.py` | 20 | 20 | 0 |

Every corpus script is E1-relevant, as the plan predicted: 20 of 20, all fenced today at the first module-level `Expr`/`Assign`/`For`/`Try`. The examples are mostly modules: only `floor_div.py`, `longer.py`, and `sum_squares.py` have a guard with a `print` call. 58 relevant statements across the 20 scripts, 25 of them `print` calls.

Statement-kind tally across both directories (statement occurrences, including inert):

| Kind | examples/ | scripts |
| --- | --- | --- |
| Import | 41 | 2 |
| FunctionDef | 37 | 27 |
| Docstring | 11 | 0 |
| ImportFrom | 4 | 0 |
| If (all non-guard) | 3 | 0 |
| ClassDef | 3 | 2 |
| Assign | 0 | 16 |
| Expr | 3 | 29 |
| AnnAssign | 0 | 3 |
| For | 0 | 8 |
| Try | 0 | 1 |
| While | 0 | 1 |

Per-file relevant counts for the scripts corpus:

| Script | entry stmts | prints | kinds beyond def/import |
| --- | --- | --- | --- |
| bigint_factorial | 4 | 3 | Assign, Expr x3 |
| binary_trees | 1 | 0 | For |
| bit_tricks | 3 | 3 | Expr x3 |
| caesar_cipher | 4 | 2 | Assign x2, Expr x2 |
| collatz | 4 | 1 | Assign x2, For, Expr |
| exceptions_flow | 3 | 2 | Expr x2, Try |
| fib_recursive | 1 | 0 | For |
| gcd_lcm | 4 | 2 | Assign, For, Expr x2 |
| generators_comp | 5 | 5 | Expr x5 |
| json_like_dump | 1 | 1 | Expr |
| linked_list | 6 | 1 | Assign x3, AnnAssign, While, Expr |
| mandelbrot | 1 | 0* | Expr (call `main`) |
| matmul | 5 | 1 | Assign x4, Expr |
| nbody | 1 | 0* | Expr (call `main`) |
| nqueens | 1 | 0 | For |
| pi_digits | 1 | 1 | Expr |
| quicksort | 6 | 2 | Assign x2, AnnAssign, For, Expr x2 |
| sieve | 2 | 1 | Assign, Expr |
| spectral_norm | 1 | 0* | Expr (call `main`) |
| word_freq | 4 | 0 | Assign, AnnAssign, For x2 |

*prints counted at module level only; the `print` calls inside `def main` are function bodies, out of scan scope.

## Surprises for E1 planning

- `Expr` dominates the corpus (29 occurrences) and 25 of those are `print` calls. The rest are calls like `main()` in `nbody`, `mandelbrot`, `spectral_norm`: the "call a function and let it print" pattern. E1's entry function must handle a bare `Name` call, not just `print`.
- Module-level `Assign` is the second entry kind (16), mostly seeds and buffers (`seed = 12345`, `text = "..."`, `h = reverse(build(1000))` followed by a `while` that mutates them). Announced variants (`AnnAssign`, 3) matter: `counts: dict[str, int] = {}` in `word_freq`, `data: list[int] = []` in `quicksort`, `node: Node | None = h` in `linked_list`.
- Control flow reaches module level directly: 8 `For`, 1 `While`, 1 `Try` (`exceptions_flow` raises and catches at top level). The plan's list of statements to lower matches, but the `Try` at module scope means E1's `finally`/`except` work must apply at entry scope, not only inside functions.
- `word_freq` has zero module-level prints; its value comes entirely from `For` + `print` inside loops. `binary_trees`, `fib_recursive`, `nqueens` print from inside a top-level `For`, so entry lowering has to carry loop bodies.
- `AugAssign` appears only inside function and loop bodies, never bare at module level, but entry-scope loops make it reachable (`total += node.value` in `linked_list`'s top-level `while`).
- No `Global`, `Delete`, `Assert`, `Raise`, `Match`, `Lambda` (as a statement), or `Await` at module scope in either directory. No `__main__` guard exists in the scripts corpus at all; all three in `examples/` are the exact canonical form.
- The three example scripts are fenced today only because their guard body is skipped (`lower.py` treats the guard as droppable). Under E1 the guard body becomes the entry function, which is the same shape the 20 corpus scripts need, just wrapped.

## Detector handoff

The E1 implementer reuses the rules verbatim (guard shape from `_is_main_guard`, docstring = position-0 string `Expr`, inert set, one-level guard inventory) and the record shape above. `docs/e1-entrypoint-scan.py` is the reference implementation for the scan; it is throwaway and can be deleted once the real detector lands.
