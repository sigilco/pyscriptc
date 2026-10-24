import subprocess

cp: subprocess.CompletedProcess = subprocess.run(["printf", "x"], capture_output=True)
print(cp.stdout)
