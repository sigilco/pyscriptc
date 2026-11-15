# SPDX-License-Identifier: Apache-2.0
"""The qualified annotation `mod.Thing` names a cross-module class (#1242).

The call path (`mod.Thing(...)`) already resolved a program module's class
through the package export table, but the annotation checker had no branch
for the same spelling: `def use(t: mod.Thing)` fenced with PS1106 while
the plain-name form (`from mod import Thing`) lowered.

The probe is a program directory with two modules: `mod.py` defines the
class, `__main__.py` annotates a parameter with the qualified name and
constructs the class through the module. The compiled binary prints what
CPython prints.
"""

import mod


def use(t: mod.Thing) -> str:
    return t.n


t = mod.Thing("y")
print(use(t))
