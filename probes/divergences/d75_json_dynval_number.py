# SPDX-License-Identifier: Apache-2.0
# Divergence 75 (issue #1267): a JSON integer literal too large for i64
# parses as the dynamic value's float arm, where CPython's json grows an
# arbitrary-precision int. The sign rides the float fallback either way.
import json

wide: object = json.loads("12345678901234567890123")
print(wide)

negative: object = json.loads("-12345678901234567890123")
print(negative)
