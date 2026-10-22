# Architecture

Choosium has three runtime pieces with one-way dependencies.

```text
BarWidget.qml -> Panel.qml -> Service.qml -> scripts/choosium.py
                                      XDG, TOML, hyprctl, browser launch

generated .desktop -----------------> scripts/choosium.py open <URI>
```

## QML Boundary

`BarWidget.qml` provides the bar affordance and injects the singleton service
into `Panel.qml`. The panel owns only transient form state. It sends complete
configuration snapshots to the service and does not read or write files.

`Service.qml` serializes helper requests through one attached process. It keeps
the latest validated status available to all bar instances and gives each panel
request a correlated response. The service has no routing logic.

`Model.js` contains pure UI transformations: immutable rule updates, ordering,
summaries, and lightweight form validation.

## Helper Boundary

`scripts/choosium.py` is both the configuration bridge and the desktop URL
handler. Its operation interface is intentionally small:

| Operation | Purpose |
|---|---|
| `status` | Read config, discover browsers, and inspect the XDG default. |
| `save` | Validate and atomically write one complete config snapshot. |
| `client-options` | Project `hyprctl -j clients` into picker options. |
| `set-default` | Install the user desktop entry and select Choosium. |
| `set-direct-default` | Select the configured destination directly. |
| `preview` | Resolve a URL and client without launching a browser. |
| `open` | Resolve and launch a URL from the generated desktop entry. |

QML requests are one JSON object on stdin and one JSON object on stdout. Browser
launches use argument arrays, never a shell command string.

## Configuration Ownership

The user-owned Choosium TOML file is authoritative. Every status response
includes a SHA-256 revision of the bytes read. A UI save must present that
revision; if the file changed after the panel loaded it, the helper returns a
conflict instead of overwriting the hand edit.

Writes use a same-directory temporary file, `fsync`, and atomic replacement.
The config is mode `0600`. Symlink targets are refused.

Hyprchoosy's top-level-table format is accepted only as a migration input. The
first Choosium write creates an ordered `[[rules]]` array in Choosium's own
directory and leaves the source file unchanged.

## Routing

The URL handler loads config for every invocation, so direct edits apply to the
next link without a daemon or reload signal. It combines GIO launch metadata
with meaningful process ancestors, preserving both an initiating agent and its
containing terminal applications. When an ancestor is a script interpreter
such as `bash` or `python3`, the script's file name from its command line is a
candidate too, because the process name is only the interpreter's. The
argument counts only if it is an existing file, so command strings and link
arguments cannot become source apps. The active Hyprland window is used only
when that launch provenance is unavailable, because focus may change before a
background process opens its link.

Routing performs two ordered passes: all app patterns first, then all website
patterns. The default destination is used after both passes. Installed desktop
IDs launch through `gtk-launch`; hand-written executable commands launch as a
detached argument array.

## XDG Integration

Choosium writes a user desktop entry under `XDG_DATA_HOME`. It updates existing
HTTP, HTTPS, and HTML overrides in the active desktops' user-specific
`XDG_CONFIG_HOME/<desktop>-mimeapps.list` files before calling `xdg-settings`:
these overrides take precedence over the general defaults and otherwise cause
`xdg-settings` to fail its read-back check. Comments, unrelated associations,
and file permissions are preserved; system files are never modified.
It sets the general defaults with `xdg-settings` and `xdg-mime`, then checks
the browser setting and queries all three handlers independently. The panel
offers repair instead of claiming success if any handler differs or the
generated desktop entry is stale. The panel presents the same default-browser
action for initial setup and repair, and hides it while Choosium is active.
