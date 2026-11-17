# SPDX-License-Identifier: Apache-2.0
# Divergence 78 (issue #1228): the email read subset carries two seams
# against CPython's compat32 reader.
#
# - `get_payload()` on a multipart part answers the list of the child
#   parts in CPython (the list-of-objects answer is outside the closed
#   surface, so the subset raises ValueError there).
# - An RFC 2047 encoded word in a charset the subset does not decode keeps
#   the raw word in the header value; CPython decodes the word and marks
#   the defect instead.
#
# The probe prints the count of the multipart payload (the payload itself
# is a list of message objects, whose repr carries a memory address, so
# only the count is printed) and the header text, so both sides replay
# byte-identically and no traceback reaches the recorded bytes.
import email

MULTI = 'Content-Type: multipart/mixed; boundary="B"\n\n--B\nContent-Type: text/plain\n\nhi\n--B--\n'

msg = email.message_from_string(MULTI)
if msg.is_multipart():
    try:
        payload = msg.get_payload()
        print(len(payload))
    except Exception as exc:
        print(type(exc).__name__)

encoded = email.message_from_string("Subject: =?koi8-r?Q?test?=\n\nbody\n")
subject: str | None = encoded.get("Subject")
if subject is not None:
    print(subject)
print(encoded.get_payload())
