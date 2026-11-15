class Sender:
    def send(self, msg: str) -> str:
        return "sent:" + msg


class Logging(Sender):
    def send(self, msg: str) -> str:
        return "log:" + msg


def deliver(s: Sender, msg: str) -> str:
    return s.send(msg)


print(deliver(Logging(), "x"))
