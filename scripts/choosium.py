#!/usr/bin/env python3
"""Choosium's browser router and narrow configuration bridge."""

from __future__ import annotations

import configparser
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlsplit

try:
    import tomllib
except ModuleNotFoundError as error:  # pragma: no cover - Omarchy ships Python 3.11+
    raise SystemExit("Choosium requires Python 3.11 or newer") from error


APP_ID = "io.github.filipechagas.choosium"
DESKTOP_ID = APP_ID + ".desktop"
LEGACY_DESKTOP_IDS = {"hyprchoosy.desktop", DESKTOP_ID}
DEFAULT_BROWSER = "chromium.desktop"
LEGACY_DEFAULT_BROWSER = "firefox"
WEB_MIME_TYPES = ("x-scheme-handler/http", "x-scheme-handler/https", "text/html")
MAX_CONFIG_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RULES = 100
MAX_VALUES_PER_RULE = 100

PREFERRED_DESKTOP_IDS = [
    "chromium.desktop",
    "google-chrome.desktop",
    "brave-browser.desktop",
    "brave-origin.desktop",
    "microsoft-edge.desktop",
    "firefox.desktop",
    "firefox-esr.desktop",
    "zen.desktop",
    "vivaldi-stable.desktop",
]

SKIPPED_PARENT_PROCESSES = {
    "bash",
    "dbus-daemon",
    "fish",
    "gio",
    "gio-launch-desktop",
    "hyprchoosy",
    "python",
    "python3",
    "sh",
    "systemd",
    "uwsm-app",
    "xdg-desktop-portal",
    "xdg-desktop-portal-gtk",
    "xdg-desktop-portal-hyprland",
    "xdg-open",
    "zsh",
}


class ChoosiumError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _xdg_home(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable, "").strip()
    path = Path(value) if value else None
    return path if path is not None and path.is_absolute() else Path.home() / fallback


