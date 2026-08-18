from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "choosium.py"
SPEC = importlib.util.spec_from_file_location("choosium_helper", SCRIPT)
assert SPEC and SPEC.loader
choosium = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(choosium)


class ConfigTests(unittest.TestCase):
    def test_reads_legacy_hyprchoosy_tables_in_file_order(self):
        config, legacy = choosium.parse_config(
            """
[default]
browser = "chromium"

[mail]
browser = "vivaldi-stable"
clients = ["thunderbird"]

[work]
browser = "google-chrome-stable"
clients = ["slack"]
url = ["github.com"]
"""
        )

        self.assertTrue(legacy)
        self.assertEqual(config["default"]["browser"], "chromium")
        self.assertEqual([rule["name"] for rule in config["rules"]], ["mail", "work"])
        self.assertEqual(config["rules"][1]["url"], ["github.com"])

    def test_serialized_config_round_trips_with_order(self):
        original = {
            "default": {"browser": "chromium.desktop"},
            "rules": [
                {
                    "name": "Chat",
                    "browser": "google-chrome.desktop",
                    "clients": ["Slack"],
                    "url": [],
                },
                {
                    "name": "Code",
                    "browser": "firefox.desktop",
                    "clients": [],
                    "url": ["GitHub.com"],
                },
            ],
        }

        text = choosium.serialize_config(original)
        parsed, legacy = choosium.parse_config(text)

        self.assertFalse(legacy)
        self.assertEqual([rule["name"] for rule in parsed["rules"]], ["Chat", "Code"])
        self.assertEqual(parsed["rules"][1]["url"], ["github.com"])

    def test_normalizes_domains_from_full_urls(self):
        self.assertEqual(choosium.normalize_domain("https://Docs.Example.com/path?q=1"), "docs.example.com")
        self.assertEqual(choosium.normalize_domain("*.Example.com"), "example.com")
        self.assertEqual(choosium.normalize_domain("localhost:3000"), "localhost")
        with self.assertRaises(choosium.ChoosiumError):
            choosium.normalize_domain("ftp://example.com/file")

    def test_preserves_legacy_triggerless_and_duplicate_routes(self):
        config = {
            "default": {"browser": "chromium.desktop"},
            "rules": [
                {"name": "Unused", "browser": "a.desktop", "clients": [], "url": []},
                {"name": "One", "browser": "a.desktop", "clients": ["slack"], "url": []},
                {"name": "Two", "browser": "b.desktop", "clients": ["SLACK"], "url": []},
            ],
        }

        serialized = choosium.serialize_config(config)
        parsed, legacy = choosium.parse_config(serialized)

        self.assertFalse(legacy)
        self.assertEqual([rule["name"] for rule in parsed["rules"]], ["Unused", "One", "Two"])

    def test_empty_version_one_config_is_modern(self):
        config, legacy = choosium.parse_config('version = 1\n\n[default]\nbrowser = "chromium.desktop"\n')

        self.assertFalse(legacy)
        self.assertEqual(config["rules"], [])

    def test_empty_legacy_default_keeps_hyprchoosy_firefox_fallback(self):
        config, legacy = choosium.parse_config('[default]\n\n[unused]\nbrowser = "chromium"\n')

        self.assertTrue(legacy)
        self.assertEqual(config["default"]["browser"], "firefox")

    def test_save_migrates_legacy_source_and_detects_external_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "hyprchoosy" / "config.toml"
            legacy.parent.mkdir(parents=True)
            legacy.write_text(
                '[default]\nbrowser = "chromium"\n\n[work]\nbrowser = "firefox"\nclients = ["slack"]\n',
                encoding="utf-8",
            )
            environment = {
                "XDG_CONFIG_HOME": str(root),
                "CHOOSIUM_CONFIG": "",
                "HYPRCHOOSY_CONFIG": "",
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                config, source, is_legacy, revision = choosium.load_effective_config()
                self.assertEqual(source, legacy)
                self.assertTrue(is_legacy)

                choosium.save_config(config, revision)
                primary = root / "choosium" / "config.toml"
                self.assertTrue(primary.is_file())
                _, source, is_legacy, new_revision = choosium.load_effective_config()
                self.assertEqual(source, primary)
                self.assertFalse(is_legacy)

                primary.write_text(primary.read_text(encoding="utf-8") + "\n# external edit\n", encoding="utf-8")
                with self.assertRaisesRegex(choosium.ChoosiumError, "changed outside"):
                    choosium.save_config(config, new_revision)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "default": {"browser": "chromium.desktop"},
            "rules": [
                {
                    "name": "Website",
                    "browser": "firefox.desktop",
                    "clients": [],
                    "url": ["example.com"],
                },
                {
                    "name": "Chat",
                    "browser": "google-chrome.desktop",
                    "clients": ["slack"],
                    "url": [],
                },
            ],
        }

    def test_client_routes_win_over_website_routes(self):
        route = choosium.choose_browser(self.config, "https://example.com/page", "Slack")
        self.assertEqual(route["browser"], "google-chrome.desktop")
        self.assertEqual(route["reason"], "client")

    def test_website_route_matches_subdomains(self):
        route = choosium.choose_browser(self.config, "https://docs.example.com", "terminal")
        self.assertEqual(route["browser"], "firefox.desktop")
        self.assertEqual(route["reason"], "website")

    def test_unicode_url_host_matches_idna_config(self):
        config = {
            "default": {"browser": "chromium.desktop"},
            "rules": [
                {
                    "name": "Books",
                    "browser": "firefox.desktop",
                    "clients": [],
                    "url": ["bücher.de"],
                }
            ],
        }

        route = choosium.choose_browser(config, "https://bücher.de/catalog", "terminal")

        self.assertEqual(route["browser"], "firefox.desktop")

    def test_unmatched_link_uses_configured_default(self):
        route = choosium.choose_browser(self.config, "https://elsewhere.test", "terminal")
        self.assertEqual(route["browser"], "chromium.desktop")
        self.assertEqual(route["reason"], "default")

    def test_browser_launch_uses_desktop_id_without_a_shell(self):
        browser = {
            "desktopId": "chromium.desktop",
            "value": "chromium.desktop",
            "label": "Chromium",
            "command": "/usr/bin/chromium",
            "exec": "/usr/bin/chromium %U",
        }
        with (
            mock.patch.object(choosium, "discover_browsers", return_value=[browser]),
            mock.patch.object(choosium.shutil, "which", return_value="/usr/bin/gtk-launch"),
            mock.patch.object(choosium.subprocess, "Popen") as popen,
        ):
            choosium.launch_browser("chromium.desktop", "https://example.com")

        command = popen.call_args.args[0]
        self.assertEqual(command, ["/usr/bin/gtk-launch", "chromium.desktop", "https://example.com"])
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_custom_browser_arguments_are_preserved(self):
        browser = {
            "desktopId": "firefox.desktop",
            "value": "firefox.desktop",
            "label": "Firefox",
            "command": "/usr/bin/firefox",
            "exec": "/usr/bin/firefox %u",
        }
        with (
            mock.patch.object(choosium, "discover_browsers", return_value=[browser]),
            mock.patch.object(choosium.shutil, "which", return_value="/usr/bin/firefox"),
            mock.patch.object(choosium.subprocess, "Popen") as popen,
        ):
            choosium.launch_browser("firefox --private-window", "https://example.com")

        self.assertEqual(
            popen.call_args.args[0],
            ["firefox", "--private-window", "https://example.com"],
        )

    def test_interpreter_based_self_route_is_rejected(self):
        identifier = f"{os.path.basename(os.sys.executable)} {SCRIPT} open"
        with (
            mock.patch.object(choosium, "discover_browsers", return_value=[]),
            mock.patch.object(choosium.shutil, "which", return_value=os.sys.executable),
        ):
            with self.assertRaisesRegex(choosium.ChoosiumError, "points back"):
                choosium.launch_browser(identifier, "https://example.com")


