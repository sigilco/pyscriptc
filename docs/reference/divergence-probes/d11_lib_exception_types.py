import shutil

try:
    shutil.copyfile("/no/such", "dst.bin")
except RuntimeError:
    print("RuntimeError")
