class Sender:
    def send(self, msg: str) -> str:
        return "sent:" + msg


class Logging(Sender):
    def send(self, msg: str) -> str:
        return "log:" + msg


def factory() -> Sender:
    return Logging()


def main() -> None:
    made: Sender = factory()
    print(made.send("a"))
    direct: Sender = Logging()
    print(direct.send("b"))
    made = factory()
    print(made.send("c"))
