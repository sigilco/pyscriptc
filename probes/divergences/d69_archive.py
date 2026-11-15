# Divergence D69: the archive read surface. The `r:bz2` and `r:xz` tar
# modes compile and raise RuntimeError at run time (the runtime links no
# bzip2 or lzma library), where CPython reads them through BZ2File and
# LZMAFile. The KeyError of a missing extractfile name matches CPython's
# exception type but not its quoted message wording.
import tarfile

try:
    tarfile.open("/tmp/a1241_div.tar.bz2", "r:bz2")
except RuntimeError:
    print("RuntimeError")
