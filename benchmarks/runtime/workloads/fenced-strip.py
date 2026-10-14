"""str.encode with an encoding argument is still a fence, so this workload is not timed."""


def clean(name: str) -> bytes:
    return name.encode("ascii")
