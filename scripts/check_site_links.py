#!/usr/bin/env python3
"""Check the links of the assembled site.

Runs over `deploy/` (the tree Cloudflare Pages publishes) and over the site
sources, so a broken link fails CI before it ships. It checks three kinds of
link:

| kind | how |
| --- | --- |
| relative file links in HTML | the target must exist in the deploy tree |
| absolute site paths in HTML | the target must exist in the deploy tree, after the site map |
| external links | not fetched, only the shape is checked, so the check stays offline and fast |

The docs pages are a docmd build with hashed assets, so the HTML is parsed
with the standard library (`html.parser`) and no third-party dependency.

    uv run python scripts/check_site_links.py [--deploy deploy] [--external]

Design doc: .agents/docs/compat-site.md
"""

from __future__ import annotations

import argparse
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]

# The site map: a URL path prefix and the directory under deploy/ that serves
# it. The landing page is the root, the docs live under /docs, the compat
# page under /compat.
SITE_MAP = (
    ("/docs/", "docs"),
    ("/compat/", "compat"),
)


class LinkCollector(HTMLParser):
    """Collect href and src attributes from one HTML file."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in ("href", "src") and value:
                self.links.append(value)


def site_path_for(path: str) -> str | None:
    """The deploy directory that serves a site path, or None."""
    for prefix, directory in SITE_MAP:
        if path.startswith(prefix):
            return directory
    return None


def resolve(deploy: Path, page: Path, link: str) -> tuple[bool, str]:
    """Check one link. Returns (ok, reason)."""
    parsed = urlparse(link)
    if parsed.scheme in ("http", "https", "mailto", "data", "javascript"):
        return True, ""
    if parsed.scheme:
        return True, ""

    target = unquote(parsed.path)
    if not target:
        return True, ""

    def ok(candidate: Path, shown: str) -> tuple[bool, str]:
        return candidate.exists(), shown

    if target.startswith("/"):
        # An absolute site path. The site map decides which directory serves
        # it; the root of the deploy tree is the landing page.
        if target == "/":
            candidate = deploy / "index.html"
            return ok(candidate, target)
        directory = site_path_for(target)
        if directory is None:
            # Not under a known prefix: it must exist at the deploy root.
            candidate = deploy / target.lstrip("/")
            if candidate.is_dir():
                candidate = candidate / "index.html"
            return ok(candidate, target)
        relative = target[len(directory) + 1 :].lstrip("/")
        candidate = deploy / directory / relative if relative else deploy / directory
        if candidate.is_dir():
            candidate = candidate / "index.html"
        return ok(candidate, target)

    # A relative link, resolved against the page that carries it.
    candidate = (page.parent / target).resolve()
    try:
        candidate.relative_to(deploy.resolve())
    except ValueError:
        return True, ""
    if candidate.is_dir():
        candidate = candidate / "index.html"
    return ok(candidate, link)


def external_shape_ok(link: str) -> bool:
    """A cheap shape check for external links: no spaces, a plausible scheme."""
    parsed = urlparse(link)
    if parsed.scheme in ("mailto", "data", "javascript"):
        return True
    if parsed.scheme not in ("http", "https"):
        return False
    if " " in link:
        return False
    return bool(parsed.netloc)


def check_deploy(deploy: Path, check_external: bool) -> list[str]:
    problems: list[str] = []
    pages = sorted(deploy.rglob("*.html"))
    if not pages:
        return [f"no HTML page found under {deploy}"]
    for page in pages:
        collector = LinkCollector()
        collector.feed(page.read_text(encoding="utf-8", errors="replace"))
        for link in collector.links:
            if link.startswith("#"):
                continue
            ok, detail = resolve(deploy, page, link)
            if not ok:
                problems.append(f"{page.relative_to(deploy)}: broken link {link} ({detail})")
                continue
            if check_external and urlparse(link).scheme in ("http", "https"):
                if not external_shape_ok(link):
                    problems.append(f"{page.relative_to(deploy)}: malformed external link {link}")
    return problems


def check_sources() -> list[str]:
    """Check the links in the site sources that are not HTML pages."""
    problems: list[str] = []
    compat = ROOT / "compat" / "site" / "index.html"
    if compat.exists():
        collector = LinkCollector()
        collector.feed(compat.read_text(encoding="utf-8"))
        for link in collector.links:
            if link.startswith("#") or link.startswith("{{"):
                continue
            if link.startswith("http") or link.startswith("/"):
                continue
            if not (compat.parent / link).exists():
                problems.append(f"compat/site/index.html: broken relative link {link}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", type=Path, default=ROOT / "deploy")
    parser.add_argument(
        "--external",
        action="store_true",
        help="also check the shape of external links (still no network access)",
    )
    args = parser.parse_args(argv)

    if not args.deploy.is_dir():
        print(f"deploy tree not found: {args.deploy}", file=sys.stderr)
        print("run: mise run site:build", file=sys.stderr)
        return 2

    problems = check_deploy(args.deploy, args.external) + check_sources()
    if problems:
        for problem in problems:
            print(f"link check: {problem}", file=sys.stderr)
        print(f"{len(problems)} broken link(s)", file=sys.stderr)
        return 1

    pages = sum(1 for _ in args.deploy.rglob("*.html"))
    print(f"link check: {pages} pages, no broken link")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
