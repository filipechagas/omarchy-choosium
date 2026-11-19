from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
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

    def test_private_launch_runs_the_entry_with_the_browsers_own_flag(self):
        cases = [
            ("chromium.desktop", "/usr/bin/chromium", "/usr/bin/chromium %U", "--incognito"),
            ("firefox.desktop", "/usr/lib/firefox/firefox", "/usr/lib/firefox/firefox %u", "--private-window"),
            ("microsoft-edge.desktop", "/usr/bin/microsoft-edge-stable", "/usr/bin/microsoft-edge-stable %U", "--inprivate"),
        ]
        for desktop_id, command, exec_line, flag in cases:
            browser = {
                "desktopId": desktop_id,
                "value": desktop_id,
                "label": desktop_id,
                "command": command,
                "exec": exec_line,
            }
            with (
                self.subTest(desktop_id),
                mock.patch.object(choosium, "discover_browsers", return_value=[browser]),
                mock.patch.object(choosium.subprocess, "Popen") as popen,
            ):
                choosium.launch_browser(desktop_id, "https://claude.ai/oauth", private=True)

                self.assertEqual(popen.call_args.args[0], [command, flag, "https://claude.ai/oauth"])

    def test_private_launch_keeps_exec_prefixes_and_drops_other_field_codes(self):
        browser = {
            "desktopId": "vivaldi-stable.desktop",
            "value": "vivaldi-stable.desktop",
            "label": "Vivaldi",
            "command": "/usr/bin/vivaldi-stable",
            "exec": "env GDK_SCALE=1 /usr/bin/vivaldi-stable --profile-directory=Work %U %i",
        }
        with (
            mock.patch.object(choosium, "discover_browsers", return_value=[browser]),
            mock.patch.object(choosium.subprocess, "Popen") as popen,
        ):
            choosium.launch_browser("vivaldi-stable.desktop", "https://example.com", private=True)

        self.assertEqual(
            popen.call_args.args[0],
            ["env", "GDK_SCALE=1", "/usr/bin/vivaldi-stable", "--profile-directory=Work", "--incognito", "https://example.com"],
        )

    def test_private_custom_command_gets_one_private_flag(self):
        with (
            mock.patch.object(choosium, "discover_browsers", return_value=[]),
            mock.patch.object(choosium.shutil, "which", return_value="/usr/bin/browser"),
            mock.patch.object(choosium.subprocess, "Popen") as popen,
        ):
            choosium.launch_browser("firefox --private-window", "https://example.com", private=True)
            first = popen.call_args.args[0]
            choosium.launch_browser("chromium --new-window", "https://example.com", private=True)
            second = popen.call_args.args[0]

        self.assertEqual(first, ["firefox", "--private-window", "https://example.com"])
        self.assertEqual(second, ["chromium", "--new-window", "--incognito", "https://example.com"])

    def test_launch_without_a_link_opens_the_browser(self):
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
            choosium.launch_browser("chromium.desktop", "")

        self.assertEqual(popen.call_args.args[0], ["/usr/bin/gtk-launch", "chromium.desktop"])

    def test_client_routes_apply_without_a_link(self):
        route = choosium.choose_browser(self.config, "", "Slack")
        self.assertEqual(route["browser"], "google-chrome.desktop")
        self.assertEqual(choosium.choose_browser(self.config, "", "")["reason"], "default")

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


class ClientDetectionTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(choosium, "_script_name", return_value="")
        self.script_name = patcher.start()
        self.addCleanup(patcher.stop)

    def test_process_ancestry_beats_an_unrelated_focused_window(self):
        ancestry = iter(
            [
                (90, "python3"),
                (80, "gio-launch-desktop"),
                (70, "claude"),
                (60, "zsh"),
                (50, "herdr"),
                (40, "foot"),
                (1, "systemd"),
                None,
            ]
        )
        with (
            mock.patch.object(choosium.os, "getpid", return_value=100),
            mock.patch.object(choosium, "_parent_process", side_effect=lambda _pid: next(ancestry)),
            mock.patch.object(choosium, "active_hypr_class", return_value="Chromium"),
            mock.patch.dict(os.environ, {"GIO_LAUNCHED_DESKTOP_FILE": ""}, clear=False),
        ):
            client = choosium.detect_client()

        self.assertEqual(client, "claude herdr foot")
        config = {
            "default": {"browser": "chromium.desktop"},
            "rules": [
                {
                    "name": "Agent terminal",
                    "browser": "google-chrome.desktop",
                    "clients": ["herdr", "foot"],
                    "url": [],
                }
            ],
        }
        route = choosium.choose_browser(config, "https://unmatched.test", client)
        self.assertEqual(route["browser"], "google-chrome.desktop")
        self.assertEqual(route["reason"], "client")

    def test_gio_metadata_and_process_ancestry_are_both_preserved(self):
        ancestry = iter([(80, "python3"), (70, "herdr"), (1, "systemd"), None])
        with (
            mock.patch.object(choosium.os, "getpid", return_value=100),
            mock.patch.object(choosium, "_parent_process", side_effect=lambda _pid: next(ancestry)),
            mock.patch.object(choosium, "active_hypr_class", return_value="Chromium"),
            mock.patch.dict(
                os.environ,
                {"GIO_LAUNCHED_DESKTOP_FILE": "/usr/share/applications/slack.desktop"},
                clear=False,
            ),
        ):
            client = choosium.detect_client()

        self.assertEqual(client, "slack herdr")

    def test_focused_window_is_used_only_without_launch_provenance(self):
        ancestry = iter([(80, "python3"), (70, "zsh"), (1, "systemd"), None])
        with (
            mock.patch.object(choosium.os, "getpid", return_value=100),
            mock.patch.object(choosium, "_parent_process", side_effect=lambda _pid: next(ancestry)),
            mock.patch.object(choosium, "active_hypr_class", return_value="Chromium"),
            mock.patch.dict(os.environ, {"GIO_LAUNCHED_DESKTOP_FILE": ""}, clear=False),
        ):
            client = choosium.detect_client()

        self.assertEqual(client, "Chromium")

    def test_a_script_run_by_an_interpreter_is_matched_by_its_file_name(self):
        ancestry = iter(
            [
                (90, "python3"),
                (80, "xdg-open"),
                (70, "bash"),
                (60, "quickshell"),
                (50, "omarchy-launch-"),
                (1, "systemd"),
                None,
            ]
        )
        self.script_name.side_effect = {100: "choosium.py", 80: "aws-vpn-login"}.get
        with (
            mock.patch.object(choosium.os, "getpid", return_value=100),
            mock.patch.object(choosium, "_parent_process", side_effect=lambda _pid: next(ancestry)),
            mock.patch.object(choosium, "active_hypr_class", return_value="Chromium"),
            mock.patch.dict(os.environ, {"GIO_LAUNCHED_DESKTOP_FILE": ""}, clear=False),
        ):
            client = choosium.detect_client()

        self.assertEqual(client, "aws-vpn-login omarchy-launch-")
        self.assertEqual(self.script_name.call_args_list, [mock.call(80)])
        config = {
            "default": {"browser": "chromium.desktop"},
            "rules": [
                {
                    "name": "Work",
                    "browser": "google-chrome.desktop",
                    "clients": ["aws"],
                    "url": [],
                }
            ],
        }
        route = choosium.choose_browser(config, "https://accounts.google.com/o/saml2/idp", client)
        self.assertEqual(route["browser"], "google-chrome.desktop")
        self.assertEqual(route["reason"], "client")

    def test_skipped_process_names_are_ignored_as_script_names(self):
        ancestry = iter([(80, "python3"), (70, "sh"), (60, "foot"), (1, "systemd"), None])
        self.script_name.return_value = "xdg-open"
        with (
            mock.patch.object(choosium.os, "getpid", return_value=100),
            mock.patch.object(choosium, "_parent_process", side_effect=lambda _pid: next(ancestry)),
            mock.patch.object(choosium, "active_hypr_class", return_value="Chromium"),
            mock.patch.dict(os.environ, {"GIO_LAUNCHED_DESKTOP_FILE": ""}, clear=False),
        ):
            client = choosium.detect_client()

        self.assertEqual(client, "foot")


