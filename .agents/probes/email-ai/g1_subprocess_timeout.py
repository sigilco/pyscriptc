from __future__ import annotations
import subprocess
def main() -> None:
    out: bytes = subprocess.run(["echo","x"], capture_output=True, timeout=30).stdout
    print(out.decode())
if __name__ == "__main__": main()
