"""Stdlib import binding, split verbatim from `lower._stdlib`.

`StdlibBindMixin` holds the `_bind_*` import-binding family (`_bind_import`,
`_bind_from`, `_bind_package_import`, `_bind_pkg_module`, `_bind_own_import`,
`_bind_own_from`), the fence message builders (`_import_fence_message`, the
module-level `_dep_lane_hint`) and the synthetic-class notebook
(`_note_module`). `StdlibDispatchMixin` composes it the way `CallsMixin`
composes `CallAttrMixin`, so every method stays reachable as
`self._bind_import(...)` and friends. Every body below is the code that lived
in `lower._stdlib`, unchanged: the IR and diagnostics are byte-identical.
"""

from __future__ import annotations

import ast

from pyscriptc.errors import span_of
from pyscriptc.ffi_manifest import handle_type, type_ir
from pyscriptc.lower._core import _FUTURE_OK, Binding, Fence, _package_base
from pyscriptc.lower._stdlib_shared import _ERASED_MODULES
from pyscriptc.stdlib_queue import (
    MATH_CONSTANTS,
    PACK_MODULES,
    SPECIAL_FUNCTIONS,
    canonical_module,
    dep_lanes,
    island_lane,
    math_function,
    module_status,
    wave_function,
)
from pyscriptc.stdmore import MODULE_CLASSES, member_callable, module_constant


