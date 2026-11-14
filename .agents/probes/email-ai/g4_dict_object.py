from __future__ import annotations

import json


def main() -> None:
    msg: dict[str, object] = json.loads('{"id":1,"method":"x","params":{"a":1}}')
    print(msg)


if __name__ == "__main__":
    main()
