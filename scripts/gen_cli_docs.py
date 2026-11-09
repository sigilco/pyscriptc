"""Generate the flag tables in docs/reference/cli.md from the argparse parser.

The page keeps its hand-written prose; everything between the
`<!-- gen:flags -->` and `<!-- /gen:flags -->` markers is regenerated from the
parser that `pyscriptc` builds, so the tables never drift from the CLI.

Run: python scripts/gen_cli_docs.py [--check] [--out PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pyscriptc.cli import build_parser  # noqa: E402

DEFAULT_OUT = ROOT / "docs" / "reference" / "cli.md"

BEGIN = "<!-- gen:flags -->"
END = "<!-- /gen:flags -->"

# Subcommands with a nested command of their own.
NESTED = {"embed": "fetch"}


def _flag_label(action: argparse.Action) -> str:
    if not action.option_strings:
        return ""
    parts = []
    for opt in action.option_strings:
        if isinstance(action, argparse._StoreTrueAction):
            parts.append(f"`{opt}`")
        elif action.nargs == "?":
            parts.append(f"`{opt}[={action.metavar or 'VALUE'}]`")
        elif action.metavar:
            parts.append(f"`{opt} {action.metavar}`")
        else:
            parts.append(f"`{opt} {action.dest.upper()}`")
    return ", ".join(parts)


def _value_column(action: argparse.Action) -> str:
    if action.choices:
        return "`" + "\\|".join(str(c) for c in action.choices) + "`"
    if isinstance(action, argparse._StoreTrueAction):
        return ""
    if action.nargs == "?":
        return f"`{action.metavar or 'VALUE'}`"
    if action.metavar:
        return f"`{action.metavar}`"
    return ""


def _default_note(action: argparse.Action) -> str:
    if isinstance(action, argparse._StoreTrueAction):
        return ""
    default = action.default
    if default is None or default is argparse.SUPPRESS or default == [] or default == "":
        return ""
    if isinstance(default, bool):
        return ""
    if isinstance(default, str):
        return f"default `{default}`"
    return f"default `{default:g}`" if isinstance(default, float) else f"default `{default}`"


def _positionals(parser: argparse.ArgumentParser) -> list[str]:
    out = []
    for action in parser._actions:
        if action.option_strings or isinstance(action, argparse._SubParsersAction):
            continue
        out.append(action.metavar or action.dest.upper())
    return out


def _table(parser: argparse.ArgumentParser) -> str:
    lines: list[str] = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        if action.dest == "verbose":
            continue
        label = _flag_label(action)
        if not label:
            continue
        value = _value_column(action)
        note = _default_note(action)
        help_text = " ".join((action.help or "").split())
        if not help_text:
            help_text = {
                "output": "output path; defaults to the source stem",
                "skip_check": "skip the type-checker preflight",
            }.get(action.dest, action.dest)
        # Help strings that already spell the default out would read it twice.
        if note and f"(default {note.split('`')[1]})" in help_text:
            note = ""
        effect = f"{help_text} ({note})" if note else help_text
        lines.append(f"| {label} | {value} | {effect} |")
    if not lines:
        return ""
    return "\n".join(["| Flag | Value | Effect |", "| ---- | ----- | ------ |", *lines])


def _nested(parser: argparse.ArgumentParser) -> argparse.ArgumentParser | None:
    """The parser of a nested subcommand group, when there is one."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            first = next(iter(action.choices.values()))
            return first
    return None


def _usage(name: str, parser: argparse.ArgumentParser) -> str:
    parts = ["pyscriptc", name]
    parts.extend(_positionals(parser))
    if name in NESTED:
        parts.append(NESTED[name])
    parts.append("[options]")
    return " ".join(parts)


def render_flags() -> str:
    parser = build_parser()
    subactions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0]
    blocks: list[str] = []
    for name, sub in subactions.choices.items():
        blocks.append(f"### `{name}`\n")
        summary = " ".join((getattr(sub, "description", None) or getattr(sub, "help", None) or "").split())
        if summary:
            blocks.append(summary + "\n")
        blocks.append("```sh")
        blocks.append(_usage(name, sub))
        blocks.append("```")
        table = _table(sub)
        if table:
            blocks.append(table)
        nested = _nested(sub)
        if nested is not None:
            blocks.append("")
            blocks.append(f"#### `{name} {NESTED[name]}`")
            blocks.append("")
            blocks.append("```sh")
            blocks.append(f"pyscriptc {name} {NESTED[name]} [options]")
            blocks.append("```")
            nested_table = _table(nested)
            if nested_table:
                blocks.append(nested_table)
        blocks.append("")
    return "\n".join(blocks).rstrip()


def render() -> str:
    text = DEFAULT_OUT.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(f"{DEFAULT_OUT} is missing the {BEGIN} / {END} markers")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{render_flags()}\n{END}{tail}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit nonzero when the page is out of date")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output path (default docs/reference/cli.md)")
    args = ap.parse_args()

    content = render()
    if args.check:
        if not args.out.exists():
            print(f"{args.out} does not exist", file=sys.stderr)
            return 1
        if args.out.read_text(encoding="utf-8") != content:
            print(f"{args.out} is out of date. Run: uv run python scripts/gen_cli_docs.py", file=sys.stderr)
            return 1
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
