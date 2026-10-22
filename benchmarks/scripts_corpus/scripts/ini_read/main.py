"""Print the sections and keys of an INI style file."""

import sys


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: ini_read FILE\n")
        return 2
    section: str = "DEFAULT"
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    with open(sys.argv[1]) as handle:
        for line in handle:
            text: str = line.rstrip("\n")
            if text == "" or text.startswith("#") or text.startswith(";"):
                continue
            if text.startswith("[") and text.endswith("]"):
                section = text[1:-1]
                if section not in sections:
                    sections[section] = []
                    order.append(section)
                continue
            if "=" not in text:
                continue
            parts: list[str] = text.split("=", 1)
            sections[section].append(f"{parts[0].strip()}={parts[1].strip()}")
    for name in order:
        print(f"[{name}]")
        for entry in sections[name]:
            print(entry)
    print(f"{len(order)} sections")
    return 0


if __name__ == "__main__":
    sys.exit(main())
