# SPDX-License-Identifier: Apache-2.0
# Divergence 71 (issue #1223): the TimeoutExpired record carries the code
# and the message only. CPython's exception holds the command and the
# bound as attributes (e.cmd, e.timeout, e.output) and its args pair is
# (cmd, timeout); here the record's args holds the message and the
# attributes are not lowered. The str spelling matches CPython's exactly;
# the probe prints the args pair's length, not the clock-derived timeout
# fraction, so both sides replay byte-identically.
import subprocess

try:
    subprocess.run(["sleep", "5"], timeout=1)
except subprocess.TimeoutExpired as exc:
    print(str(exc))
    print(len(exc.args))
