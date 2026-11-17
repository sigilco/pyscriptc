import ssl


def run() -> int:
    ctx = ssl.create_default_context()
    print(ctx is not None)
    return 0


run()
