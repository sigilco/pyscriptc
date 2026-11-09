import shutil

p: str = "same.txt"
open(p, "w").write("x")
shutil.copyfile(p, p)
print("done")
