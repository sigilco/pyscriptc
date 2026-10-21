"""Anonymous usage telemetry via PostHog.

What is sent: a short event payload (subcommand, version, python/platform, and
for `compile` a duration, an outcome, the fence count, the diagnostic family
names behind those fences, the checker/emit/driver flags and the binary size;
for `coverage` the aggregate fence counts and the covered-definition ratio).
Never sent: file names, paths, source text, per-fence identifiers (the file,
definition, line or frame a fence points at), or anything else derived from
the compiled program.

Token flow. The PostHog project token is baked in at release time, not read
from the environment at run time: the release workflow writes
`src/pyscriptc/_posthog_token.py` (gitignored, `TOKEN = "..."`) from the
`POSTHOG_PROJECT_TOKEN` repo variable before `packaging/build.sh` and before
`uv build`. Dev checkouts and contributor installs have no such file, so
`_token()` returns "" and every telemetry call is a silent no-op. For local
testing set `PYSC_POSTHOG_TOKEN`; it wins over the baked-in file.

Opt-out, first match wins:

- `CI` env var set: telemetry is off and the first-use notice never prints.
- `PYSC_NO_TELEMETRY` set to any truthy value.
- `DO_NOT_TRACK` set to any truthy value.
- `pyscriptc telemetry disable` writes `telemetry = false` to the config file.
- No token (see above): silent no-op.

Config file: `$XDG_CONFIG_HOME/pyscriptc/config.toml` (default
`~/.config/pyscriptc/config.toml`). It is a flat `key = value` TOML file; the
two keys are `telemetry` (bool) and `notice_seen` (bool).

Sending is fire-and-forget: each `track()` posts the event on a daemon thread
with a 1.5 s socket timeout and swallows every failure. Nothing here can slow
down or break a compile.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sys
import threading
import urllib.request
import uuid
from pathlib import Path

POSTHOG_HOST = "https://eu.i.posthog.com"

_SALT = b"pyscriptc-v1"
_TIMEOUT_S = 1.5

try:  # release builds stamp this file; see the module docstring
    from pyscriptc._posthog_token import TOKEN as _BAKED_TOKEN
except ImportError:
    _BAKED_TOKEN = ""


def _token() -> str:
    return os.environ.get("PYSC_POSTHOG_TOKEN", "") or _BAKED_TOKEN


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


def _config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pyscriptc" / "config.toml"


def _read_config() -> dict[str, str]:
    """Parse the flat `key = value` TOML file. Unknown lines are ignored."""
    values: dict[str, str] = {}
    try:
        for line in _config_path().read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def _write_config(values: dict[str, str]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key} = {_toml_value(value)}" for key, value in sorted(values.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toml_value(value: str) -> str:
    if value in ("true", "false"):
        return value
    return json.dumps(value)


def _machine_id() -> str:
    """Stable anonymous machine hash. Not reversible, carries no raw identity."""
    parts = [_SALT]
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            parts.append(Path(candidate).read_bytes().strip())
            break
        except OSError:
            continue
    try:
        parts.append(str(uuid.getnode()).encode())
    except Exception:
        pass
    try:
        parts.append(socket.gethostname().encode())
    except Exception:
        pass
    return hashlib.sha256(b"|".join(parts)).hexdigest()


def ci() -> bool:
    return bool(os.environ.get("CI"))


def enabled() -> bool:
    if ci() or _truthy("PYSC_NO_TELEMETRY") or _truthy("DO_NOT_TRACK"):
        return False
    if _read_config().get("telemetry") == "false":
        return False
    return bool(_token())


def maybe_print_notice() -> None:
    """One stderr line on first non-CI, opted-in invocation; never twice."""
    if not enabled():
        return
    config = _read_config()
    if config.get("notice_seen") == "true":
        return
    print(
        "pyscriptc collects anonymous usage metrics (no source/paths). "
        "Disable: PYSC_NO_TELEMETRY=1 or `pyscriptc telemetry disable`",
        file=sys.stderr,
    )
    config["notice_seen"] = "true"
    try:
        _write_config(config)
    except OSError:
        pass


def track(event: str, properties: dict | None = None) -> None:
    """Queue one event on a daemon thread. Every failure is swallowed."""
    if not enabled():
        return
    payload = {
        "api_key": _token(),
        "event": event,
        "distinct_id": _machine_id(),
        "properties": properties or {},
    }
    thread = threading.Thread(target=_post, args=(payload,), daemon=True)
    try:
        thread.start()
    except Exception:
        pass


def _post(payload: dict) -> None:
    try:
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{POSTHOG_HOST}/capture/",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S):
            pass
    except Exception:
        pass


def base_properties() -> dict:
    from pyscriptc import __version__

    return {
        "pyscriptc_version": __version__,
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "ci": ci(),
    }


def compile_properties(
    *,
    duration_ms: int,
    outcome: str,
    fence_count: int,
    fence_families: dict[str, int],
    checker: str | None = None,
    emit: str | None = None,
    driver: str | None = None,
    binary_bytes: int | None = None,
    skip_check: bool = False,
) -> dict:
    """Build the `compile.finished` property payload.

    `outcome` is one of ok|fences|check-failed|lower-error|build-error.
    `fence_families` maps a diagnostic family name to a count; callers
    must strip the dynamic "stdlib:<module>" suffix before calling.
    Never includes file names, paths, or source text.
    """
    props = dict(base_properties())
    props.update(
        duration_ms=duration_ms,
        outcome=outcome,
        fence_count=fence_count,
        fence_families=dict(fence_families),
        emit=emit,
        skip_check=skip_check,
    )
    if checker:
        props["checker"] = checker
    if driver:
        props["driver"] = driver
    if binary_bytes:
        props["binary_bytes"] = binary_bytes
    return props


def coverage_properties(summary: dict) -> dict:
    """Build the `coverage.reported` payload from a coverage summary.

    Only aggregate counts and ratios; never per-fence entries, which
    carry file/definition/frame identifiers.
    """
    definitions = int(summary.get("definitions", 0))
    fenced_definitions = int(summary.get("fencedDefinitions", 0))
    families: dict[str, int] = {}
    for family, value in (summary.get("byFamily") or {}).items():
        count = int(value.get("fences", 0)) if isinstance(value, dict) else int(value)
        # `stdlib:<module>` embeds an imported module name taken from the source.
        if family.startswith("stdlib:"):
            family = "stdlib"
        families[family] = families.get(family, 0) + count
    props = dict(base_properties())
    props.update(
        files=int(summary.get("files", 0)),
        definitions=definitions,
        fencedDefinitions=fenced_definitions,
        fences=int(summary.get("fences", 0)),
        rootFences=int(summary.get("rootFences", 0)),
        byFamily=families,
        coverage_ratio=fenced_definitions / definitions if definitions else 0.0,
    )
    return props


def telemetry_cmd(action: str) -> int:
    """Implement `pyscriptc telemetry disable|enable|status`."""
    if action == "status":
        config = _read_config()
        print(f"telemetry: {'on' if enabled() else 'off'}")
        print(f"config: {_config_path()} ({'exists' if _config_path().exists() else 'absent'})")
        if ci():
            print("reason: CI env var set")
        elif _truthy("PYSC_NO_TELEMETRY"):
            print("reason: PYSC_NO_TELEMETRY set")
        elif _truthy("DO_NOT_TRACK"):
            print("reason: DO_NOT_TRACK set")
        elif config.get("telemetry") == "false":
            print("reason: telemetry = false in config")
        elif not _token():
            print("reason: no PostHog token (dev install)")
        return 0
    if action not in ("enable", "disable"):
        print("usage: pyscriptc telemetry disable|enable|status", file=sys.stderr)
        return 2
    config = _read_config()
    config["telemetry"] = "true" if action == "enable" else "false"
    try:
        _write_config(config)
    except OSError as exc:
        print(f"cannot write {_config_path()}: {exc}", file=sys.stderr)
        return 1
    print(f"telemetry {'enabled' if action == 'enable' else 'disabled'} in {_config_path()}")
    return 0
