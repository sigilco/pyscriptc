# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "colorama>=0.4.6",
# ]
# ///
from __future__ import annotations

import colorama
from colorama import Fore, Style


def format_status(label: str, is_ok: bool) -> str:
    if is_ok:
        color: str = Fore.GREEN
        text: str = "SUCCESS"
    else:
        color = Fore.RED
        text = "FAILURE"
    reset: str = Style.RESET_ALL
    return f"{label}: {color}[{text}]{reset}"


def main() -> None:
    colorama.init(strip=False)
    msg1: str = format_status("Build step", True)
    msg2: str = format_status("Lint step", False)
    print(msg1)
    print(msg2)
    print(f"{Fore.BLUE}Blue notice{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
