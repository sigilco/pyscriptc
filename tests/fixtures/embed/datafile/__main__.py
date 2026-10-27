"""The acceptance program that reads a staged data file (issue #665)."""

import os

from helper import read_data

print(read_data(), os.path.basename(os.path.dirname(__file__)))
