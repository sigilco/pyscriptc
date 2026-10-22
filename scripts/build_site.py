#!/usr/bin/env python3
"""Assemble the single pyscriptc site into `deploy/`.

Three builds, one deploy tree (the layout Cloudflare Pages publishes as
https://pyscriptc.pages.dev):

    deploy/index.html      the landing page      (site/landing/dist)
    deploy/docs/           the user docs         (dist/docs, built by docmd)
    deploy/compat/         the compat page       (dist/compat, built by compat/build.py)
    deploy/_headers        response headers      (site/_headers)
    deploy/_redirects      /docs and /compat 308 to their trailing-slash form

The three parts build to their own output first (`pnpm build`, `pnpm
docs:build`, `uv run python compat/build.py`); this script only assembles. It
is idempotent: `deploy/` is wiped on every run.

    uv run python scripts/build_site.py [--skip-landing] [--check]

Design doc: .agents/docs/compat-site.md
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"

REQUIRED = (
    "index.html",
    "_headers",
    "_redirects",
    "docs/index.html",
    "compat/index.html",
    "compat/data/compat.json",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-landing",
        action="store_true",
        help="assemble without rebuilding the Astro landing app",
    )
    parser.add_argument(
        "--skip-docs",
        action="store_true",
        help="assemble without rebuilding the docmd docs",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="build, then validate the assembled tree and exit",
    )
    return parser.parse_args(argv)


def run(cmd: list[str]) -> None:
    print(f"+ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def build_parts(args: argparse.Namespace) -> None:
    if not args.skip_landing:
        run(["pnpm", "--filter", "pyscriptc-site-landing", "build"])
    if not args.skip_docs:
        run(["pnpm", "run", "docs:build"])
    run([sys.executable, "compat/build.py"])


def part_output(part: str) -> Path:
    """Where a part's build output lives."""
    if part == "landing":
        return ROOT / "site" / "landing" / "dist"
    if part == "docs":
        return ROOT / "dist" / "docs"
    return ROOT / "dist" / "compat"


def fallback_landing() -> Path:
    """A minimal landing tree, used when the Astro build has not run.

    The landing app is a separate pnpm package. A checkout that has no
    node_modules (a python-only CI job, for instance) cannot build it, but
    the assembly still has to produce a valid tree, so the public assets
    and a placeholder index stand in for the Astro output. The real build
    replaces them.
    """
    out = ROOT / "dist" / "landing-fallback"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    public = ROOT / "site" / "landing" / "public"
    if public.is_dir():
        shutil.copytree(public, out, dirs_exist_ok=True)
    (out / "index.html").write_text(
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>pyscriptc</title>\n</head>\n<body>\n<h1>pyscriptc</h1>\n</body>\n</html>\n",
        encoding="utf-8",
    )
    return out


def assemble(parts: dict[str, Path], out: Path = DEPLOY) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    landing = parts["landing"]
    docs = parts["docs"]
    compat = parts["compat"]

    if not landing.is_dir():
        print(f"missing build output: {landing}", file=sys.stderr)
        print("falling back to the minimal landing tree", file=sys.stderr)
        landing = fallback_landing()

    for source in (docs, compat):
        if not source.is_dir():
            print(f"missing build output: {source}", file=sys.stderr)
            print("run the part builds first (or drop the --skip flags)", file=sys.stderr)
            raise SystemExit(2)

    shutil.copytree(landing, out, dirs_exist_ok=True)
    shutil.copytree(docs, out / "docs")
    shutil.copytree(compat, out / "compat")

    shutil.copy2(ROOT / "site" / "_headers", out / "_headers")
    shutil.copy2(ROOT / "site" / "_redirects", out / "_redirects")

    # _headers and _redirects only have meaning at the deploy root; a build
    # that carries its own copy would shadow the site-wide one.
    for shadow in sorted(out.glob("*/_headers")) + sorted(out.glob("*/_redirects")):
        shadow.unlink()

    return out


def validate(deploy: Path) -> int:
    problems: list[str] = []
    for rel in REQUIRED:
        if not (deploy / rel).exists():
            problems.append(f"missing {rel}")
    redirects = ""
    redirect_file = deploy / "_redirects"
    if redirect_file.is_file():
        redirects = redirect_file.read_text(encoding="utf-8")
    for line in ("/docs    /docs/    308", "/compat  /compat/  308"):
        if line not in redirects:
            problems.append(f"missing redirect rule {line!r}")
    for name in ("logo-256.png", "icon-64.png"):
        if not (deploy / name).exists():
            problems.append(f"missing landing asset {name}")
        if not (deploy / "compat" / name).exists():
            problems.append(f"missing compat asset {name}")
    # Only HTML pages are checked, and only for our own placeholders. The
    # bundled JavaScript (KaTeX, mermaid) contains `{{` as literal syntax, and
    # the llms-full.txt export carries the raw markdown of every page, so the
    # check would false-positive on both.
    for path in sorted(deploy.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for placeholder in ("{{BASE}}", "{{PYSC_COMPAT_URL}}", "{{PYSC_DOCS_URL}}"):
            if placeholder in text:
                problems.append(f"unexpanded placeholder {placeholder} in {path.relative_to(deploy)}")
    if problems:
        for problem in problems:
            print(f"site assembly: {problem}", file=sys.stderr)
        return 1
    files = sum(1 for _ in deploy.rglob("*") if _.is_file())
    try:
        shown = deploy.relative_to(ROOT)
    except ValueError:
        shown = deploy
    print(f"assembled {files} files in {shown}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    build_parts(args)
    deploy = assemble(parts={name: part_output(name) for name in ("landing", "docs", "compat")})
    return validate(deploy)


if __name__ == "__main__":
    raise SystemExit(main())
