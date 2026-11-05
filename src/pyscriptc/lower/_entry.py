"""Entry-point construction, split verbatim from `lower.__init__`.

`EntryMixin` holds the entry split: the module scan that separates script
statements from definitions, the synthesized entry function, and the static
branch folds (`__name__` guard, `sys.version_info` compares,
`typing.TYPE_CHECKING`). `Lowerer` composes it, so every method stays
reachable as `self._split_entry(...)` and friends. Every body below is the
code that lived in `lower.__init__`, unchanged.
"""

from __future__ import annotations

import ast
import sys

from pyscriptc.lower._core import _CMP, Fence
from pyscriptc.lower._stdlib_shared import _ERASED_MODULES, _module_assigned, _release_erased_import
from pyscriptc.lower._stmt import _is_docstring, _is_main_guard


class EntryMixin:
    def _split_entry(
        self,
        live: list[ast.stmt],
        functions: list[ast.FunctionDef],
        class_nodes: list[ast.ClassDef],
        fences: list[Fence],
    ) -> list[ast.stmt]:
        """Walk the live statements: definitions stay where they are, script
        statements collect into the entry body, in source order.

        A module assignment tries the constant binding first. When the module
        also carries live script statements the failed binding rolls back and
        the assignment lowers inside the entry instead; a definition-only
        module keeps the constant fence it always raised. `pass` is inert
        (C3) and never makes a script.
        """
        entry_stmts: list[ast.stmt] = []
        failed_assigns: list[tuple[ast.stmt, int]] = []
        bare_decls: list[ast.AnnAssign] = []
        definitions = (ast.Import, ast.ImportFrom, ast.Pass, ast.FunctionDef, ast.ClassDef, ast.AnnAssign, ast.Assign)
        self._script_line = min(
            (stmt.lineno for stmt in live if not isinstance(stmt, definitions) and not _is_docstring(stmt)),
            default=None,
        )
        scriptish = self._script_line is not None
        divert = self._diverted_literals(live) if scriptish else set()
        for stmt in live:
            if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.Pass)):
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not self._accept_module_function(stmt, fences):
                    continue
                functions.append(stmt)
            elif isinstance(stmt, ast.ClassDef):
                class_nodes.append(stmt)
            elif isinstance(stmt, (ast.AnnAssign, ast.Assign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if any(isinstance(t, ast.Name) and t.id in divert for t in targets):
                    entry_stmts.append(stmt)
                    continue
                snapshot = (dict(self.constants), set(self.declared), len(fences))
                self.caught = []
                self._bind_module_assign(stmt)
                # A failed binding rolls back at once, so later assignments
                # bind against the pre-statement state. Its fences stay for
                # now; when the module turns out to be a script they are
                # retracted and the statement lowers inside the entry.
                if self.caught:
                    self.constants = snapshot[0]
                    self.declared = snapshot[1]
                    fences.extend(self.caught)
                    failed_assigns.append((stmt, snapshot[2]))
                elif isinstance(stmt, ast.AnnAssign) and stmt.value is None:
                    # Bound nothing, but a script may still need the
                    # declaration: the name's real assignment lowers inside
                    # the entry and reads `locals` for its annotation.
                    bare_decls.append(stmt)
                self.caught = []
            elif _is_docstring(stmt):
                continue
            else:
                entry_stmts.append(stmt)
        if entry_stmts:
            # This module is a script: retract the fences the failed constant
            # bindings raised; those assignments lower inside the entry
            # instead, in source order. Bindings that did not fence stay
            # bound - a script may still carry true constants.
            retract = min((at for _, at in failed_assigns), default=len(fences))
            if retract < len(fences):
                del fences[retract:]
            entry_stmts.extend(stmt for stmt, _ in failed_assigns)
            # A bare declaration follows its name into the entry unless already bound.
            for stmt in bare_decls:
                if isinstance(stmt.target, ast.Name) and stmt.target.id not in self.declared:
                    entry_stmts.append(stmt)
            entry_stmts.sort(key=lambda stmt: stmt.lineno)
        return entry_stmts

    def _diverted_literals(self, live: list[ast.stmt]) -> set[str]:
        """Names bound at module scope whose entry reassigns or mutates them.

        Module constants re-emit their literal on every read, so a script's
        `counts[k] = v` would write into a throwaway dict; the binding lowers
        inside the entry instead unless the name hides inside a def/class.
        Similarly, any module variable reassigned in the script must lower
        inside the entry so its updates take effect.
        """
        bound: set[str] = set()
        for stmt in live:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                bound.update(t.id for t in targets if isinstance(t, ast.Name))
        if not bound:
            return set()
        hidden: set[str] = set()
        for stmt in live:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                hidden.update(n.id for n in ast.walk(stmt) if isinstance(n, ast.Name) and n.id in bound)
        mutated: set[str] = set()
        for stmt in live:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if self._script_line is not None and stmt.lineno >= self._script_line:
                for n in ast.walk(stmt):
                    if isinstance(n, ast.Name) and n.id in bound and isinstance(n.ctx, (ast.Store, ast.Del)):
                        mutated.add(n.id)
            for n in ast.walk(stmt):
                if (
                    isinstance(n, ast.Subscript)
                    and isinstance(n.value, ast.Name)
                    and n.value.id in bound
                    and isinstance(n.ctx, (ast.Store, ast.Del))
                ):
                    mutated.add(n.value.id)
        return mutated - hidden

    def _entry_function(self, stmts: list[ast.stmt], functions: list[ast.FunctionDef]) -> list[ast.FunctionDef]:
        """The synthesized entry for module-level statements.

        A definition-only module (imports, constants, functions, classes,
        docstrings) synthesizes nothing: the IR is exactly what it was before
        entry lowering existed. Scripts get one function named `main`, or
        `__entry__` when the module defines its own `main` - the user's name
        always wins - carrying the live statements in source order.

        Package units never synthesize one: a package is a library, its
        modules are namespaced, and `sigs` feeds the export table.

        A program's `__main__` module does synthesize one: it is the script
        the whole program runs, so its top-level statements are the entry.
        A program's other modules synthesize `__init__`: their body runs when
        the program first imports them, before the importing module's body.
        """
        if not stmts:
            return []
        if self.module_name and not self._is_program_main():
            if not self.module_init:
                return []
            taken = {fn.name for fn in functions} | self.declared
            name = "__init__" if "__init__" not in taken else "__init_entry__"
            return [self._make_entry(stmts, name)]
        if self.cataloguing:
            # The cataloguing pass only needs the signatures. The program's
            # entry is not an export, so it is not synthesized here.
            return []
        taken = {fn.name for fn in functions} | self.declared
        name = "main" if "main" not in taken else "__entry__"
        return [self._make_entry(stmts, name)]

    def _make_entry(self, stmts: list[ast.stmt], name: str) -> ast.FunctionDef:
        """A synthetic no-argument function carrying `stmts` in source order."""

        fn = ast.FunctionDef(
            name=name,
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=stmts,
            decorator_list=[],
            returns=ast.Constant(value=None),
            type_comment=None,
            type_params=[],
        )
        for stmt in stmts:
            ast.copy_location(fn, stmt)
        fn.lineno = stmts[0].lineno
        fn.col_offset = 0
        fn.end_lineno = stmts[-1].end_lineno
        fn.end_col_offset = 0
        return fn

    def _is_program_main(self) -> bool:
        """True for the `__main__` module of a program directory.

        Project mode: the named entry module plays the same role under its
        own dotted name.
        """

        return self.program and (self.module_name == "__main__" or self.entry_module)

    def _is_main_module(self) -> bool:
        """True when this module runs as `__main__` (a script or program entry)."""

        return not self.module_name or self._is_program_main()

    def _is_program_module(self, module: str | None) -> bool:
        """A program tree's own module: a sibling, or a package inside it.

        In a program the export table is keyed by plain names (`util`,
        `pkg.x`), so any exact key match, or a dotted name whose head is a
        program package, is the program's own module. `__main__` is the
        entry module and never importable.

        A dependency source root's package (issue #398) is the program's own
        module too: it is exported under its plain name, so `import certifi`
        binds the package the way a sibling module does.
        """

        if not self.program or not module or self.package is None:
            return False
        if module == "__main__":
            return True
        if module in self.package:
            return True
        head = module.split(".", 1)[0]
        if head in self.program_roots:
            return True
        return head in self.package and self.package[head] is not None

    def _live_statements(
        self,
        statements: list[ast.stmt],
        fences: list[Fence],
        rebound: set[str] | None = None,
    ) -> list[ast.stmt]:
        # Names assigned by user code. A later `if TYPE_CHECKING` is the typing
        # constant only while that name is still the erased import.
        if rebound is None:
            rebound = set()
        live: list[ast.stmt] = []
        for stmt in statements:
            if isinstance(stmt, ast.Import):
                self._bind_import(stmt, fences)
                live.append(stmt)
                _release_erased_import(stmt, rebound)
                continue
            if isinstance(stmt, ast.ImportFrom):
                self._bind_from(stmt, fences)
                live.append(stmt)
                _release_erased_import(stmt, rebound)
                continue
            if _is_main_guard(stmt):
                if self._is_main_module():
                    # C3: the canonical guard's body is live script text. It joins
                    # the entry in source order; an `else` under a `__name__` test
                    # never runs as `__main__`, so it stays dropped.
                    live.extend(self._live_statements(list(stmt.body), fences, rebound))
                    continue
                # An imported module is not `__main__`: the guard body never
                # runs there, and the `else` branch does.
                live.extend(self._live_statements(list(stmt.orelse), fences, rebound))
                continue
            if _is_docstring(stmt):
                continue
            if isinstance(stmt, ast.If):
                taken = self._static_branch(stmt, rebound)
                if taken is None:
                    live.append(stmt)
                    rebound.update(_module_assigned(stmt))
                    continue
                # The other branch is not lowered. Its imports and annotations stay unread.
                live.extend(self._live_statements(taken, fences, rebound))
                continue
            live.append(stmt)
            rebound.update(_module_assigned(stmt))
        return live

    def _static_branch(self, stmt: ast.If, rebound: set[str]) -> list[ast.stmt] | None:
        held = self._version_holds(stmt.test)
        if held is None:
            held = self._type_checking_value(stmt.test, rebound)
        if held is None:
            return None
        return list(stmt.body if held else stmt.orelse)

    def _type_checking_value(self, node: ast.expr, rebound: set[str]) -> bool | None:
        """typing.TYPE_CHECKING is False. `not TYPE_CHECKING` is True. Anything else is not this fold."""
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            inner = self._type_checking_value(node.operand, rebound)
            if inner is None:
                return None
            return not inner
        if isinstance(node, ast.Name):
            if node.id in rebound:
                return None
            if self.type_aliases.get(node.id) == "TYPE_CHECKING":
                return False
            return None
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "TYPE_CHECKING"
            and isinstance(node.value, ast.Name)
            and node.value.id not in rebound
        ):
            binding = self.bindings.get(node.value.id)
            if binding is not None and binding.kind == "erased" and binding.module in _ERASED_MODULES:
                return False
        return None

    def _version_holds(self, test: ast.expr) -> bool | None:
        if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
            return None
        op = _CMP.get(type(test.ops[0]))
        if op is None:
            return None
        left = self._version_operand(test.left)
        right = self._version_operand(test.comparators[0])
        if left == "version" and isinstance(right, tuple):
            return _cmp_version(op, right)
        if right == "version" and isinstance(left, tuple):
            flipped = {">=": "<=", "<=": ">=", ">": "<", "<": ">", "==": "==", "!=": "!="}[op]
            return _cmp_version(flipped, left)
        return None

    def _version_operand(self, node: ast.expr) -> str | tuple[int, ...] | None:
        if self._is_version_info(node):
            return "version"
        if isinstance(node, ast.Tuple) and node.elts and all(_is_int_const(elt) for elt in node.elts):
            return tuple(int(elt.value) for elt in node.elts if isinstance(elt, ast.Constant))
        return None

    def _is_version_info(self, node: ast.expr) -> bool:
        if not isinstance(node, ast.Attribute) or node.attr != "version_info" or not isinstance(node.value, ast.Name):
            return False
        binding = self.bindings.get(node.value.id)
        return (
            binding is not None
            and binding.kind == "module"
            and binding.module == "sys"
            and not binding.rooted
            and not binding.queued
        )


def _is_int_const(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool)


def _cmp_version(op: str, parts: tuple[int, ...]) -> bool:
    current = sys.version_info
    if op == ">=":
        return current >= parts
    if op == ">":
        return current > parts
    if op == "<=":
        return current <= parts
    if op == "<":
        return current < parts
    if op == "==":
        return current == parts
    if op == "!=":
        return current != parts
    return False
