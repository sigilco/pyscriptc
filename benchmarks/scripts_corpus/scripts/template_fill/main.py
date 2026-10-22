"""Replace template placeholders with key=value pairs."""

import sys


def render(template: str, values: dict[str, str]) -> str:
    out: str = template
    for key in sorted(values.keys(), reverse=True):
        out = out.replace("{" + key + "}", values[key])
    return out


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("usage: template_fill FILE [KEY=VALUE ...]\n")
        return 2
    values: dict[str, str] = {}
    for arg in sys.argv[2:]:
        parts: list[str] = arg.split("=", 1)
        if len(parts) == 2:
            values[parts[0]] = parts[1]
    with open(sys.argv[1]) as handle:
        for line in handle:
            sys.stdout.write(render(line, values))
    return 0


if __name__ == "__main__":
    sys.exit(main())
