import os

os.mkdir("w")
os.mkdir("w/a")
os.mkdir("w/b")
for dirpath, dirnames, _filenames in os.walk("w"):
    if dirpath == "w":
        dirnames.remove("a")
    print(dirpath)