class DiscoveryTests(unittest.TestCase):
    def _desktop(self, name: str, command: str, *, no_display: bool = False) -> str:
        return "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                f"Name={name}",
                f"Exec={command} %U",
                "TryExec=/bin/true",
                "Categories=Network;WebBrowser;",
                "MimeType=x-scheme-handler/http;x-scheme-handler/https;",
                f"NoDisplay={'true' if no_display else 'false'}",
                "",
            ]
        )

    def test_discovers_visible_browser_desktop_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            data_home = Path(directory) / "data"
            apps = data_home / "applications"
            apps.mkdir(parents=True)
            (apps / "alpha.desktop").write_text(self._desktop("Alpha", "/bin/true"), encoding="utf-8")
            (apps / "hidden.desktop").write_text(
                self._desktop("Hidden", "/bin/true", no_display=True), encoding="utf-8"
            )
            environment = {
                "XDG_DATA_HOME": str(data_home),
                "XDG_DATA_DIRS": str(Path(directory) / "empty"),
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                browsers = choosium.discover_browsers()

        self.assertEqual([browser["desktopId"] for browser in browsers], ["alpha.desktop"])
        self.assertEqual(choosium.resolve_browser("true", browsers)["label"], "Alpha")

    def test_hidden_desktop_entries_are_available_only_for_runtime_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            data_home = Path(directory) / "data"
            apps = data_home / "applications"
            apps.mkdir(parents=True)
            (apps / "hidden.desktop").write_text(
                self._desktop("Hidden", "/bin/true", no_display=True), encoding="utf-8"
            )
            environment = {
                "XDG_DATA_HOME": str(data_home),
                "XDG_DATA_DIRS": str(Path(directory) / "empty"),
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                self.assertEqual(choosium.discover_browsers(), [])
                hidden = choosium.discover_browsers(include_hidden=True)

        self.assertEqual([browser["desktopId"] for browser in hidden], ["hidden.desktop"])

    def test_ambiguous_wrapper_command_is_not_resolved_to_an_arbitrary_browser(self):
        browsers = [
            {"desktopId": "one.desktop", "command": "flatpak"},
            {"desktopId": "two.desktop", "command": "flatpak"},
        ]

        self.assertIsNone(choosium.resolve_browser("flatpak", browsers))
        self.assertIsNone(choosium.resolve_browser("flatpak run org.mozilla.firefox", browsers))

    def test_projects_legacy_commands_to_desktop_ids_for_the_ui(self):
        browsers = [
            {
                "desktopId": "chromium.desktop",
                "value": "chromium.desktop",
                "label": "Chromium",
                "command": "/usr/bin/chromium",
                "exec": "/usr/bin/chromium %U",
            }
        ]
        config = {
            "default": {"browser": "chromium"},
            "rules": [{"name": "Work", "browser": "chromium", "clients": ["slack"], "url": []}],
        }

        projected = choosium.config_for_ui(config, browsers)

        self.assertEqual(projected["default"]["browser"], "chromium.desktop")
        self.assertEqual(projected["rules"][0]["browser"], "chromium.desktop")
        self.assertEqual(config["default"]["browser"], "chromium")

    def test_xdg_paths_ignore_relative_values_and_empty_data_dirs(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            environment = {
                "HOME": str(home),
                "XDG_CONFIG_HOME": "relative-config",
                "XDG_DATA_HOME": "relative-data",
                "XDG_DATA_DIRS": "",
                "CHOOSIUM_CONFIG": "",
                "HYPRCHOOSY_CONFIG": "",
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                self.assertEqual(choosium.config_path(), home / ".config/choosium/config.toml")
                directories = choosium._desktop_directories()

        self.assertIn(home / ".local/share/applications", directories)
        self.assertIn(Path("/usr/share/applications"), directories)


class HyprlandTests(unittest.TestCase):
    def test_client_options_are_unique_and_put_the_focused_class_first(self):
        clients = [
            {
                "class": "Slack",
                "title": "general",
                "workspace": {"name": "2"},
                "focusHistoryID": 5,
            },
            {
                "class": "Alacritty",
                "title": "shell",
                "workspace": {"name": "1"},
                "focusHistoryID": 1,
            },
            {
                "class": "Slack",
                "title": "random",
                "workspace": {"name": "3"},
                "focusHistoryID": 2,
            },
        ]

        options = choosium.parse_hypr_clients(clients, "Slack")

        self.assertEqual([option["value"] for option in options], ["Slack", "Alacritty"])
        self.assertIn("Focused now", options[0]["description"])
        self.assertIn("2 windows", options[0]["description"])

    def test_client_options_strip_markup_from_window_controlled_text(self):
        options = choosium.parse_hypr_clients(
            [{"class": "Browser", "title": '<img src="https://attacker.invalid/pixel">'}]
        )

        self.assertNotIn("<", options[0]["description"])
        self.assertNotIn(">", options[0]["description"])

    def test_optional_hyprland_failure_falls_back_cleanly(self):
        with mock.patch.object(
            choosium,
            "_run",
            side_effect=choosium.ChoosiumError("command-failed", "hyprctl missing"),
        ):
            self.assertEqual(choosium.active_hypr_class(), "")


class DesktopIntegrationTests(unittest.TestCase):
    def test_generated_desktop_entry_routes_uris_through_the_helper(self):
        content = choosium.desktop_entry_content(Path("/tmp/plugin/scripts/choosium.py"))

        self.assertIn('Exec=/usr/bin/env python3 "/tmp/plugin/scripts/choosium.py" open %u', content)
        self.assertIn("x-scheme-handler/http", content)
        self.assertIn("NoDisplay=true", content)

    def test_set_default_updates_all_web_handlers_and_verifies(self):
        def run_result(command, **_kwargs):
            if command[:2] == ["xdg-settings", "check"]:
                return mock.Mock(returncode=0, stdout="yes\n", stderr="")
            if command[:3] == ["xdg-mime", "query", "default"]:
                return mock.Mock(returncode=0, stdout=choosium.DESKTOP_ID + "\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(choosium, "_run", side_effect=run_result) as run:
            choosium.set_default_desktop(choosium.DESKTOP_ID)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["xdg-settings", "set", "default-web-browser", choosium.DESKTOP_ID], commands)
        for mime_type in choosium.WEB_MIME_TYPES:
            self.assertIn(["xdg-mime", "default", choosium.DESKTOP_ID, mime_type], commands)
            self.assertIn(["xdg-mime", "query", "default", mime_type], commands)
        self.assertIn(
            ["xdg-settings", "check", "default-web-browser", choosium.DESKTOP_ID],
            commands,
        )

    def test_default_status_rejects_one_mismatched_web_handler(self):
        handlers = {mime_type: choosium.DESKTOP_ID for mime_type in choosium.WEB_MIME_TYPES}
        handlers["text/html"] = "other.desktop"
        completed = mock.Mock(returncode=0, stdout="yes\n", stderr="")

        with mock.patch.object(choosium, "_run", return_value=completed):
            self.assertFalse(choosium.desktop_is_default(choosium.DESKTOP_ID, handlers))


class QmlContractTests(unittest.TestCase):
    def test_panel_keeps_per_monitor_requests_and_editor_revisions_distinct(self):
        panel = (SCRIPT.parents[1] / "Panel.qml").read_text(encoding="utf-8")

        self.assertIn('"panel:" + instanceKey + ":" + kind', panel)
        self.assertIn("property string editorRevision", panel)
        self.assertIn("String(service.revision || \"\") !== editorRevision", panel)

    def test_panel_resynchronizes_multiselect_and_avoids_unbounded_browser_popups(self):
        panel = (SCRIPT.parents[1] / "Panel.qml").read_text(encoding="utf-8")

        self.assertIn("onEditorClientsChanged", panel)
        self.assertIn("component BrowserChoices", panel)
        self.assertNotIn("Ui.SearchableDropdown", panel)


if __name__ == "__main__":
    unittest.main()
