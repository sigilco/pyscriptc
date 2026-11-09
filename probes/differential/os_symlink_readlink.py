import os
import tempfile

target = os.path.join(tempfile.mkdtemp(), "target.txt")
with open(target, "w") as handle:
    handle.write("payload")

link = target + ".link"
os.symlink(target, link)
print(os.readlink(link) == target)
print(os.path.isfile(link))
print(os.path.exists(link))
os.unlink(link)
print(os.path.exists(link))
