from __future__ import annotations
import urllib.request
def main() -> None:
    data: bytes = urllib.request.urlopen("https://example.com").read()
    print(len(data))
if __name__ == "__main__": main()
