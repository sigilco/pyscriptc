# Issue #488: csv.DictReader and csv.DictWriter over the ps_csv core.
# The reader maps the header row onto every record (a short record takes
# the None restval); the writer names the fields once and writes the
# header row and the rows of a dict through the same excel-dialect row
# writer. A temp file carries the file-handle lane, so the two runs
# compare the same bytes.
import csv
import io
import os
import tempfile

# The reader over physical lines: named columns, a short record, quoted
# fields with embedded commas, a tab delimiter. The row text narrows out
# of `str | None` before it prints, the way the row dict reads.
lines = ["name,age,city", "ada,36,london", "alan,41"]
for row in csv.DictReader(lines):
    name: str | None = row["name"]
    age: str | None = row["age"]
    city: str | None = row["city"]
    if name is None or age is None or city is None:
        print("missing")
    else:
        print(name, age, city)

tab_lines = ["a\tb", "1\t2"]
for row in csv.DictReader(tab_lines, delimiter="\t"):
    first: str | None = row["a"]
    second: str | None = row["b"]
    if first is None or second is None:
        print("missing")
    else:
        print(first, second)


def dump(path: str) -> int:
    # The reader over a file handle: the bytes round-trip, and the writer
    # output matches CPython's excel dialect (\r\n line terminator,
    # minimal quoting, the empty-text restval for a missing field).
    with open(path) as f:
        for row in csv.DictReader(f):
            name: str | None = row["name"]
            age: str | None = row["age"]
            note: str | None = row["note"]
            if name is None or age is None or note is None:
                print("missing")
            else:
                print(name, age, note)
    return 0


path = os.path.join(tempfile.mkdtemp(), "people.csv")
with open(path, "w") as f:
    writer: csv.DictWriter = csv.DictWriter(f, ["name", "age", "note"])
    writer.writeheader()
    writer.writerow({"name": "ada", "age": "36", "note": "x,y"})
    writer.writerow({"name": "alan", "age": "41"})
dump(path)
os.unlink(path)

# The StringIO lane: the writer bytes, read back as text.
buf: io.StringIO = io.StringIO()
wr: csv.DictWriter = csv.DictWriter(buf, ["a", "b"])
wr.writeheader()
wr.writerow({"a": 'q"z', "b": ""})
text: str = buf.getvalue()
print(len(text))
print(text.endswith("\r\n"))
print(text.count('""'))
