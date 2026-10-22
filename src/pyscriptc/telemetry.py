"""Pseudonymous usage telemetry via PostHog (EU host).

What is sent: a short event payload (subcommand, version, python major.minor, platform, and
for `compile` a duration, an outcome, the fence count, the diagnostic family
names behind those fences and the checker/emit/driver flags;
for `coverage` the aggregate fence and definition counts). The id is a random
uuid4 stored in `telemetry-id` next to the config file; no hardware is read.
Every event also carries `$ip: null`, `$geoip_disable` and `$process_person_profile: false`.
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

First-run notice: printed once on stderr when stderr is a TTY, outside CI and
while telemetry is enabled; it records `notice_seen = true`. No event is sent
before the notice was shown, so non-interactive use never sends anything until
a TTY run has shown it.

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

import json
import os
import sys
import threading
import urllib.request
import uuid
from pathlib import Path

POSTHOG_HOST = "https://eu.i.posthog.com"

_TIMEOUT_S = 1.5

try:  # release builds stamp this file; see the module docstring
    from pyscriptc._posthog_token import TOKEN as _BAKED_TOKEN
except ImportError:
    _BAKED_TOKEN = ""


# Sent with every event so PostHog does not keep the client IP or a person profile.
# `$ip: null` and `$geoip_disable` are per-event requests; the authoritative control is the
# PostHog project setting "Discard client IP data" (see docs/project/privacy.md).
_PRIVACY_PROPERTIES = {"$ip": None, "$geoip_disable": True, "$process_person_profile": False}


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


def _id_path() -> Path:
    return _config_path().with_name("telemetry-id")


def _install_id() -> str:
    """Random per-install id (uuid4), created on first send and kept in `telemetry-id` (mode 0600).

    Nothing is derived from the machine: no machine id, MAC address or hostname is read.
    Deleting the file (or `pyscriptc telemetry reset-id`) makes the next send start a new id.
    """
    path = _id_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return str(uuid.UUID(existing))
    except OSError, ValueError:
        pass
    new_id = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_id + "\n")
        os.chmod(path, 0o600)
    except OSError:
        pass
    return new_id


def ci() -> bool:
    return bool(os.environ.get("CI"))


# Consent model switch. False (shipped default): on unless opted out, after a first-run notice.
# True: off until the user runs `pyscriptc telemetry enable` (consent); the notice then asks instead of informs.
# The owner decides; see the PR description. Flip only this constant.
OPT_IN = False


def _blocked() -> bool:
    return ci() or _truthy("PYSC_NO_TELEMETRY") or _truthy("DO_NOT_TRACK") or not _token()


def enabled() -> bool:
    if _blocked():
        return False
    setting = _read_config().get("telemetry")
    if setting == "false":
        return False
    return setting == "true" or not OPT_IN


NOTICE_URL = "https://github.com/sigilco/pyscriptc/blob/master/docs/project/telemetry.md"

PRIVACY_URL = "https://github.com/sigilco/pyscriptc/blob/master/docs/project/privacy.md"

NOTICE = (
    "pyscriptc sends usage metrics: command name, pyscriptc/Python/OS versions, compile outcome, "
    "duration, fence counts and diagnostic family names, plus a random install id (no hardware ids, "
    "no IP address stored). Never source code, file paths or names. Data goes to PostHog (EU) and is kept 90 days.\n"
    "Opt out: PYSC_NO_TELEMETRY=1 or `pyscriptc telemetry disable`; new id: `pyscriptc telemetry reset-id`.\n"
    "Details: " + NOTICE_URL + "  Privacy: " + PRIVACY_URL
)

NOTICE_OPT_IN = (
    "pyscriptc can send anonymous-by-design usage metrics (command name, versions, compile outcome, duration, "
    "fence counts and diagnostic family names, a random install id; never source code, paths or names, no IP stored). "
    "It is OFF until you consent: run `pyscriptc telemetry enable` to turn it on.\n"
    "Details: " + NOTICE_URL + "  Privacy: " + PRIVACY_URL
)

_notice_shown_now = False


def maybe_print_notice() -> None:
    """Print the first-run notice once, on an interactive stderr outside CI.

    Nothing is sent until the notice was shown (see `track`). A non-interactive
    run never shows it, so it never sends either.
    """
    global _notice_shown_now
    if _blocked() or _read_config().get("telemetry") == "false":
        return
    if OPT_IN and _read_config().get("telemetry") != "true":
        if _read_config().get("notice_seen") == "true" or not _stderr_tty():
            return
        print(NOTICE_OPT_IN, file=sys.stderr)
        _mark_notice_seen()
        return
    if _read_config().get("notice_seen") == "true":
        return
    if not _stderr_tty():
        return
    print(NOTICE, file=sys.stderr)
    _notice_shown_now = True
    _mark_notice_seen()


def _stderr_tty() -> bool:
    try:
        return bool(sys.stderr.isatty())
    except AttributeError, ValueError:
        return False


def _mark_notice_seen() -> None:
    config = _read_config()
    config["notice_seen"] = "true"
    try:
        _write_config(config)
    except OSError:
        pass


def _notice_acknowledged() -> bool:
    return _notice_shown_now or _read_config().get("notice_seen") == "true"


def track(event: str, properties: dict | None = None) -> None:
    """Queue one event on a daemon thread. Every failure is swallowed."""
    if not enabled() or not _notice_acknowledged():
        return
    payload = {
        "api_key": _token(),
        "event": event,
        "distinct_id": _install_id(),
        "properties": {**(properties or {}), **_PRIVACY_PROPERTIES},
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
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": sys.platform,
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
        definitions=definitions,
        fencedDefinitions=fenced_definitions,
        fences=int(summary.get("fences", 0)),
        rootFences=int(summary.get("rootFences", 0)),
        byFamily=families,
    )
    return props


def telemetry_cmd(action: str) -> int:
    """Implement `pyscriptc telemetry disable|enable|status|reset-id`."""
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
        elif OPT_IN and config.get("telemetry") != "true":
            print("reason: opt-in build, run `pyscriptc telemetry enable` to consent")
        return 0
    if action == "reset-id":
        try:
            _id_path().unlink(missing_ok=True)
        except OSError as exc:
            print(f"cannot remove {_id_path()}: {exc}", file=sys.stderr)
            return 1
        print(f"install id removed ({_id_path()}); a new random id is created on the next send")
        return 0
    if action not in ("enable", "disable"):
        print("usage: pyscriptc telemetry disable|enable|status|reset-id", file=sys.stderr)
        return 2
    config = _read_config()
    config["telemetry"] = "true" if action == "enable" else "false"
    if action == "enable":
        config["notice_seen"] = "true"  # explicit consent counts as having seen the notice
    try:
        _write_config(config)
    except OSError as exc:
        print(f"cannot write {_config_path()}: {exc}", file=sys.stderr)
        return 1
    print(f"telemetry {'enabled' if action == 'enable' else 'disabled'} in {_config_path()}")
    return 0
