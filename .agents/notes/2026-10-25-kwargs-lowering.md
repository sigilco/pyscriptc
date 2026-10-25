# Keyword argument lowering: alternatives and recommendation

Date: 2026-10-25. Branch work/kwmech. Refs #592.

## Current design

Keywords are resolved at compile time at the call site (`_fill_args` and `_fill_varargs` in `src/pyscriptc/lower/_calls.py`). The callee has a plain positional signature, so a call with keywords becomes a positional call with defaults filled in. Two bugs were real and are fixed on this branch: argument evaluation order (CPython evaluates in written order, the positional rewrite evaluated in parameter order, now pinned with temps via `_pin_arg_order`) and keywords on nested defs (`local_sigs` records the signature). Optional dataclass field defaults also reach the generated `__init__`.

## Alternatives

1. Call-site binding (current). Zero runtime cost, no new IR node, callee unchanged. Cost: the callee must be statically known, so keywords never work through a function value, a stored callable, a method resolved at run time, or `**kwargs` forwarding. Those are refused at compile time with PS2003.
2. Parameter object. The call site builds an aggregate (a struct of the parameters plus a presence bitmask) and passes one pointer. The callee unpacks fields, using the bitmask to pick the default. Works for callables whose target is not known at the call site and for `**kwargs` forwarding of a known key set. Cost: a new calling convention for every function that wants it (or two entry points per function), an extra alloca and stores per call that LLVM must scalar-replace to recover the positional speed, and every IR hash for functions with keywords changes.
3. Dictionary or hash table passed as one pointer. Most general (arbitrary `**kwargs`, runtime-chosen names). Cost: a heap allocation or arena per call, string hashing per key, a boxed value per argument (the subset has no universal value type, so each entry needs a tag and a union), and loss of static type checking of the keyword types. This is the CPython model and the opposite of the native plane's goal.
4. Per-call-shape specialization. Emit one thin wrapper per distinct keyword shape seen in the program, each forwarding positionally. Equivalent to option 1 for static targets, and the only way to keep a stable address for a function value. Cost: code growth proportional to distinct shapes.

## Adversarial points

- Option 2 and 3 look cheap in the compiler but move cost into every call, including the 99 percent that are positional. Only option 1 and 4 keep positional calls untouched.
- A struct passed by pointer defeats inlining unless the optimizer proves the pointer does not escape; across the island boundary (ps_island) it would escape, so the island path would need a separate adapter anyway.
- Option 3 breaks the type system: a dict of heterogeneous values has no type in the subset (divergence 45 lists heterogeneous dict tables as refused), so it would need a new boxed value kind.
- The measured demand is low. In the two ported scripts the only remaining keyword fences are two `Path.write_text(..., encoding=)` calls in binary_size, a stdlib signature fixed in the compiler, and fixing them unblocks 0 definitions. No script needed a runtime keyword.

## Prototype

None built. A struct prototype would only show that LLVM scalar-replaces the aggregate when the callee is inlined, which is known behavior, and nothing here measures the cost that matters (calls through a function value), because the subset has no keyword calls through function values to measure. Building it would cost a calling-convention change for no evidence.

## Recommendation

Keep option 1. Add option 4 only if a port needs a keyword call through a function value. Do not adopt options 2 or 3.

## Kill criterion

Revisit if either holds: at least 3 distinct real scripts in `benchmarks/ports/` (or sibling scripts audited in the delta notes) are blocked solely by a keyword call whose target is not statically known or by `**kwargs` forwarding, or a measured release-gate script shows keyword calls costing more than 2 percent of the native share. Until then, keyword fences stay compile-time errors with the PS2003 message and this note stays the decision record.
