# Screenshots

Screenshots taken while building Flow, committed with the change they show, so the history shows how the shell
looked before and after. Each folder is one piece of work, named by date and topic. All were taken in the UTM test
guest at 1280×800 unless a name says otherwise (`narrow`, `65` for a 0.65 scale); the search `before-*` shots are the user's own crops of the guest's window on their Mac. They show the guest's own test
data only.

| Folder | What it shows |
|---|---|
| [2026-11-21-shell-polish](2026-11-21-shell-polish) | The visual polish pass: `1-before-*`, then `2-review-fix-*` after the review's fixes (with narrow-screen checks), then `3-after-*`, in both appearances. |
| [2026-11-21-installed](2026-11-21-installed) | The installed shell after each NixOS switch that day, named by time: Overview, Desk, quick settings, a banner. |
| [2026-11-21-system-panels](2026-11-21-system-panels) | Quick Settings and Notifications as panels, working shots in the order taken (`HHMM-` prefix); `pl-` are the light appearance. The 20:52 ones are the version that was merged. |
| [2026-11-21-notification-banners](2026-11-21-notification-banners) | A banner in both appearances, the banner gone after its time, a critical banner, and the panel after a banner. |
| [2026-11-22-pointer](2026-11-22-pointer) | The guest's first virtual pointer, a wlrctl probe before `flow-pointer` was written: `1-` and `2-` are the cursor at (640, 400) after one relative move from the corner and after ten, so Niri adds no acceleration (the banner in `2-` is Niri's own, for the screenshot before it); `3-` and `4-` are 200 × 40 crops of the home indicator, then with the pointer on it, wider and in the accent colour. |
| [2026-11-21-search](2026-11-21-search) | Search before the fix (the old fuzzel picker over the shell's panel, and "Nothing matches" on top of the footer) and after it (results with app icons, a ranked query, the no-match message). |
| [2026-11-22-wifi-hwsim](2026-11-22-wifi-hwsim) | GNOME's Wi-Fi panel as Quick settings' Network row opens it in the simulated Wi-Fi test (`tests/niri_wifi_hwsim_smoke.py`): `1-` lists the test network on the simulated radio, `2-` shows it connected through NetworkManager. The signal icons are missing (a broken-image placeholder) and the island covers the panel's title; both are Flow's to fix, not the test's. |
| [2026-11-22-search-files](2026-11-22-search-files) | Search finding files by name while typing, in a demo Documents folder: `1-` the files found for “quarterly”, one of them in a hidden `.drafts` folder; `2-` the second chosen with ↓. |
| [2026-11-22-search-commands](2026-11-22-search-commands) | Commands in search: `1-` the system commands found by “system”, with the power icon; `2-` Restart chosen once, its row asking to be chosen again. |
| [2026-11-22-rejoin-pinned-group](2026-11-22-rejoin-pinned-group) | The switcher with a pinned Group's apps apart and another app after them: `1-before-` the card refusing to rejoin, `2-after-` the card offering *Rejoin Group*. |
| [2026-11-22-overview-first-frame](2026-11-22-overview-first-frame) | Overview 50 ms after it opened, three terminals as spaces: `1-before-` with every card built while Overview was hidden (main), `2-after-` with the cards built while it took its snapshot. `3-before-` and `4-after-from-search-` the same with Overview opened from search, which takes no snapshot first, so the cards are built as it shows. All show every snapshot and the selected card centred; the column order differs only because the terminals opened in another order. |
