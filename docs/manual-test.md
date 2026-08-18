# Manual Test Checklist

Use a disposable config directory when a case should not affect normal routes:

```bash
export CHOOSIUM_CONFIG="$(mktemp -d)/config.toml"
```

## Installation And Panel

- [ ] `omarchy plugin validate .` succeeds.
- [ ] The plugin installs and enables with `omarchy plugin add <repo> --enable`.
- [ ] The bar icon follows the current Omarchy theme.
- [ ] The panel opens by pointer and `omarchy-shell shell toggle io.github.filipechagas.choosium`.
- [ ] Escape closes the editor before it closes the panel.
- [ ] The panel fits on a narrow display and scrolls without clipped dropdowns.
- [ ] Opening it on each monitor anchors the panel to that monitor's bar.

## Browser Setup

- [ ] Every installed visible browser appears once with its executable name.
- [ ] Changing the unmatched-link destination persists after reopening.
- [ ] **Set Choosium as your Default browser** makes Choosium the default.
- [ ] The default-browser action is hidden while Choosium is active.
- [ ] Changing only one web default makes the default-browser action reappear.
- [ ] Removing or changing the generated desktop entry makes the default-browser action reappear.
- [ ] The bar status changes from standby to routing.
- [ ] A missing or invalid custom browser command produces an actionable error.

## Add Workflow

- [ ] The source-app picker lists current `hyprctl -j clients` classes.
- [ ] The focused class appears first and is marked **Focused now**.
- [ ] Duplicate windows are collapsed and show their window count.
- [ ] Search filters by class, title, and workspace description.
- [ ] Refresh notices a newly opened or closed app.
- [ ] Multiple app classes can be selected.
- [ ] A closed app class can be added manually.
- [ ] A full website URL is reduced to its hostname.
- [ ] Edit, delete, move up, and move down persist after reopening.

## Routing

- [ ] A link opened from a configured app reaches that app's browser.
- [ ] A configured exact domain reaches its browser.
- [ ] A subdomain reaches the parent-domain route's browser.
- [ ] An app route wins when the URL also matches a website route.
- [ ] Earlier rules win when partial app patterns overlap.
- [ ] An unmatched link reaches the configured default destination.
- [ ] A browser already running receives the link without a duplicate profile.
- [ ] A malformed config reports an error rather than silently replacing it.

## Migration And Direct Editing

- [ ] With no Choosium config, an existing Hyprchoosy config appears in the UI.
- [ ] The migration notice is visible.
- [ ] Saving creates the Choosium config and does not modify the old file.
- [ ] Rule order from the old file is retained.
- [ ] A valid hand edit is visible after **Refresh**.
- [ ] A hand edit made while the panel is open causes the next stale UI save to conflict.
- [ ] Reloading after the conflict preserves the hand edit.

## Removal

- [ ] Set another browser as the default before removal.
- [ ] Removing the plugin unloads its bar widget and service.
- [ ] The optional config and desktop-entry cleanup commands remove all Choosium-owned files.
