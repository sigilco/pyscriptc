"""The pyscriptc-arrow surface: Arrow C Data / C Stream exchange for the
primitive dtypes (issue #1209).

The adapter speaks the Arrow C Data Interface through the vendored nanoarrow
(bundle, namespaced `PsArrow`): a compiled program builds an Arrow array from
a subset list, reads a column back element by element, and exchanges whole
arrays and streams over the Arrow IPC stream wire form, which is the same
bytes the C Stream interface carries. A consumer under CPython (PyArrow,
nanoarrow, Polars) imports the exported bytes with `pyarrow.ipc.open_stream`
and sees one record batch of one column named `v`.

Supported dtypes: int32, int64, float32, float64, bool and utf8 (string).
The `dtype` constants are the adapter's own small integer words (`INT32` ..
`UTF8`); the thunk refuses anything else with a ValueError, which is the
honest fence for the Arrow types outside the subset (nested types,
dictionaries, unions, extension metadata, timestamps).

Positional arguments only, no capsule protocol in-process, no callbacks, no
context managers. The exchange with a live producer goes over the wire form:
`export_array` produces the bytes a consumer reads, `import_stream` reads
them back.

Exception surface:
- ValueError: the dtype word is not one of the constants, or a `list[int]`
  element is outside the dtype's range.
- TypeError: the values list holds an element type the dtype does not take.
- RuntimeError: nanoarrow refused an operation (allocation failure, IPC
  decode error); the message is "arrow: " plus nanoarrow's own text.
- IndexError: an out-of-range subscript (like a subset list subscript).

Precision fence: `array_get` and `array_sum` widen every numeric element to
double, so an int64 value above 2**53 loses integer precision (divergence
ledger, entry `arrow.f64-widen`).
"""

class Array: ...
class Stream: ...

INT32: int
INT64: int
FLOAT32: int
FLOAT64: int
BOOL: int
UTF8: int

def import_array(dtype: int, values: list) -> Array: ...
def array_length(array: Array) -> int: ...
def array_sum(array: Array) -> float: ...
def array_get(array: Array, index: int) -> float: ...
def array_is_null(array: Array, index: int) -> bool: ...
def export_array(array: Array) -> bytes: ...
def release(array: Array) -> None: ...
def import_stream(data: bytes) -> Stream: ...
def stream_read(stream: Stream) -> list[dict[str, str]]: ...
def stream_release(stream: Stream) -> None: ...
