# SPDX-License-Identifier: Apache-2.0
"""The compile-time class-id enum (issue #447, divergence 10).

Every lowered class takes one monotonically increasing id at its
signature pass. The ids are pure compile-time constants: a classmethod
call site bakes the receiving class's id into the argument list and a
classmethod body's `cls` uses read the parameter back. The table rides
one lowering run, so ids are stable within a compile and never escape
it; a name that never took a class body reads as 0.
"""

from __future__ import annotations


class ClassIdTable:
    """One id per lowered class, assigned in class-definition order."""

    def __init__(self) -> None:
        self.ids: dict[str, int] = {}
        self.next_id = 1

    def assign(self, name: str) -> int:
        """The id of `name`, taking the next free one on first sight."""
        found = self.ids.get(name)
        if found is None:
            found = self.next_id
            self.ids[name] = found
            self.next_id += 1
        return found

    def of(self, name: str) -> int:
        """The id of `name`; a name that never lowered reads as 0."""
        return self.ids.get(name, 0)