class ScriptNameTests(unittest.TestCase):
    def run_process(self, arguments, cwd):
        process = subprocess.Popen(arguments, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process.pid

    def test_names_the_script_file_an_interpreter_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "aws-vpn-login"
            script.write_text("sleep 10\nexit 0\n", encoding="utf-8")
            absolute = self.run_process(["bash", "-e", "--", str(script)], directory)
            relative = self.run_process(["bash", "aws-vpn-login"], directory)

            self.assertEqual(choosium._script_name(absolute), "aws-vpn-login")
            self.assertEqual(choosium._script_name(relative), "aws-vpn-login")

    def test_ignores_command_strings_that_are_not_files(self):
        with tempfile.TemporaryDirectory() as directory:
            command = self.run_process(["bash", "-c", "sleep 10; exit 0", "slack"], directory)
            module = self.run_process(["python3", "-m", "slack"], directory)

            self.assertEqual(choosium._script_name(command), "")
            self.assertEqual(choosium._script_name(module), "")


class DesktopIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("xdg-settings") and shutil.which("xdg-mime"), "XDG tools are required")
    def test_set_default_repairs_desktop_specific_web_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_home = root / "config"
            applications = root / "data" / "applications"
            config_home.mkdir()
            applications.mkdir(parents=True)
            override = config_home / "hyprland-mimeapps.list"
            override.write_text(
                "# Keep desktop-specific defaults.\n"
                "[Default Applications]\n"
                "x-scheme-handler/http=omarchy-browser.desktop\n"
                "x-scheme-handler/https=omarchy-browser.desktop\n"
                "text/plain=editor.desktop\n"
                "\n[Added Associations]\n"
                "x-scheme-handler/http=omarchy-browser.desktop;\n",
                encoding="utf-8",
            )
            override.chmod(0o640)
            browser_entry = (
                "[Desktop Entry]\nType=Application\nName=Browser\n"
                "Exec=python3 %u\nCategories=Network;WebBrowser;\n"
                "MimeType=x-scheme-handler/http;x-scheme-handler/https;text/html;\n"
            )
            for desktop_id in ("chromium.desktop", "omarchy-browser.desktop"):
                (applications / desktop_id).write_text(browser_entry, encoding="utf-8")
            config = config_home / "choosium" / "config.toml"
            config.parent.mkdir()
            config.write_text(choosium.serialize_config(choosium.default_config()), encoding="utf-8")
            environment = {
                "CHOOSIUM_CONFIG": str(config),
                "XDG_CONFIG_HOME": str(config_home),
                "XDG_CONFIG_DIRS": str(root / "system-config"),
                "XDG_DATA_HOME": str(root / "data"),
                "XDG_DATA_DIRS": str(root / "system-data"),
                "XDG_CURRENT_DESKTOP": "Hyprland",
            }

            with mock.patch.dict(os.environ, environment):
                choosium.ensure_desktop_entry()
                self.assertFalse(choosium.build_status()["isDefault"])
                result = choosium.handle_operation("set-default", {})

                self.assertTrue(result["isDefault"])
                self.assertFalse(result["handlerNeedsRepair"])
                self.assertTrue(all(value == choosium.DESKTOP_ID for value in result["webHandlers"].values()))

                direct = choosium.handle_operation("set-direct-default", {"browser": "chromium.desktop"})
                self.assertTrue(all(value == "chromium.desktop" for value in direct["webHandlers"].values()))

            content = override.read_text(encoding="utf-8")
            self.assertIn("# Keep desktop-specific defaults.\n", content)
            self.assertIn("text/plain=editor.desktop\n", content)
            self.assertIn("[Added Associations]\nx-scheme-handler/http=omarchy-browser.desktop;\n", content)
            self.assertEqual(override.stat().st_mode & 0o777, 0o640)

    def test_desktop_overrides_only_update_active_user_web_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = (
                "# Preserve comments.\n[Default Applications]\n"
                "x-scheme-handler/http=other.desktop\n"
                "text/html=other.desktop\n"
                "x-scheme-handler/mailto=mail.desktop\n"
                "[Removed Associations]\ntext/html=removed.desktop;\n"
            )
            for desktop in ("hyprland", "custom", "inactive"):
                (root / f"{desktop}-mimeapps.list").write_text(original, encoding="utf-8")
            with mock.patch.dict(os.environ, {
                "XDG_CONFIG_HOME": directory,
                "XDG_CURRENT_DESKTOP": "Hyprland:Custom:Missing:../../outside",
            }):
                choosium._set_desktop_web_overrides(choosium.DESKTOP_ID)

            expected = original.replace("other.desktop", choosium.DESKTOP_ID)
            for desktop in ("hyprland", "custom"):
                self.assertEqual((root / f"{desktop}-mimeapps.list").read_text(encoding="utf-8"), expected)
            self.assertEqual((root / "inactive-mimeapps.list").read_text(encoding="utf-8"), original)
            self.assertFalse((root / "missing-mimeapps.list").exists())

    def test_desktop_override_repair_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.list"
            original = "[Default Applications]\nx-scheme-handler/http=other.desktop\n"
            target.write_text(original, encoding="utf-8")
            (root / "hyprland-mimeapps.list").symlink_to(target)
            with mock.patch.dict(os.environ, {
                "XDG_CONFIG_HOME": directory,
                "XDG_CURRENT_DESKTOP": "Hyprland",
            }):
                with self.assertRaisesRegex(choosium.ChoosiumError, "symlink"):
                    choosium._set_desktop_web_overrides(choosium.DESKTOP_ID)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_generated_desktop_entry_routes_uris_through_the_helper(self):
        with mock.patch.object(choosium.os, "access", return_value=True):
            content = choosium.desktop_entry_content(Path("/tmp/plugin/scripts/choosium.py"))

        self.assertIn("Exec=/tmp/plugin/scripts/choosium.py open %u", content)
        self.assertIn("x-scheme-handler/http", content)
        self.assertIn("NoDisplay=true", content)

    def test_exec_starts_with_the_helper_for_launchers_that_run_its_first_word(self):
        # omarchy-launch-browser runs `sed 's/^Exec=\([^ ]*\).*/\1/'` with its own arguments.
        with mock.patch.object(choosium.os, "access", return_value=True):
            content = choosium.desktop_entry_content(Path("/opt/choosium/scripts/choosium.py"))

        exec_line = next(line for line in content.splitlines() if line.startswith("Exec="))
        self.assertEqual(exec_line.removeprefix("Exec=").split(" ")[0], "/opt/choosium/scripts/choosium.py")

    def test_non_executable_helper_falls_back_to_the_interpreter(self):
        with mock.patch.object(choosium.os, "access", return_value=False):
            line = choosium.desktop_exec_line(Path("/tmp/plugin/scripts/choosium.py"))

        self.assertEqual(line, "/usr/bin/env python3 /tmp/plugin/scripts/choosium.py open %u")

    def test_helper_paths_with_reserved_characters_are_quoted(self):
        with mock.patch.object(choosium.os, "access", return_value=True):
            line = choosium.desktop_exec_line(Path('/home/me/My Plugins/$x/choosium.py'))

        self.assertEqual(line, '"/home/me/My Plugins/\\\\$x/choosium.py" open %u')

    def test_status_rewrites_an_outdated_generated_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / choosium.DESKTOP_ID
            entry.write_text("[Desktop Entry]\nExec=/usr/bin/env python3 old open %u\n", encoding="utf-8")
            with (
                mock.patch.object(choosium, "desktop_entry_path", return_value=entry),
                mock.patch.object(choosium.shutil, "which", return_value=None),
            ):
                choosium.refresh_desktop_entry()

            self.assertEqual(
                entry.read_text(encoding="utf-8"),
                choosium.desktop_entry_content(Path(choosium.__file__).resolve()),
            )

    def test_status_does_not_create_a_missing_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / choosium.DESKTOP_ID
            with mock.patch.object(choosium, "desktop_entry_path", return_value=entry):
                choosium.refresh_desktop_entry()

            self.assertFalse(entry.exists())

    @unittest.skipUnless(shutil.which("xdg-open"), "xdg-open is required")
    def test_generated_desktop_entry_works_with_generic_xdg_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_home = root / "data"
            applications = data_home / "applications"
            config_home = root / "config"
            applications.mkdir(parents=True)
            config_home.mkdir()

            helper = root / "probe.py"
            helper.write_text(
                "#!/usr/bin/env python3\nimport json\nimport sys\nprint(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            helper.chmod(0o755)
            (applications / choosium.DESKTOP_ID).write_text(
                choosium.desktop_entry_content(helper),
                encoding="utf-8",
            )
            (config_home / "mimeapps.list").write_text(
                "[Default Applications]\n"
                f"x-scheme-handler/https={choosium.DESKTOP_ID}\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "BROWSER": "",
                    "DISPLAY": ":0",
                    "XDG_CONFIG_HOME": str(config_home),
                    "XDG_CURRENT_DESKTOP": "X-Generic",
                    "XDG_DATA_DIRS": str(root / "empty"),
                    "XDG_DATA_HOME": str(data_home),
                }
            )

            result = subprocess.run(
                ["xdg-open", "https://www.google.com"],
                check=False,
                capture_output=True,
                cwd=root,
                env=environment,
                text=True,
                timeout=5,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["open", "https://www.google.com"])

    def test_set_default_updates_all_web_handlers_and_verifies(self):
        def run_result(command, **_kwargs):
            if command[:2] == ["xdg-settings", "check"]:
                return mock.Mock(returncode=0, stdout="yes\n", stderr="")
            if command[:3] == ["xdg-mime", "query", "default"]:
                return mock.Mock(returncode=0, stdout=choosium.DESKTOP_ID + "\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(choosium, "_run", side_effect=run_result) as run,
            mock.patch.object(choosium, "_set_desktop_web_overrides"),
        ):
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

    def test_panel_only_offers_setting_choosium_as_the_default(self):
        panel = (SCRIPT.parents[1] / "Panel.qml").read_text(encoding="utf-8")

        self.assertIn("Set Choosium as your Default browser", panel)
        self.assertIn("visible: root.serviceReady && !root.routingHealthy", panel)
        self.assertNotIn("useFallbackDirectly", panel)
        self.assertNotIn('requestOperation("set-direct-default"', panel)


