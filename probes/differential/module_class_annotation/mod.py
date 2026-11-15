# SPDX-License-Identifier: Apache-2.0
"""The defining module of the #1242 probe: one class, one field."""


class Thing:
    def __init__(self, n: str) -> None:
        self.n = n
