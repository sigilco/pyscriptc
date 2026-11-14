from __future__ import annotations

import email
from email.message import Message


def main() -> None:
    m: Message = email.message_from_string("Subject: hi\n\nbody")
    print(m.get("Subject"))


if __name__ == "__main__":
    main()
