class AppError(Exception):
    code: int

    def __init__(self, code: int) -> None:
        self.code = code


try:
    raise AppError(7)
except AppError as e:
    print(e.code)