def config_path() -> Path:
    override = os.environ.get("CHOOSIUM_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return _xdg_home("XDG_CONFIG_HOME", ".config") / "choosium" / "config.toml"


def legacy_config_path() -> Path:
    override = os.environ.get("HYPRCHOOSY_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return _xdg_home("XDG_CONFIG_HOME", ".config") / "hyprchoosy" / "config.toml"


def desktop_entry_path() -> Path:
    return _xdg_home("XDG_DATA_HOME", ".local/share") / "applications" / DESKTOP_ID


def default_config() -> dict[str, Any]:
    return {"default": {"browser": DEFAULT_BROWSER}, "rules": []}


def _clean_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ChoosiumError("config-invalid", f"{label} must be text")
    cleaned = value.strip()
    if not cleaned:
        raise ChoosiumError("config-invalid", f"{label} cannot be empty")
    if len(cleaned) > maximum or re.search(r"[\x00-\x1f\x7f]", cleaned):
        raise ChoosiumError("config-invalid", f"{label} is not valid")
    return cleaned


def normalize_domain(value: Any) -> str:
    text = _clean_text(value, "Website", 2048).lower()
    if text.startswith("*."):
        text = text[2:]
    text = text.lstrip(".")

    scheme = re.match(r"^([a-z][a-z0-9+.-]*):\/\/", text)
    if scheme and scheme.group(1) not in {"http", "https"}:
        raise ChoosiumError("config-invalid", f"Invalid website: {value}")
    candidate = text if scheme else "//" + text
    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname or ""
        _ = parsed.port
    except ValueError as error:
        raise ChoosiumError("config-invalid", f"Invalid website: {value}") from error

    if not host:
        raise ChoosiumError("config-invalid", f"Invalid website: {value}")
    host = host.rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ChoosiumError("config-invalid", f"Invalid website: {value}") from error
    if len(host) > 253 or ".." in host or re.search(r"[\s/\\]", host):
        raise ChoosiumError("config-invalid", f"Invalid website: {value}")
    if ":" in host:
        try:
            ipaddress.ip_address(host)
        except ValueError as error:
            raise ChoosiumError("config-invalid", f"Invalid website: {value}") from error
        return host
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ChoosiumError("config-invalid", f"Invalid website: {value}")
    labels = host.split(".")
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
        raise ChoosiumError("config-invalid", f"Invalid website: {value}")
    if re.fullmatch(r"[0-9.]+", host):
        try:
            ipaddress.ip_address(host)
        except ValueError as error:
            raise ChoosiumError("config-invalid", f"Invalid website: {value}") from error
    return host


def _string_list(value: Any, label: str, *, domains: bool = False) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ChoosiumError("config-invalid", f"{label} must be a list")
    if len(value) > MAX_VALUES_PER_RULE:
        raise ChoosiumError("config-invalid", f"{label} has too many entries")

    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        cleaned = normalize_domain(item) if domains else _clean_text(item, label, 255)
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def normalize_config(value: Any, *, strict: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ChoosiumError("config-invalid", "The config root must be a table")

    default_value = value.get("default", {})
    if not isinstance(default_value, dict):
        raise ChoosiumError("config-invalid", "[default] must be a table")
    browser = default_value.get("browser", DEFAULT_BROWSER)
    browser = _clean_text(browser, "Default browser", 512)

    raw_rules = value.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ChoosiumError("config-invalid", "rules must be an array of tables")
    if len(raw_rules) > MAX_RULES:
        raise ChoosiumError("config-invalid", "The config has too many rules")

    rules: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            raise ChoosiumError("config-invalid", f"Rule {index + 1} must be a table")
        name = _clean_text(raw_rule.get("name", f"Route {index + 1}"), "Rule name", 80)
        name_key = name.casefold()
        if name_key in names:
            raise ChoosiumError("config-invalid", f"Rule name '{name}' is used more than once")
        names.add(name_key)

        rule_browser = _clean_text(raw_rule.get("browser", ""), f"Browser for {name}", 512)
        clients = _string_list(raw_rule.get("clients", []), f"Clients for {name}")
        domains = _string_list(
            raw_rule.get("url", raw_rule.get("domains", [])),
            f"Websites for {name}",
            domains=True,
        )
        rules.append(
            {
                "name": name,
                "browser": rule_browser,
                "clients": clients,
                "url": domains,
            }
        )

    return {"default": {"browser": browser}, "rules": rules}


def parse_config(text: str) -> tuple[dict[str, Any], bool]:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ChoosiumError("config-invalid", f"Invalid TOML: {error}") from error

    version = raw.get("version")
    if isinstance(version, bool) or version not in (None, 1):
        raise ChoosiumError("config-invalid", "Unsupported config version")

    if version == 1 or isinstance(raw.get("rules"), list):
        modern = dict(raw)
        modern.setdefault("rules", [])
        return normalize_config(modern, strict=False), False

    # Hyprchoosy stored every named rule as a top-level table. Keep it as a
    # read-only migration source; the first UI save writes Choosium's ordered
    # array-of-tables format to its own config directory.
    rules: list[dict[str, Any]] = []
    for name, section in raw.items():
        if name in {"default", "version", "meta", "integration"}:
            continue
        if not isinstance(section, dict):
            continue
        rules.append(
            {
                "name": name,
                "browser": section.get("browser", ""),
                "clients": section.get("clients", []),
                "url": section.get("url", section.get("domains", [])),
            }
        )
    legacy_default = raw.get("default", {})
    if isinstance(legacy_default, dict) and not legacy_default.get("browser"):
        legacy_default = dict(legacy_default)
        legacy_default["browser"] = LEGACY_DEFAULT_BROWSER
    migrated = {"default": legacy_default, "rules": rules}
    return normalize_config(migrated, strict=False), True


def _read_config_bytes(path: Path) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ChoosiumError("config-unreadable", f"Could not read {path}: {error}") from error
    if len(data) > MAX_CONFIG_BYTES:
        raise ChoosiumError("config-invalid", f"Config is larger than {MAX_CONFIG_BYTES} bytes")
    return data


def load_effective_config() -> tuple[dict[str, Any], Path | None, bool, str]:
    primary = config_path()
    legacy = legacy_config_path()
    source: Path | None = None
    legacy_source = False
    if primary.is_file():
        source = primary
    elif legacy != primary and legacy.is_file():
        source = legacy
        legacy_source = True
    if source is None:
        return default_config(), None, False, ""

    data = _read_config_bytes(source)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ChoosiumError("config-invalid", "Config must be UTF-8") from error
    config, legacy_shape = parse_config(text)
    revision = hashlib.sha256(data).hexdigest()
    return config, source, legacy_source or legacy_shape, revision


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def serialize_config(config: dict[str, Any]) -> str:
    normalized = normalize_config(config, strict=True)
    lines = [
        "# Choosium browser routing. This file can also be edited by hand.",
        "version = 1",
        "",
        "[default]",
        f"browser = {_toml_string(normalized['default']['browser'])}",
    ]
    for rule in normalized["rules"]:
        lines.extend(
            [
                "",
                "[[rules]]",
                f"name = {_toml_string(rule['name'])}",
                f"browser = {_toml_string(rule['browser'])}",
                f"clients = {_toml_array(rule['clients'])}",
                f"url = {_toml_array(rule['url'])}",
            ]
        )
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, data: str, mode: int) -> None:
    if path.is_symlink():
        raise ChoosiumError("write-failed", f"Refusing to replace symlink: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        file_descriptor, temporary_name = tempfile.mkstemp(prefix=".choosium-", dir=path.parent)
        try:
            os.fchmod(file_descriptor, mode)
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
    except ChoosiumError:
        raise
    except OSError as error:
        raise ChoosiumError("write-failed", f"Could not write {path}: {error}") from error


def save_config(config: Any, expected_revision: Any) -> dict[str, Any]:
    if not isinstance(expected_revision, str):
        raise ChoosiumError("request-invalid", "expectedRevision is required")
    _, _, _, current_revision = load_effective_config()
    if current_revision != expected_revision:
        raise ChoosiumError(
            "config-conflict",
            "The config changed outside Choosium. Reload it before saving.",
        )
    normalized = normalize_config(config, strict=True)
    atomic_write(config_path(), serialize_config(normalized), 0o600)
    return normalized


def _desktop_directories() -> list[Path]:
    result = [_xdg_home("XDG_DATA_HOME", ".local/share") / "applications"]
    raw = os.environ.get("XDG_DATA_DIRS", "").strip() or "/usr/local/share:/usr/share"
    for entry in raw.split(":"):
        path = Path(entry)
        if entry and path.is_absolute():
            result.append(path / "applications")
    unique: list[Path] = []
    seen: set[str] = set()
    for path in result:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _desktop_id(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace(os.sep, "-")


def _truth(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def _exec_command(exec_line: str) -> str:
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        return ""
    if not tokens:
        return ""

    index = 0
    if Path(tokens[index]).name == "env":
        index += 1
        while index < len(tokens) and ("=" in tokens[index] or tokens[index].startswith("-")):
            index += 1
    if index < len(tokens) and Path(tokens[index]).name == "uwsm-app":
        index += 1
        if index < len(tokens) and tokens[index] == "--":
            index += 1
    if index >= len(tokens) or tokens[index].startswith("%"):
        return ""
    return tokens[index]


def _desktop_entry(path: Path, desktop_id: str, *, include_hidden: bool) -> dict[str, Any] | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, UnicodeError, configparser.Error):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    entry = parser["Desktop Entry"]
    if entry.get("Type", "Application") != "Application":
        return None
    if _truth(entry.get("Hidden", "false")):
        return None
    if not include_hidden and _truth(entry.get("NoDisplay", "false")):
        return None
    mime_types = {item for item in entry.get("MimeType", "").split(";") if item}
    categories = {item for item in entry.get("Categories", "").split(";") if item}
    if not ({"x-scheme-handler/http", "x-scheme-handler/https"} <= mime_types or "WebBrowser" in categories):
        return None
    if desktop_id in LEGACY_DESKTOP_IDS:
        return None

    exec_line = entry.get("Exec", "").strip()
    if not include_hidden and "-safe-mode" in exec_line:
        return None
    command = _exec_command(exec_line)
    if not command:
        return None
    try_exec = entry.get("TryExec", "").strip()
    availability_probe = try_exec or command
    if os.path.isabs(availability_probe):
        if not os.access(availability_probe, os.X_OK):
            return None
    elif shutil.which(availability_probe) is None:
        return None

    return {
        "value": desktop_id,
        "desktopId": desktop_id,
        "label": entry.get("Name", desktop_id.removesuffix(".desktop")),
        "description": Path(command).name,
        "command": command,
        "exec": exec_line,
        "icon": entry.get("Icon", "web-browser"),
        "path": str(path),
    }


def discover_browsers(*, include_hidden: bool = False) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for root in _desktop_directories():
        if not root.is_dir():
            continue
        try:
            paths = sorted(root.rglob("*.desktop"))
        except OSError:
            continue
        for path in paths:
            desktop_id = _desktop_id(root, path)
            if desktop_id in seen_ids:
                continue
            seen_ids.add(desktop_id)
            parsed = _desktop_entry(path, desktop_id, include_hidden=include_hidden)
            if parsed:
                entries.append(parsed)

    preference = {desktop_id: index for index, desktop_id in enumerate(PREFERRED_DESKTOP_IDS)}
    entries.sort(
        key=lambda item: (
            preference.get(item["desktopId"], len(preference)),
            item["label"].casefold(),
            item["desktopId"],
        )
    )

    # Several browser packages install aliases with the same name and launch
    # command. Keep the preferred desktop ID so the picker stays readable.
    result: list[dict[str, Any]] = []
    seen_launchers: set[tuple[str, str]] = set()
    for entry in entries:
        key = (entry["label"].casefold(), entry["exec"])
        if key in seen_launchers:
            continue
        seen_launchers.add(key)
        result.append(entry)
    return result


def resolve_browser(identifier: str, browsers: list[dict[str, Any]]) -> dict[str, Any] | None:
    value = str(identifier or "").strip()
    if not value:
        return None
    for browser in browsers:
        if browser["desktopId"] == value:
            return browser

    try:
        tokens = shlex.split(value)
    except ValueError:
        return None
    if len(tokens) != 1:
        return None

    command_name = Path(tokens[0]).name
    matches = [
        browser
        for browser in browsers
        if value == browser["command"] or command_name == Path(browser["command"]).name
    ]
    return matches[0] if len(matches) == 1 else None


def config_for_ui(config: dict[str, Any], browsers: list[dict[str, Any]]) -> dict[str, Any]:
    projected = json.loads(json.dumps(config))
    identifiers = [projected["default"]] + projected["rules"]
    for item in identifiers:
        resolved = resolve_browser(item.get("browser", ""), browsers)
        if resolved:
            item["browser"] = resolved["desktopId"]
    return projected


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("BROWSER", None)
    environment.pop("GIO_LAUNCHED_DESKTOP_FILE", None)
    environment.pop("GIO_LAUNCHED_DESKTOP_FILE_PID", None)
    return environment


def _run(command: list[str], *, timeout: float = 10) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_clean_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ChoosiumError("command-failed", f"Could not run {command[0]}: {error}") from error


def current_web_handlers() -> dict[str, str]:
    handlers: dict[str, str] = {}
    for mime_type in WEB_MIME_TYPES:
        result = _run(["xdg-mime", "query", "default", mime_type])
        handlers[mime_type] = result.stdout.strip() if result.returncode == 0 else ""
    return handlers


def current_default_desktop(handlers: dict[str, str] | None = None) -> str:
    result = _run(["xdg-settings", "get", "default-web-browser"])
    value = result.stdout.strip()
    if result.returncode == 0 and value:
        return value
    values = handlers if handlers is not None else current_web_handlers()
    return values.get("x-scheme-handler/https", "")


def desktop_is_default(desktop_id: str, handlers: dict[str, str] | None = None) -> bool:
    result = _run(["xdg-settings", "check", "default-web-browser", desktop_id])
    setting_matches = result.returncode == 0 and result.stdout.strip().lower() == "yes"
    values = handlers if handlers is not None else current_web_handlers()
    return setting_matches and all(values.get(mime_type) == desktop_id for mime_type in WEB_MIME_TYPES)


def _desktop_exec_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def desktop_entry_content(helper_path: Path) -> str:
    return "\n".join(
        [
            "[Desktop Entry]",
            "Version=1.0",
            "Type=Application",
            "Name=Choosium",
            "GenericName=Browser Router",
            "Comment=Route links by source app or website",
            f"Exec=/usr/bin/env python3 {_desktop_exec_quote(str(helper_path))} open %u",
            "TryExec=python3",
            "Icon=web-browser",
            "Terminal=false",
            "NoDisplay=true",
            "Categories=Network;WebBrowser;",
            "MimeType=x-scheme-handler/http;x-scheme-handler/https;text/html;",
            "StartupNotify=false",
            "",
        ]
    )


def ensure_desktop_entry() -> None:
    helper_path = Path(__file__).resolve()
    atomic_write(desktop_entry_path(), desktop_entry_content(helper_path), 0o644)
    updater = shutil.which("update-desktop-database")
    if updater:
        _run([updater, str(desktop_entry_path().parent)])


def set_default_desktop(desktop_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.desktop", desktop_id):
        raise ChoosiumError("browser-invalid", "Invalid desktop application ID")
    result = _run(["xdg-settings", "set", "default-web-browser", desktop_id])
    if result.returncode != 0:
        detail = result.stderr.strip() or "xdg-settings rejected the change"
        raise ChoosiumError("default-failed", detail)
    for mime_type in WEB_MIME_TYPES:
        result = _run(["xdg-mime", "default", desktop_id, mime_type])
        if result.returncode != 0:
            detail = result.stderr.strip() or f"Could not update {mime_type}"
            raise ChoosiumError("default-failed", detail)
    handlers = current_web_handlers()
    if not desktop_is_default(desktop_id, handlers):
        mismatched = [mime_type for mime_type in WEB_MIME_TYPES if handlers.get(mime_type) != desktop_id]
        detail = ", ".join(mismatched) if mismatched else "default-web-browser"
        raise ChoosiumError("default-failed", f"The desktop did not keep the selected browser for: {detail}")


def _display_name(desktop_id: str, browsers: list[dict[str, Any]]) -> str:
    if desktop_id == DESKTOP_ID:
        return "Choosium"
    if desktop_id == "hyprchoosy.desktop":
        return "Hyprchoosy"
    for browser in browsers:
        if browser["desktopId"] == desktop_id:
            return browser["label"]
    return desktop_id or "Not set"


def build_status() -> dict[str, Any]:
    config, source, legacy, revision = load_effective_config()
    browsers = discover_browsers()
    all_browsers = discover_browsers(include_hidden=True)
    projected = config_for_ui(config, browsers)
    handlers = current_web_handlers()
    current_default = current_default_desktop(handlers)
    entry_path = desktop_entry_path()
    try:
        installed = entry_path.is_file() and str(Path(__file__).resolve()) in entry_path.read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        installed = False
    is_default = desktop_is_default(DESKTOP_ID, handlers)
    handler_needs_repair = not is_default and (
        current_default == DESKTOP_ID or DESKTOP_ID in handlers.values()
    )
    return {
        "ok": True,
        "config": projected,
        "browsers": browsers,
        "revision": revision,
        "configPath": str(config_path()),
        "sourcePath": str(source) if source else str(config_path()),
        "legacyConfig": legacy,
        "currentDefault": current_default,
        "currentDefaultName": _display_name(current_default, browsers),
        "isDefault": is_default,
        "handlerNeedsRepair": handler_needs_repair,
        "webHandlers": handlers,
        "desktopInstalled": installed,
        "canSetDirect": resolve_browser(config["default"]["browser"], all_browsers) is not None,
    }


def _safe_ui_text(value: Any, maximum: int = 160) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    text = re.sub(r"[<>]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:maximum]


def parse_hypr_clients(raw: Any, active_class: str = "") -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ChoosiumError("hyprland-invalid", "hyprctl returned an unexpected response")
    by_class: dict[str, dict[str, Any]] = {}
    active_key = active_class.casefold()
    for window in raw:
        if not isinstance(window, dict):
            continue
        window_class = _safe_ui_text(window.get("class") or window.get("initialClass"), 120)
        if not window_class or window_class.casefold() == "unknown":
            continue
        key = window_class.casefold()
        title = _safe_ui_text(window.get("title") or window.get("initialTitle"), 160)
        workspace_value = window.get("workspace")
        workspace = ""
        if isinstance(workspace_value, dict):
            workspace = _safe_ui_text(workspace_value.get("name") or workspace_value.get("id"), 40)
        try:
            focus_rank = int(window.get("focusHistoryID", 1_000_000))
        except (TypeError, ValueError):
            focus_rank = 1_000_000

        existing = by_class.get(key)
        if existing is None:
            existing = {
                "value": window_class,
                "label": window_class,
                "title": title,
                "workspace": workspace,
                "focusRank": focus_rank,
                "active": key == active_key,
                "count": 1,
            }
            by_class[key] = existing
        else:
            existing["count"] += 1
            existing["active"] = existing["active"] or key == active_key
            if focus_rank < existing["focusRank"]:
                existing["focusRank"] = focus_rank
                existing["title"] = title
                existing["workspace"] = workspace

    result = list(by_class.values())
    result.sort(key=lambda item: (not item["active"], item["focusRank"], item["label"].casefold()))
    for item in result:
        details: list[str] = []
        if item["active"]:
            details.append("Focused now")
        if item["title"] and item["title"].casefold() != item["label"].casefold():
            details.append(item["title"])
        if item["workspace"]:
            details.append(f"Workspace {item['workspace']}")
        if item["count"] > 1:
            details.append(f"{item['count']} windows")
        item["description"] = " · ".join(details) or "Open window"
    return result


def _hyprctl_json(arguments: list[str], *, required: bool) -> Any:
    try:
        result = _run(["hyprctl", "-j", *arguments], timeout=4)
    except ChoosiumError:
        if required:
            raise
        return None
    if result.returncode != 0:
        if required:
            raise ChoosiumError("hyprland-unavailable", result.stderr.strip() or "Hyprland is unavailable")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        if required:
            raise ChoosiumError("hyprland-invalid", "hyprctl returned invalid JSON") from error
        return None


def active_hypr_class() -> str:
    window = _hyprctl_json(["activewindow"], required=False)
    if not isinstance(window, dict):
        return ""
    return _safe_ui_text(window.get("class") or window.get("initialClass"), 120)


def hypr_client_options() -> list[dict[str, Any]]:
    clients = _hyprctl_json(["clients"], required=True)
    return parse_hypr_clients(clients, active_hypr_class())


def _parent_process(pid: int) -> tuple[int, str] | None:
    status_path = Path("/proc") / str(pid) / "status"
    comm_path = Path("/proc") / str(pid) / "comm"
    try:
        name = comm_path.read_text(encoding="utf-8").strip()
        parent = 0
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("PPid:"):
                parent = int(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        return None
    return (parent, name) if parent > 0 else None


def detect_client() -> str:
    active = active_hypr_class()
    if active:
        return active

    launched_desktop = os.environ.get("GIO_LAUNCHED_DESKTOP_FILE", "")
    if launched_desktop:
        candidate = Path(launched_desktop).name.removesuffix(".desktop")
        if candidate and candidate not in {APP_ID, "hyprchoosy"}:
            return candidate

    pid = os.getpid()
    for _ in range(16):
        parent = _parent_process(pid)
        if parent is None:
            break
        pid, name = parent
        lowered = name.casefold()
        if lowered and not any(skipped in lowered for skipped in SKIPPED_PARENT_PROCESSES):
            return name
    return ""


def parse_url_host(url: str) -> str:
    if not isinstance(url, str) or not url or len(url) > 65536 or "\x00" in url:
        raise ChoosiumError("url-invalid", "The link is not valid")
    candidate = url if "://" in url else "http://" + url
    try:
        host = (urlsplit(candidate).hostname or "").lower().rstrip(".")
        return host.encode("idna").decode("ascii") if host else ""
    except (UnicodeError, ValueError) as error:
        raise ChoosiumError("url-invalid", "The link is not valid") from error


def choose_browser(config: dict[str, Any], url: str, client: str) -> dict[str, str]:
    normalized = normalize_config(config, strict=False)
    client_key = str(client or "").casefold()
    host = parse_url_host(url)
    for rule in normalized["rules"]:
        for pattern in rule["clients"]:
            if pattern.casefold() in client_key:
                return {
                    "browser": rule["browser"],
                    "rule": rule["name"],
                    "reason": "client",
                    "client": client,
                    "host": host,
                }
    for rule in normalized["rules"]:
        for domain in rule["url"]:
            pattern = domain.casefold()
            if host == pattern or host.endswith("." + pattern):
                return {
                    "browser": rule["browser"],
                    "rule": rule["name"],
                    "reason": "website",
                    "client": client,
                    "host": host,
                }
    return {
        "browser": normalized["default"]["browser"],
        "rule": "",
        "reason": "default",
        "client": client,
        "host": host,
    }


def command_routes_to_choosium(identifier: str, command: list[str]) -> bool:
    if APP_ID in identifier:
        return True
    helper_path = Path(__file__).resolve()
    for argument in command:
        argument_name = Path(argument).name.casefold()
        if argument_name in {"hyprchoosy", "choosium.py"}:
            return True
        if os.path.isabs(argument):
            try:
                if Path(argument).resolve() == helper_path:
                    return True
            except OSError:
                pass
    return False


def launch_browser(identifier: str, url: str) -> None:
    browsers = discover_browsers(include_hidden=True)
    resolved = resolve_browser(identifier, browsers)
    if resolved:
        desktop_id = resolved["desktopId"]
        if desktop_id in LEGACY_DESKTOP_IDS:
            raise ChoosiumError("browser-loop", "The fallback browser points back to Choosium")
        gtk_launch = shutil.which("gtk-launch")
        if not gtk_launch:
            raise ChoosiumError("browser-unavailable", "gtk-launch is not installed")
        command = [gtk_launch, desktop_id, url]
    else:
        try:
            command = shlex.split(identifier)
        except ValueError as error:
            raise ChoosiumError("browser-invalid", "The browser command is not valid") from error
        if not command:
            raise ChoosiumError("browser-invalid", "No browser is configured")
        if command_routes_to_choosium(identifier, command):
            raise ChoosiumError("browser-loop", "The fallback browser points back to Choosium")
        if not (os.path.isabs(command[0]) and os.access(command[0], os.X_OK)) and shutil.which(command[0]) is None:
            raise ChoosiumError("browser-unavailable", f"Browser command not found: {command[0]}")
        command.append(url)

    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=_clean_environment(),
        )
    except OSError as error:
        raise ChoosiumError("browser-launch-failed", f"Could not open the browser: {error}") from error


def _fallback_is_available(config: dict[str, Any], browsers: list[dict[str, Any]]) -> None:
    identifier = config["default"]["browser"]
    if resolve_browser(identifier, browsers):
        return
    try:
        command = shlex.split(identifier)
    except ValueError as error:
        raise ChoosiumError("browser-invalid", "The fallback browser is not valid") from error
    if not command:
        raise ChoosiumError("browser-unavailable", "Choose an installed fallback browser first")
    if command_routes_to_choosium(identifier, command):
        raise ChoosiumError("browser-loop", "The fallback browser points back to Choosium")
    executable_available = (
        os.path.isabs(command[0]) and os.access(command[0], os.X_OK)
    ) or shutil.which(command[0]) is not None
    if not executable_available:
        raise ChoosiumError("browser-unavailable", "Choose an installed fallback browser first")


def _read_request() -> dict[str, Any]:
    line = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
    if len(line) > MAX_REQUEST_BYTES:
        raise ChoosiumError("request-invalid", "Request is too large")
    if not line.strip():
        return {}
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ChoosiumError("request-invalid", "Request must be one JSON object") from error
    if not isinstance(value, dict):
        raise ChoosiumError("request-invalid", "Request must be one JSON object")
    return value


def handle_operation(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    if operation == "status":
        return build_status()
    if operation == "save":
        save_config(payload.get("config"), payload.get("expectedRevision"))
        result = build_status()
        result["message"] = "Routing configuration saved."
        return result
    if operation == "clients":
        return {"ok": True, "clients": hypr_client_options()}
    if operation == "set-default":
        config, _, legacy, revision = load_effective_config()
        browsers = discover_browsers()
        all_browsers = discover_browsers(include_hidden=True)
        _fallback_is_available(config, all_browsers)
        if legacy or not config_path().is_file():
            save_config(config_for_ui(config, browsers), revision)
        ensure_desktop_entry()
        set_default_desktop(DESKTOP_ID)
        result = build_status()
        result["message"] = "Choosium now handles web links."
        return result
    if operation == "set-direct-default":
        browsers = discover_browsers(include_hidden=True)
        selected = resolve_browser(str(payload.get("browser", "")), browsers)
        if selected is None:
            raise ChoosiumError("browser-unavailable", "Choose an installed browser first")
        set_default_desktop(selected["desktopId"])
        result = build_status()
        result["message"] = f"{selected['label']} now handles web links directly."
        return result
    if operation == "preview":
        config, _, _, _ = load_effective_config()
        url = str(payload.get("url", ""))
        client = str(payload.get("client", ""))
        return {"ok": True, "route": choose_browser(config, url, client)}
    raise ChoosiumError("request-invalid", f"Unknown operation: {operation}")


def _notify_error(message: str) -> None:
    notifier = shutil.which("notify-send")
    if not notifier:
        return
    try:
        subprocess.Popen(
            [notifier, "Choosium", message],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print("Usage: choosium.py <open|status|save|clients|client-options|set-default|set-direct-default|preview>", file=sys.stderr)
        return 2

    operation = arguments[0]
    if operation == "open":
        url = arguments[1] if len(arguments) > 1 else ""
        try:
            config, _, _, _ = load_effective_config()
            route = choose_browser(config, url, detect_client())
            launch_browser(route["browser"], url)
            return 0
        except ChoosiumError as error:
            print(f"choosium: {error}", file=sys.stderr)
            _notify_error(str(error))
            return 1

    if operation == "client-options":
        try:
            _emit(hypr_client_options())  # type: ignore[arg-type]
            return 0
        except ChoosiumError as error:
            print(f"choosium: {error}", file=sys.stderr)
            return 1

    try:
        payload = _read_request()
        _emit(handle_operation(operation, payload))
    except ChoosiumError as error:
        response: dict[str, Any] = {"ok": False, "code": error.code, "error": str(error)}
        try:
            response["state"] = build_status()
        except ChoosiumError:
            pass
        _emit(response)
    except Exception as error:  # Defensive protocol boundary; never expose a traceback to QML.
        _emit({"ok": False, "code": "internal-error", "error": f"Choosium helper failed: {error}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