if __name__ == "__main__":
    unittest.main()


class BrowserInvocationTests(unittest.TestCase):
    def run_main(self, arguments):
        with (
            mock.patch.object(choosium, "load_effective_config", return_value=({"default": {"browser": "chromium.desktop"}, "rules": []}, None, False, "")),
            mock.patch.object(choosium, "detect_client", return_value=""),
            mock.patch.object(choosium, "launch_browser") as launch,
            mock.patch.object(choosium, "_read_request", side_effect=AssertionError("read a panel request")),
        ):
            status = choosium.main(arguments)
        return status, launch

    def test_omarchy_private_launch_is_routed_privately(self):
        # omarchy-launch-browser --private <url> runs `<Exec first word> --incognito <url>`.
        status, launch = self.run_main(["--incognito", "https://claude.ai/oauth/authorize"])

        self.assertEqual(status, 0)
        launch.assert_called_once_with("chromium.desktop", "https://claude.ai/oauth/authorize", private=True)

    def test_bare_link_and_open_operation_are_routed_normally(self):
        for arguments in (["https://example.com"], ["open", "https://example.com"]):
            with self.subTest(arguments):
                status, launch = self.run_main(arguments)

                self.assertEqual(status, 0)
                launch.assert_called_once_with("chromium.desktop", "https://example.com", private=False)

    def test_no_arguments_opens_the_default_browser(self):
        status, launch = self.run_main([])

        self.assertEqual(status, 0)
        launch.assert_called_once_with("chromium.desktop", "", private=False)

    def test_help_answers_without_reading_stdin(self):
        result = subprocess.run(
            [os.sys.executable, str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            stdin=subprocess.PIPE,
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertNotIn("MOZ_LOG", result.stdout)

    def test_panel_operations_still_read_requests(self):
        with (
            mock.patch.object(choosium, "_read_request", return_value={}),
            mock.patch.object(choosium, "handle_operation", return_value={"ok": True}) as handle,
            mock.patch.object(choosium, "_emit"),
        ):
            choosium.main(["status"])

        handle.assert_called_once_with("status", {})
