import subprocess

out: bytes = subprocess.check_output(["printf", "island"])
print(out)
print(len(out))
