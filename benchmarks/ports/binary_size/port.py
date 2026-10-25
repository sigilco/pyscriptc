#!/usr/bin/env python3
"""
Compare stripped ReleaseSafe fx binaries.
"""

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MIB: int = 1_048_576
DEFAULT_WARNING_BYTES: int = 52_429
SHA_PATTERN: re.Pattern = re.compile(r"^[0-9a-f]{40}$")
TARGET_PATTERN: re.Pattern = re.compile(r"^[A-Za-z0-9_.-]+$")
SEGMENT_PATTERN: re.Pattern = re.compile(r"^Segment\s+(\S+):\s+(\d+)")
SECTION_PATTERN: re.Pattern = re.compile(r"^Section\s+(\S+):\s+(\d+)")
ELF_SECTION_PATTERN: re.Pattern = re.compile(r"^(\S+)\s+(\d+)\s+(?:0x)?[0-9A-Fa-f]+$")


class BinarySizeError(RuntimeError):
    pass


@dataclass
class ArtifactEvidence:
    source_sha: str
    sha256: str
    size_bytes: int
    size_mib: float


@dataclass
class DeltaInfo:
    direction: str
    percent: float
    size_bytes: int
    size_mib: float


@dataclass
class Report:
    schema_version: int
    status: str
    target: str
    warning_threshold_bytes: int
    base: ArtifactEvidence
    head: ArtifactEvidence
    delta: DeltaInfo
    segment_deltas_bytes: dict[str, int]
    section_deltas_bytes: dict[str, int]


@dataclass
class DeltaItem:
    name: str
    value: int


def sha256_file(path: Path) -> str:
    data: bytes = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def artifact_evidence(path: Path, source_sha: str) -> ArtifactEvidence:
    if not path.is_file():
        raise BinarySizeError(f"binary is missing or empty: {path}")
    size_bytes: int = path.stat().st_size
    if size_bytes <= 0:
        raise BinarySizeError(f"binary is missing or empty: {path}")
    size_mib: float = float(size_bytes) / float(MIB)
    return ArtifactEvidence(
        source_sha=source_sha,
        sha256=sha256_file(path),
        size_bytes=size_bytes,
        size_mib=size_mib,
    )


