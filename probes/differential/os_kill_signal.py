# Issue #1244: the process-control surface a CLI watchdog needs. `os.kill`
# and `os.killpg` lower over the ps_os_kill runtime helpers, `signal` carries
# the six POSIX constants, and the OSError subclasses follow CPython's errno
# translation (ESRCH -> ProcessLookupError, EPERM -> PermissionError). The
# program kills its own process group and itself with SIGCONT (a no-op for a
# running, untracked process), so both sides run the same shape byte-for-byte;
# the sleeping-child watchdog scenario over subprocess.run's timeout keyword
# is #1223's lane and stays out of this probe.
import os
import signal


def main() -> None:
    os.kill(os.getpid(), signal.SIGCONT)
    print("continued")

    os.killpg(0, signal.SIGCONT)
    print("group continued")

    try:
        os.kill(999999, signal.SIGTERM)
    except ProcessLookupError:
        print("no-such-process")

    try:
        os.killpg(999999, signal.SIGKILL)
    except ProcessLookupError:
        print("no-such-group")

    print(signal.SIGKILL > 0, signal.SIGTERM > 0, signal.SIGINT > 0)
    print(signal.SIGCONT > 0, signal.SIGUSR1 > 0, signal.SIGUSR2 > 0)
    print(signal.SIGTERM == 15)


main()
