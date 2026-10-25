#!/usr/bin/env python3
"""
classify.py — parse a Wine launch log and emit a structured verdict.

Usage:
    python3 classify.py [logfile]

If logfile is omitted, reads the most recent file in data/launch/.
Emits JSON: {log, stage, winerosetta, signatures, hints, evidence}
"""

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LOG_DIR: str = "data/launch"

PAT_DLL: re.Pattern = re.compile(r"err:.*import_dll.*not found|err:.*loader_init.*c0000135", re.IGNORECASE)
PAT_DXVK: re.Pattern = re.compile(r"DXVK|d3d9.*initialized|MoltenVK", re.IGNORECASE)
PAT_X87: re.Pattern = re.compile(r"unhandled exception|EXCEPTION_ILLEGAL_INSTRUCTION", re.IGNORECASE)
PAT_WOWERROR: re.Pattern = re.compile(r"WowError\.exe|starting.*WowError", re.IGNORECASE)
PAT_LOGIN: re.Pattern = re.compile(r"character.select|login.screen|GlueXML|RealmList", re.IGNORECASE)
PAT_WINEROSETTA: re.Pattern = re.compile(r"winerosetta", re.IGNORECASE)


@dataclass
class Milestone:
    name: str
    pattern: re.Pattern
    stage: str
    hint: str


@dataclass
class Evidence:
    line: int
    text: str


def get_milestones() -> list[Milestone]:
    ms: list[Milestone] = [
        Milestone(
            "dll_not_found",
            PAT_DLL,
            "dll_missing",
            (
                "A required DLL is missing (c0000135). Check libDllLdr.dll,"
                " mods/winerosetta.dll. libSiliconPatch.dll is optional."
            ),
        ),
        Milestone(
            "dxvk_init",
            PAT_DXVK,
            "graphics_init",
            "DXVK/MoltenVK initialized \u2014 graphics layer is up.",
        ),
        Milestone(
            "x87_exception",
            PAT_X87,
            "x87_crash",
            ("Unhandled x87/illegal-instruction exception \u2014 winerosetta VEH may not cover this opcode."),
        ),
        Milestone(
            "wowerror",
            PAT_WOWERROR,
            "crashed",
            "WoW crashed into WowError.exe.",
        ),
        Milestone(
            "login_screen",
            PAT_LOGIN,
            "login_screen",
            ("Reached login/character-select screen \u2014 launch successful."),
        ),
    ]
    return ms


def find_latest_log() -> Path | None:
    log_dir: Path = Path(DEFAULT_LOG_DIR)
    if not log_dir.exists():
        return None
    names: list[str] = [str(p) for p in log_dir.glob("*.log")]
    sorted_names: list[str] = sorted(names, reverse=True)
    if len(sorted_names) == 0:
        return None
    return Path(sorted_names[0])


def format_verdict(
    log_path: str,
    stage: str,
    winerosetta: str,
    signatures: list[str],
    hints: list[str],
    evidence: dict[str, Evidence],
) -> str:
    lines: list[str] = ["{"]
    lines.append('  "log": ' + json.dumps(log_path) + ",")
    lines.append('  "stage": ' + json.dumps(stage) + ",")
    lines.append('  "winerosetta": ' + json.dumps(winerosetta) + ",")

    if len(signatures) == 0:
        lines.append('  "signatures": [],')
    else:
        sig_str: str = json.dumps(signatures, indent=2)
        sig_lines: list[str] = sig_str.splitlines()
        lines.append('  "signatures": [')
        for sl in sig_lines[1:-1]:
            lines.append("  " + sl)
        lines.append("  ],")

    if len(hints) == 0:
        lines.append('  "hints": [],')
    else:
        h_str: str = json.dumps(hints, indent=2).replace("\u2014", "\\u2014")
        h_lines: list[str] = h_str.splitlines()
        lines.append('  "hints": [')
        for hl in h_lines[1:-1]:
            lines.append("  " + hl)
        lines.append("  ],")

    if len(evidence) == 0:
        lines.append('  "evidence": {}')
    else:
        lines.append('  "evidence": {')
        ev_keys: list[str] = list(evidence.keys())
        for idx in range(len(ev_keys)):
            k: str = ev_keys[idx]
            ev: Evidence = evidence[k]
            comma: str = "," if idx < len(ev_keys) - 1 else ""
            lines.append("    " + json.dumps(k) + ": {")
            lines.append('      "line": ' + str(ev.line) + ",")
            lines.append('      "text": ' + json.dumps(ev.text))
            lines.append("    }" + comma)
        lines.append("  }")

    lines.append("}")
    return "\n".join(lines)


def classify(log_path: Path) -> str:
    text: str = log_path.read_text(encoding="utf-8")
    lines: list[str] = text.splitlines()

    matched_names: list[str] = []
    stage: str = "unknown"
    hints: list[str] = []
    evidence: dict[str, Evidence] = {}

    milestones: list[Milestone] = get_milestones()
    for m_idx in range(len(milestones)):
        sig: Milestone = milestones[m_idx]
        res: re.Match | None = sig.pattern.search(text)
        if res is not None:
            matched_names.append(sig.name)
            stage = sig.stage
            hints.append(sig.hint)
            for i, line in enumerate(lines):
                line_res: re.Match | None = sig.pattern.search(line)
                if line_res is not None:
                    evidence[sig.name] = Evidence(i + 1, line.strip())
                    break

    rosetta_res: re.Match | None = PAT_WINEROSETTA.search(text)
    winerosetta_loaded: bool = rosetta_res is not None
    injection_hint: str = ""
    winerosetta_status: str = ""
    if winerosetta_loaded:
        winerosetta_status = "loaded"
        injection_hint = "winerosetta VEH is active."
    else:
        winerosetta_status = "absent"
        injection_hint = (
            "winerosetta did not appear in log \u2014 DivxDecoder bootstrap may"
            " have failed. Delete DivxDecoder.dll.bak and re-run the launcher to"
            " re-patch."
        )

    all_hints: list[str] = hints + [injection_hint]
    return format_verdict(
        str(log_path),
        stage,
        winerosetta_status,
        matched_names,
        all_hints,
        evidence,
    )


def main() -> None:
    has_arg: bool = len(sys.argv) > 1
    target_path: Path | None = None
    if has_arg:
        target_path = Path(sys.argv[1])
    else:
        target_path = find_latest_log()

    if target_path is not None:
        if not target_path.exists():
            err_dict: dict[str, str] = {
                "error": "No log file found",
                "log_dir": str(DEFAULT_LOG_DIR),
            }
            print(json.dumps(err_dict))
            sys.exit(1)
        print(classify(target_path))
    else:
        err_dict2: dict[str, str] = {
            "error": "No log file found",
            "log_dir": str(DEFAULT_LOG_DIR),
        }
        print(json.dumps(err_dict2))
        sys.exit(1)


if __name__ == "__main__":
    main()