def parse_macho_sections(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    if not path.is_file():
        raise BinarySizeError(f"section report does not exist: {path}")
    segments: dict[str, int] = {}
    sections: dict[str, int] = {}
    seen_segments: set[str] = set()
    current_segment: str = ""
    lines: list[str] = path.read_text().splitlines()
    for raw_line in lines:
        line: str = raw_line.strip()
        segment_match: re.Match | None = SEGMENT_PATTERN.match(line)
        if segment_match is not None:
            seg_val: str | None = segment_match.group(1)
            if seg_val is not None:
                current_segment = seg_val
                if current_segment in seen_segments:
                    raise BinarySizeError(
                        "section report contains duplicate segment " + current_segment + ": " + str(path)
                    )
                seen_segments.add(current_segment)
                if current_segment != "__PAGEZERO":
                    size_str: str | None = segment_match.group(2)
                    if size_str is not None:
                        segments[current_segment] = int(size_str)
            continue
        section_match: re.Match | None = SECTION_PATTERN.match(line)
        if section_match is not None:
            if len(current_segment) > 0 and current_segment != "__PAGEZERO":
                sec_name: str | None = section_match.group(1)
                sec_size_str: str | None = section_match.group(2)
                if sec_name is not None:
                    if sec_size_str is not None:
                        key: str = current_segment + "." + sec_name
                        if key in sections:
                            raise BinarySizeError("section report contains duplicate section " + key + ": " + str(path))
                        sections[key] = int(sec_size_str)
    if len(segments) == 0:
        raise BinarySizeError(f"section report contains no Mach-O segments: {path}")
    if "__TEXT" not in segments:
        raise BinarySizeError(f"section report is missing __TEXT segment: {path}")
    return segments, sections


def parse_elf_sections(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    if not path.is_file():
        raise BinarySizeError(f"section report does not exist: {path}")
    sections: dict[str, int] = {}
    lines: list[str] = path.read_text().splitlines()
    for raw_line in lines:
        sm: re.Match | None = ELF_SECTION_PATTERN.match(raw_line.strip())
        if sm is not None:
            name_val: str | None = sm.group(1)
            size_val: str | None = sm.group(2)
            if name_val is not None:
                if size_val is not None:
                    if name_val in sections:
                        raise BinarySizeError(
                            "section report contains duplicate section " + name_val + ": " + str(path)
                        )
                    sections[name_val] = int(size_val)
    if len(sections) == 0:
        raise BinarySizeError(f"section report contains no ELF sections: {path}")
    if ".text" not in sections:
        raise BinarySizeError(f"section report is missing .text section: {path}")
    empty_segments: dict[str, int] = {}
    return empty_segments, sections


def parse_sections(
    path: Path,
    target: str,
) -> tuple[dict[str, int], dict[str, int]]:
    if target.endswith("-macos"):
        return parse_macho_sections(path)
    if target.endswith("-linux"):
        return parse_elf_sections(path)
    raise BinarySizeError(f"unsupported target: {target}")


def deltas(base: dict[str, int], head: dict[str, int]) -> dict[str, int]:
    all_keys: set[str] = set()
    for k in base.keys():
        all_keys.add(k)
    for k in head.keys():
        all_keys.add(k)
    res: dict[str, int] = {}
    sorted_keys: list[str] = sorted(list(all_keys))
    for k in sorted_keys:
        h_val: int = head.get(k, 0)
        b_val: int = base.get(k, 0)
        diff: int = h_val - b_val
        if diff != 0:
            res[k] = diff
    return res


def build_report(
    base_binary: Path,
    head_binary: Path,
    base_sections: Path,
    head_sections: Path,
    base_sha: str,
    head_sha: str,
    target: str,
    warning_bytes: int,
) -> Report:
    if SHA_PATTERN.match(base_sha) is None:
        raise BinarySizeError("source SHAs must be lowercase 40-character hex values")
    if SHA_PATTERN.match(head_sha) is None:
        raise BinarySizeError("source SHAs must be lowercase 40-character hex values")
    if TARGET_PATTERN.match(target) is None:
        raise BinarySizeError(f"invalid target: '{target}'")
    if warning_bytes <= 0:
        raise BinarySizeError("warning threshold must be positive")

    base: ArtifactEvidence = artifact_evidence(base_binary, base_sha)
    head: ArtifactEvidence = artifact_evidence(head_binary, head_sha)
    base_segments, base_section_values = parse_sections(base_sections, target)
    head_segments, head_section_values = parse_sections(head_sections, target)
    delta_bytes: int = head.size_bytes - base.size_bytes
    direction: str = "unchanged"
    if delta_bytes > 0:
        direction = "increase"
    elif delta_bytes < 0:
        direction = "decrease"

    status_str: str = "ok"
    if delta_bytes >= warning_bytes:
        status_str = "warning"

    pct: float = (float(delta_bytes) / float(base.size_bytes)) * 100.0
    delta_mib: float = float(delta_bytes) / float(MIB)
    delta_info: DeltaInfo = DeltaInfo(
        direction=direction,
        percent=pct,
        size_bytes=delta_bytes,
        size_mib=delta_mib,
    )
    return Report(
        schema_version=1,
        status=status_str,
        target=target,
        warning_threshold_bytes=warning_bytes,
        base=base,
        head=head,
        delta=delta_info,
        segment_deltas_bytes=deltas(base_segments, head_segments),
        section_deltas_bytes=deltas(base_section_values, head_section_values),
    )


def signed_bytes(value: int) -> str:
    return f"{value:+,} bytes"


def sort_delta_items(items: list[DeltaItem]) -> list[DeltaItem]:
    res: list[DeltaItem] = []
    for i in range(len(items)):
        res.append(items[i])
    n: int = len(res)
    for i in range(n):
        for j in range(0, n - i - 1):
            left: DeltaItem = res[j]
            right: DeltaItem = res[j + 1]
            swap: bool = False
            l_abs: int = abs(left.value)
            r_abs: int = abs(right.value)
            if l_abs < r_abs:
                swap = True
            elif l_abs == r_abs and left.name > right.name:
                swap = True
            if swap:
                res[j] = right
                res[j + 1] = left
    return res


def append_delta_table(
    lines: list[str],
    heading: str,
    values: dict[str, int],
    limit: int = 0,
) -> None:
    lines.extend(["", heading, ""])
    items: list[DeltaItem] = []
    for k, v in values.items():
        items.append(DeltaItem(name=k, value=v))
    ranked: list[DeltaItem] = sort_delta_items(items)
    if limit > 0 and len(ranked) > limit:
        ranked = ranked[:limit]
    if len(ranked) == 0:
        lines.append("No changes detected.")
        return
    lines.extend(["| Name | Change in bytes |", "| --- | ---: |"])
    for idx in range(len(ranked)):
        item: DeltaItem = ranked[idx]
        lines.append(f"| `{item.name}` | {item.value:+,} |")


def markdown_report(report: Report) -> str:
    base: ArtifactEvidence = report.base
    head: ArtifactEvidence = report.head
    delta: DeltaInfo = report.delta
    status: str = report.status
    direction: str = delta.direction

    signal: str = "The increase is within the informational warning threshold."
    if status == "warning":
        wb: int = report.warning_threshold_bytes
        wb_mib: float = float(wb) / float(MIB)
        signal = (
            f"Review recommended: the increase meets the informational threshold of {wb:,} bytes ({wb_mib:.6f} MiB)."
        )
    elif direction == "decrease":
        signal = "The PR binary is smaller than the base binary."
    elif direction == "unchanged":
        signal = "The PR and base binaries have the same file size."

    lines: list[str] = [
        "# Binary size",
        "",
        "This comparison is informational. Release PGSO qualification remains authoritative.",
        "",
        f"Target: `{report.target}`",
        "",
        "| Artifact | Bytes | MiB | Source | SHA-256 |",
        "| --- | ---: | ---: | --- | --- |",
        (f"| Base | {base.size_bytes:,} | {base.size_mib:.6f} | `{base.source_sha}` | `{base.sha256}` |"),
        (f"| PR | {head.size_bytes:,} | {head.size_mib:.6f} | `{head.source_sha}` | `{head.sha256}` |"),
        "",
        (f"Change: **{signed_bytes(delta.size_bytes)}** ({delta.size_mib:+.6f} MiB, {delta.percent:+.3f}%)."),
        "",
        signal,
    ]
    append_delta_table(lines, "## Segment changes", report.segment_deltas_bytes)
    append_delta_table(
        lines,
        "## Largest section changes",
        report.section_deltas_bytes,
        limit=10,
    )
    return "\n".join(lines) + "\n"


def report_to_json(report: Report) -> str:
    lines: list[str] = ["{"]
    # base
    lines.append('  "base": {')
    lines.append('    "sha256": ' + json.dumps(report.base.sha256) + ",")
    lines.append('    "size_bytes": ' + str(report.base.size_bytes) + ",")
    lines.append('    "size_mib": ' + json.dumps(report.base.size_mib) + ",")
    lines.append('    "source_sha": ' + json.dumps(report.base.source_sha))
    lines.append("  },")
    # delta
    lines.append('  "delta": {')
    lines.append('    "direction": ' + json.dumps(report.delta.direction) + ",")
    lines.append('    "percent": ' + json.dumps(report.delta.percent) + ",")
    lines.append('    "size_bytes": ' + str(report.delta.size_bytes) + ",")
    lines.append('    "size_mib": ' + json.dumps(report.delta.size_mib))
    lines.append("  },")
    # head
    lines.append('  "head": {')
    lines.append('    "sha256": ' + json.dumps(report.head.sha256) + ",")
    lines.append('    "size_bytes": ' + str(report.head.size_bytes) + ",")
    lines.append('    "size_mib": ' + json.dumps(report.head.size_mib) + ",")
    lines.append('    "source_sha": ' + json.dumps(report.head.source_sha))
    lines.append("  },")
    # schema_version
    lines.append('  "schema_version": ' + str(report.schema_version) + ",")
    # section_deltas_bytes
    if len(report.section_deltas_bytes) == 0:
        lines.append('  "section_deltas_bytes": {},')
    else:
        lines.append('  "section_deltas_bytes": {')
        sec_keys: list[str] = sorted(list(report.section_deltas_bytes.keys()))
        for idx in range(len(sec_keys)):
            sk: str = sec_keys[idx]
            comma: str = "," if idx < len(sec_keys) - 1 else ""
            lines.append("    " + json.dumps(sk) + ": " + str(report.section_deltas_bytes[sk]) + comma)
        lines.append("  },")
    # segment_deltas_bytes
    if len(report.segment_deltas_bytes) == 0:
        lines.append('  "segment_deltas_bytes": {},')
    else:
        lines.append('  "segment_deltas_bytes": {')
        seg_keys: list[str] = sorted(list(report.segment_deltas_bytes.keys()))
        for idx in range(len(seg_keys)):
            sgk: str = seg_keys[idx]
            comma: str = "," if idx < len(seg_keys) - 1 else ""
            lines.append("    " + json.dumps(sgk) + ": " + str(report.segment_deltas_bytes[sgk]) + comma)
        lines.append("  },")
    # status
    lines.append('  "status": ' + json.dumps(report.status) + ",")
    # target
    lines.append('  "target": ' + json.dumps(report.target) + ",")
    # warning_threshold_bytes
    lines.append('  "warning_threshold_bytes": ' + str(report.warning_threshold_bytes))
    lines.append("}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Compare stripped ReleaseSafe fx binaries")
    parser.add_argument("--base-binary", required=True)
    parser.add_argument("--head-binary", required=True)
    parser.add_argument("--base-sections", required=True)
    parser.add_argument("--head-sections", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--warning-bytes",
        type=int,
        default=52429,
    )
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-markdown", required=True)
    parser.add_argument("--github-output", default="")
    args: argparse.Namespace = parser.parse_args()
    return args


def main() -> int:
    args: argparse.Namespace = parse_args()
    b_bin: Path = Path(args.base_binary)
    h_bin: Path = Path(args.head_binary)
    b_sec: Path = Path(args.base_sections)
    h_sec: Path = Path(args.head_sections)
    b_sha: str = args.base_sha
    h_sha: str = args.head_sha
    tgt: str = args.target
    wb: int = args.warning_bytes
    try:
        report: Report = build_report(
            b_bin,
            h_bin,
            b_sec,
            h_sec,
            b_sha,
            h_sha,
            tgt,
            wb,
        )
    except BinarySizeError as error:
        print(str(error))
        return 1

    out_json_path: Path = Path(args.output_json)
    out_md_path: Path = Path(args.output_markdown)
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    out_md_path.parent.mkdir(parents=True, exist_ok=True)

    out_json_path.write_text(report_to_json(report) + "\n")
    out_md_path.write_text(markdown_report(report))

    gh_out: str = args.github_output
    if len(gh_out) > 0:
        gh_path: Path = Path(gh_out)
        gh_path.parent.mkdir(parents=True, exist_ok=True)
        is_warn: bool = report.status == "warning"
        warn_str: str = "true" if is_warn else "false"
        delta_str: str = str(report.delta.size_bytes)
        status_val: str = report.status
        gh_content: str = f"warning={warn_str}\ndelta_bytes={delta_str}\nstatus={status_val}\n"
        gh_path.write_text(gh_content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
