import argparse
from pathlib import Path

p: argparse.ArgumentParser = argparse.ArgumentParser()
p.add_argument("--out", type=Path)
print("ok")
