import subprocess

subprocess.run(["false"], capture_output=True, check=True)
print("done")
