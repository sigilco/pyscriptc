# Divergence D69: the archive read surface. The `r:bz2` and `r:xz` tar
# modes compile and raise RuntimeError at run time (the runtime links no
# bzip2 or lzma library), where CPython reads them through BZ2File and
# LZMAFile. The KeyError of a missing extractfile name matches CPython's
# exception type but not its quoted message wording.
#
# The probe opens a path that does not exist, so CPython's side fails in
# the `bz2` module with a FileNotFoundError (its traceback names the
# stdlib source lines, which move between CPython patch releases) while
# the compiled side fails in the runtime with the RuntimeError. The
# exception type is the seam, so the probe prints the type name on both
# sides and catches it; the traceback never reaches the recorded bytes.
import os
import tarfile

path: str = "/tmp/a1241_div.tar.bz2"
if os.path.exists(path):
    os.unlink(path)

try:
    tarfile.open(path, "r:bz2")
except Exception as exc:
    print(type(exc).__name__)