class StdlibBindMixin:
    def _import_fence_message(self, name: str) -> str:
        head = name.split(".", 1)[0]
        if self.config is not None:
            lane, _key = self.config.lane_for(head)
            if lane == "refuse":
                return f"import `{head}` is refused by the `imports` policy"
        return _dep_lane_hint(name, f"{name} is not in the closed import table")

    def _bind_import(self, stmt: ast.Import, fences: list[Fence]) -> None:
        for alias in stmt.names:
            root = alias.name.split(".", 1)[0]
            if root in _ERASED_MODULES:
                self.bindings[alias.asname or root] = Binding("erased", root, "", False)
                continue
            if self._bind_ffi(alias.name, alias.asname or root):
                continue
            if self._is_own_package(alias.name) or self._is_program_module(alias.name):
                self._bind_own_import(alias, stmt, fences)
                continue
            if alias.name.split(".", 1)[0] in PACK_MODULES:
                self.bindings[alias.asname or alias.name] = Binding("module", alias.name, "", False)
                self._note_module(alias.name)
                continue
            status = module_status(alias.name)
            if status is None:
                head = alias.name.split(".", 1)[0]
                if self.config is not None and self.config.lane_for(head)[0] == "refuse":
                    fences.append(
                        Fence(
                            "PS2002",
                            f"import `{head}` is refused by the `imports` policy",
                            span_of(stmt, self.filename),
                            None,
                        )
                    )
                    continue
                # boxed:begin
                if island_lane(alias.name):
                    parts = alias.name.split(".")
                    if alias.asname or len(parts) == 1:
                        bound = alias.asname or parts[0]
                        module = alias.name
                        rooted = False
                    else:
                        bound = parts[0]
                        module = parts[0]
                        rooted = True
                    self.bindings[bound] = Binding("island", module, "", False, rooted=rooted)
                    continue
                # boxed:end
                fences.append(
                    Fence(
                        "PS2002",
                        self._import_fence_message(alias.name),
                        span_of(stmt, self.filename),
                        None,
                    )
                )
                continue
            parts = alias.name.split(".")
            if alias.asname or len(parts) == 1:
                bound = alias.asname or parts[0]
                rooted = False
            else:
                bound = parts[0]
                rooted = True
            self.bindings[bound] = Binding("module", alias.name, "", status == "queued", rooted=rooted)
            self._note_module(alias.name)

    def _bind_from(self, stmt: ast.ImportFrom, fences: list[Fence]) -> None:
        if stmt.module in _ERASED_MODULES and not stmt.level:
            for alias in stmt.names:
                if alias.name == "*":
                    fences.append(Fence("PS2002", "star import is not lowered", span_of(stmt, self.filename), None))
                    continue
                self.type_aliases[alias.asname or alias.name] = alias.name
            return
        if stmt.module == "__future__" and not stmt.level:
            for alias in stmt.names:
                if alias.name not in _FUTURE_OK:
                    fences.append(
                        Fence(
                            "PS2002",
                            f"__future__ import {alias.name} is not a type import",
                            span_of(stmt, self.filename),
                            None,
                        )
                    )
            return
        if stmt.level or not stmt.module:
            if self.package is None:
                fences.append(Fence("PS2002", "relative import is not lowered", span_of(stmt, self.filename), None))
                return
            self._bind_package_import(stmt, fences)
            return
        if self._is_own_package(stmt.module) or self._is_program_module(stmt.module):
            self._bind_own_from(stmt, fences)
            return
        if canonical_module(stmt.module) in PACK_MODULES:
            for alias in stmt.names:
                local = alias.asname or alias.name
                if member_callable(stmt.module, alias.name):
                    self.bindings[local] = Binding("func", stmt.module, alias.name, False)
                    self._note_module(stmt.module)
                    continue
                fences.append(
                    Fence(
                        "PS2001",
                        f"{stmt.module}.{alias.name} is not in the lowered subset of {stmt.module}",
                        span_of(stmt, self.filename),
                        None,
                    )
                )
            return
        status = module_status(stmt.module)
        if status is None:
            # boxed:begin
            if island_lane(stmt.module):
                for alias in stmt.names:
                    if alias.name == "*":
                        fences.append(Fence("PS2002", "star import is not lowered", span_of(stmt, self.filename), None))
                        continue
                    local = alias.asname or alias.name
                    self.bindings[local] = Binding("island_member", stmt.module, alias.name, False)
                return
            # boxed:end
            fences.append(
                Fence(
                    "PS2002",
                    self._import_fence_message(stmt.module),
                    span_of(stmt, self.filename),
                    None,
                )
            )
            return
        # Aliased spellings (os.path -> posixpath) bind against the canonical
        # table so later calls reach the lowered rows under the real module.
        module = canonical_module(stmt.module)
        manifest = self._ffi_module(module)
        for alias in stmt.names:
            if alias.name == "*":
                fences.append(Fence("PS2002", "star import is not lowered", span_of(stmt, self.filename), None))
                continue
            if manifest is not None:
                if manifest.function(alias.name) is not None:
                    self.bindings[alias.asname or alias.name] = Binding("func", module, alias.name, False)
                    continue
                if manifest.handle(alias.name) is not None:
                    # A handle binds as a type name: annotations on it lower
                    # to the opaque ptr path (`from demo import Conn` then
                    # `c: Conn`). It is not a value (issue #224).
                    self.bindings[alias.asname or alias.name] = Binding(
                        "handle", module, alias.name, False, handle_type(alias.name)
                    )
                    continue
                if manifest.const(alias.name) is not None:
                    typ = type_ir(manifest.const(alias.name).type, manifest.handle_names)
                    self.bindings[alias.asname or alias.name] = Binding("const", module, alias.name, False, typ)
                    continue
                fences.append(
                    Fence(
                        "PS2001",
                        f"{module}.{alias.name} is not in the FFI manifest of {module}",
                        span_of(stmt, self.filename),
                        None,
                    )
                )
                continue
            local = alias.asname or alias.name
            if status == "queued":
                self.bindings[local] = Binding("func", module, alias.name, True)
                continue
            if module == "math" and math_function(alias.name) is not None:
                self.bindings[local] = Binding("func", module, alias.name, False, math_function(alias.name)["return"])
                continue
            if module == "math" and alias.name in MATH_CONSTANTS:
                self.bindings[local] = Binding("const", module, alias.name, False, {"kind": "float"})
                continue
            if wave_function(module, alias.name) is not None or alias.name in SPECIAL_FUNCTIONS.get(
                module, frozenset()
            ):
                self.bindings[local] = Binding("func", module, alias.name, False)
                self._note_module(module)
                continue
            constant = module_constant(module, alias.name)
            if constant is not None:
                self.bindings[local] = Binding("const", module, alias.name, False, constant[1])
                continue
            if member_callable(module, alias.name):
                self.bindings[local] = Binding("func", module, alias.name, False)
                self._note_module(module)
                continue
            child = f"{module}.{alias.name}"
            child_status = module_status(child)
            if child_status is not None:
                self.bindings[local] = Binding("module", child, "", child_status == "queued")
                self._note_module(child)
                continue
            # The member is outside the lowered subset. Record where the name
            # came from so a later use (a decorator) still names the stdlib
            # member (issue #732): the behavior-synthesizing guard reads this
            # table for a `from functools import lru_cache` spelling.
            self.stdlib_imports[local] = (module, alias.name)
            fences.append(
                Fence(
                    "PS2001",
                    f"{module}.{alias.name} is not in the lowered subset of {module}",
                    span_of(stmt, self.filename),
                    None,
                )
            )

    def _bind_package_import(self, stmt: ast.ImportFrom, fences: list[Fence]) -> None:
        if self.program and not self.is_package and "." not in self.module_name:
            # A program's plain sibling module is not a package: CPython
            # refuses a relative import there ("attempted relative import
            # with no known parent package"). Import the sibling by name.
            fences.append(
                Fence(
                    "PS2002",
                    f"relative import in {self.module_name} has no parent package; import the module by name",
                    span_of(stmt, self.filename),
                    None,
                )
            )
            return
        base = _package_base(self.module_name, self.is_package, stmt.level)
        if base is None:
            fences.append(Fence("PS2002", "relative import is not lowered", span_of(stmt, self.filename), None))
            return
        if stmt.module:
            module = ".".join([*base, *stmt.module.split(".")]) if base else stmt.module
            for alias in stmt.names:
                if alias.name == "*":
                    self._bind_star(module, stmt, fences)
                    continue
                self._bind_one_export(module, alias.name, alias.asname or alias.name, stmt, fences)
            return
        for alias in stmt.names:
            if alias.name == "*":
                fences.append(Fence("PS2002", "star import is not lowered", span_of(stmt, self.filename), None))
                continue
            local = alias.asname or alias.name
            child = ".".join([*base, alias.name]) if base else alias.name
            if self._export_lookup(child, None) is not None:
                self._bind_pkg_module(local, child, stmt, fences)
                continue
            owner = ".".join(base)
            if owner and self._export_lookup(owner, alias.name) is not None:
                if owner != self.module_name and self.program and self._export_lookup(owner, None) is not None:
                    # CPython runs the owner package's body before this
                    # import completes; the init call needs the name bound.
                    self._bind_pkg_module(owner.split(".")[-1], owner, stmt, fences)
                self._bind_one_export(owner, alias.name, local, stmt, fences)
                continue
            fences.append(
                Fence(
                    "PS2002",
                    f"{child} is not a module in this package",
                    span_of(stmt, self.filename),
                    None,
                )
            )

    def _bind_pkg_module(self, local: str, module: str, stmt: ast.AST, fences: list[Fence]) -> None:
        existing = self.bindings.get(local)
        if existing is not None and existing.kind == "pkg" and existing.module == module:
            # The same module imported twice under the same name: CPython
            # keeps one binding, and so does this.
            return
        if local in self.bindings or local in self.constants or local in self.declared:
            fences.append(Fence("PS2002", f"{local} is already bound", span_of(stmt, self.filename), None))
            return
        self.bindings[local] = Binding("pkg", module, "", False)
        self.declared.add(local)

    def _bind_own_import(self, alias: ast.alias, stmt: ast.Import, fences: list[Fence]) -> None:
        root = alias.name.split(".", 1)[0]
        if alias.asname:
            if self._export_lookup(alias.name, None) is None:
                fences.append(
                    Fence(
                        "PS2002",
                        f"{alias.name} is not a module in this package",
                        span_of(stmt, self.filename),
                        None,
                    )
                )
                return
            self._bind_pkg_module(alias.asname, alias.name, stmt, fences)
            return
        if self._export_lookup(root, None) is None or (
            alias.name != root and self._export_lookup(alias.name, None) is None
        ):
            missing = alias.name if alias.name != root else root
            fences.append(
                Fence(
                    "PS2002",
                    f"{missing} is not a module in this package",
                    span_of(stmt, self.filename),
                    None,
                )
            )
            return
        self._bind_pkg_module(root, root, stmt, fences)

    def _bind_own_from(self, stmt: ast.ImportFrom, fences: list[Fence]) -> None:
        module = stmt.module or ""
        if self.program and module and module != "__main__":
            # A program's `from pkg import x` runs pkg's body before it binds
            # x, and that body's call is spelled `pkg.__init__()`: the module
            # name is bound here (CPython keeps the module object reachable
            # the same way; the name itself is this compiler's one addition).
            head = module.split(".")[0]
            self._bind_pkg_module(head, head, stmt, fences)
        for alias in stmt.names:
            if alias.name == "*":
                # `from pkg.mod import *` binds the module's public names,
                # the same set the export table records.
                self._bind_star(module, stmt, fences)
                continue
            local = alias.asname or alias.name
            child = f"{module}.{alias.name}"
            if self._export_lookup(child, None) is not None:
                self._bind_pkg_module(local, child, stmt, fences)
                continue
            if self._export_lookup(module, None) is not None:
                if (
                    module != self.module_name
                    and self._export_lookup(child, None) is not None
                    and self._export_lookup(module, alias.name) is None
                ):
                    # CPython's submodule fallback: `from pkg.sub import blob`
                    # with no `blob` export in `pkg.sub` imports the module
                    # `pkg.sub.blob` and binds it. The import runs the
                    # submodule's body, so the unit must be kept too.
                    self._bind_pkg_module(local, child, stmt, fences)
                    continue
                self._bind_one_export(module, alias.name, local, stmt, fences)
                continue
            fences.append(
                Fence(
                    "PS2002",
                    f"{module} is not a module in this package",
                    span_of(stmt, self.filename),
                    None,
                )
            )

    def _note_module(self, module: str) -> None:
        if module == "re" or module.startswith("re."):
            if module_status("re") == "lowered":
                self.synthetics.add("Pattern")
                self.synthetics.add("Match")
        if module == "urllib" or module == "urllib.parse" or module.startswith("urllib."):
            if module_status("urllib.parse") == "lowered":
                self.synthetics.add("SplitResult")
        if module == "urllib.request" and module_status("urllib.request") == "lowered":
            self.synthetics.add("HttpRequest")
            self.synthetics.add("HttpResponse")
        if module == "urllib.error" and module_status("urllib.error") == "lowered":
            self.synthetics.add("HTTPError")
            self.synthetics.add("URLError")
        for name, classes in MODULE_CLASSES.items():
            if module == name or module.startswith(name + "."):
                if module_status(name) == "lowered":
                    self.synthetics.update(classes)


def _dep_lane_hint(module: str, message: str) -> str:
    """The fence message for an import that names a locked dependency.

    A dependency on a lane the compiler cannot take (issue #398) is refused
    with the distribution and the lane hint, so the user knows which
    dependency refused and what would unblock it. With the #408 map, the
    key is the recorded import name, not the distribution name read as a
    module: `import yaml` names PyYAML. An unknown distribution is named as
    `unknown` rather than guessed; any other import keeps today's message.
    """

    lanes = dep_lanes()
    if not lanes:
        return message
    head = module.split(".", 1)[0]
    entry = lanes.get(head)
    if entry is None:
        return message
    if entry.get("importNames") == "unknown":
        return f"{message} ({entry['distribution']} is locked, on the {entry['lane']} lane: {entry['hint']})"
    if entry.get("ambiguous"):
        return f"{message} ({entry['ambiguous']} both provide {head}; lock only one)"
    return f"{message} ({entry['distribution']} provides {head}, on the {entry['lane']} lane: {entry['hint']})"
