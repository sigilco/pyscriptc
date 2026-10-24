import yaml

def main() -> None:
    d: dict[str, object] = yaml.safe_load("a: 1\nb: [x, y]\n")
    print(d)
    r: str = yaml.safe_load("t: 1:30\n")
    print(r)

main()
