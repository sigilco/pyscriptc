# Issue #503: `os.system` shells out through the host shell and answers the
# wait status of the child. The probe keeps the child's output off the
# parent's stdout (the child writes into a file, the parent reads it back), so
# the byte comparison never depends on how the two streams interleave. The
# status CPython reports is the raw wait status (the exit code in the high
# byte), not the decoded exit code, and the shell owns the interpretation of
# the command, so both sides agree on every row.
import os


def main() -> None:
    path: str = "os_system_probe.txt"
    code: int = os.system("echo hello > " + path)
    print(code)
    print(code == 0)

    with open(path) as handle:
        print(handle.read())

    code = os.system("exit 3")
    print(code)
    print(code == 3 * 256)

    code = os.system("exit 0")
    print(code)

    # The shell expands the command, so the pipe and the redirection ride the
    # shell the way they do in CPython.
    code = os.system("echo piped | tr a-z A-Z > " + path)
    print(code == 0)
    with open(path) as handle:
        print(handle.read())

    os.remove(path)


main()
