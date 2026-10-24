class AppError(Exception):
    pass

def make() -> int:
    try:
        raise AppError("no")
    except AppError:
        print("caught AppError")
    try:
        raise AppError("no2")
    except Exception:
        print("caught Exception")
    return 0

print(make())
