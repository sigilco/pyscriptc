# Issue #877: `tempfile.mkstemp()` returns the (fd, path) pair. The path
# differs between the two runs (CPython and the compiled program each
# create their own file), so the probe prints booleans and the file
# contents instead of the path itself.
import os
import tempfile

fd, path = tempfile.mkstemp()
print(fd > 0)
print(os.path.isabs(path))
print(os.path.basename(path).startswith("tmp"))
print(os.path.exists(path))
os.write(fd, b"payload")
os.close(fd)
with open(path, "rb") as handle:
    print(handle.read() == b"payload")
print(os.path.exists(path))
os.unlink(path)
print(os.path.exists(path))

# The pair is a tuple: the index reads and the unpacking both work.
fd2, path2 = tempfile.mkstemp()
print(fd2 > 0)
print(path2.endswith(path2[-6:]))
os.close(fd2)
os.unlink(path2)
