import csv

# The restkey default: CPython stores the extra fields under the None key
# and the row still yields; the compiled reader raises ValueError because
# the None key and the list[str] value are outside the row dict's type.
for row in csv.DictReader(["a,b", "1,2,3"]):
    a: str | None = row["a"]
    b: str | None = row["b"]
    if a is None or b is None:
        print("missing")
    else:
        print(a, b)
