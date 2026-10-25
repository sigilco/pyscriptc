# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "click>=8.1.0",
# ]
# ///
from __future__ import annotations

import click
from click.testing import CliRunner, Result


@click.command()
@click.option("--count", default=1, help="Number of greetings.")
@click.option("--name", prompt="Your name", help="The person to greet.")
def hello(count: int, name: str) -> None:
    """Simple program that greets NAME for a total of COUNT times."""
    for _ in range(count):
        click.echo(f"Hello, {name}!")


def invoke_cli(count: int, name: str) -> str:
    runner: CliRunner = CliRunner()
    result: Result = runner.invoke(hello, ["--count", str(count), "--name", name])
    return result.output.strip()


def main() -> None:
    output: str = invoke_cli(2, "World")
    print("CLI Output:")
    print(output)


if __name__ == "__main__":
    main()
