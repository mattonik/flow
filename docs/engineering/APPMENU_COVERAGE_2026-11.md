# App-menu coverage over AT-SPI — November 2026

Phase 1 of the universal-menu plan: can Flow's universal menu rely on AT-SPI? This document measures Flow's existing reader against a fixed list of apps. No product code was changed. The spec's contextual app menu is still **approved to test** (D-012, [master spec §8.1](../Flow_MASTER_SPEC_v1.1.md)), and this answers open question 8: *does toolkit coverage justify visible menu chrome?*

## Answer

**No, not as chrome that is always there.** Of the ten apps in the brief, four give the reader a usable menu: Kate (Qt 6), DB Browser for SQLite (Qt 5), Mousepad (GTK 3) and LibreOffice Writer. LibreOffice only works once a reader bug is fixed (below). For the other six, the reader gets nothing it can invoke, and for most of them there is nothing to get:
- Nautilus and GNOME Text Editor (GTK 4 / libadwaita) have no menu bar.
- Firefox's menu bar shows no items until a menu is opened.
- Chromium has no menu bar.
- VSCodium (Electron) exposes either nothing or seven top-level labels with no items, with or without `--force-renderer-accessibility`.
- foot is not on the accessibility bus at all.
- X11 apps cannot even be mapped to their process.

The browser, Electron, GNOME and terminal rows are the apps people use most, and all of them come up empty. So the menu should stay a conditional affordance: the App button shows only when the focused window's menu read returned items. Native menus stay, and no screen space is reserved for a menu strip.

Where AT-SPI does work, it works well: About ran through `doAction` in all four apps, reads take 0.2–0.8 s, and menus come back whole. A fix that keeps the same model is cheap:
- catch the reader's desktop-file `TypeError`;
- pick the window's frame by title and `active` state;
- remove LibreOffice's duplicate menu bar.

## Table

Measured 2026-11-22 in the UTM ARM64 guest: NixOS 26.11, Niri 26.04, at-spi2-core 2.62.0, Qt 6.12.0 and 5.15.19, GTK 3.24.52 and 4.24.0, one 1280×800 output. Every row is one window, opened on an empty workspace and focused.

| App | Toolkit | On a11y bus | Menu bar | Reader items, as is | Items, `app_name` fixed | Nodes visited, capped (uncapped) | Walk ms | Reader s | Shortcut labels | Command via `doAction` | Windows of PID | Late enable, 1st / 2nd call |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Kate 26.08.3 | Qt 6 / KF6 | yes | yes | 186 | 186 | 546 (603) | 316 | 0.51 | 48 of 186 | Help > About Kate: ran, dialog opened | 1 | 186 / 186 |
| DB Browser for SQLite 3.13.1 | Qt 5 | yes | yes | 67 | 67 | 815 (821) | 388 | 0.54 | 16 of 67 | Help > About: dialog opened, but the call timed out (modal dialog) | 1 | 0 / 0 |
| VLC 3.0.24 | Qt 5 on X11 (xwayland-satellite) | no (PID is xwayland-satellite's) | — | 0 | 0 | — | — | 0.11 | — | not reachable | 1 | 0 / 0 |
| LibreOffice Writer 26.8.0.3 | VCL, gtk3 plugin | yes | yes, twice | **error** | 1385 (each menu twice) | 2030 (2113) | 531 | 0.80 | 175 of 1385 | Help > About LibreOffice: ran, dialog opened | 1 | 1385 / 1385 |
| Mousepad 0.7.0 | GTK 3 | yes | yes | 230 | 230 | 327 (327) | 78 | 0.21 | 37 of 230 | Help > About: ran, dialog opened | 1 | 230 / 230 |
| Nautilus 51.0.1 | GTK 4 / libadwaita | yes, fresh process | no | 0 | 0 | 25 (167) | 13 | 0.12 | — | no menu | 1 | 0 / 0 |
| GNOME Text Editor 51.0 | GTK 4 / libadwaita | yes | no | 0 | 0 | 27 (83) | 11 | 0.12 | — | no menu | 1 | 0 / 0 |
| Firefox 159.0.1 | Gecko, GTK 3 | yes | yes, empty | 0 | 0 | 117 (118) | 20 | 0.14 | — | no items | 1 | 0 / 0 |
| Chromium 157 | Chromium views | yes, 3 nodes | no | 0 | 0 | 3 (3) | 1 | 0.10 | — | no menu | 1 | 0 / 0 |
| VSCodium 1.143, own title bar (default) | Electron 43 | yes | in web content, depth 31 | 0 | 0 (uncapped: 7 labels) | 29 (181) | 6 | 0.12 | 0 | no items | 1 | 0 / 0 |
| — with `--force-renderer-accessibility` | Electron 43 | yes | same | 0 | 0 (uncapped: 7 labels) | 29 (181) | 12 | 0.13 | 0 | no items | 1 | 0 / 0 |
| VSCodium, native title bar | Electron 43 native menu bar | yes | yes, empty | 0 | 0 | 41 (196) | 9 | 0.11 | — | no items | 1 | 0 / 0 |
| — with `--force-renderer-accessibility` | Electron 43 native menu bar | yes | yes, empty | 0 | 0 | 41 (196) | 8 | 0.13 | — | no items | 1 | 0 / 0 |
| foot 1.28.0 | none | no | — | 0 | 0 | — | — | 0.10 | — | — | 1 | 0 / 0 |

Columns:
- **Reader items, as is:** the menu items (leaves) that this checkout's `session/appmenu.py` returns. It runs in a fresh process, as the shell runs `flow-appmenu`.
- **Items, `app_name` fixed:** the same reader with only the `TypeError` described below caught.
- **Nodes visited:** AT-SPI nodes the reader's walk visits under its 2000-node and depth-12 caps; in brackets, without them.
- **Walk ms:** the walk alone. **Reader s:** the whole reader call, median of three.
- **Shortcut labels:** items whose `Action.getKeyBinding` holds a shortcut. Qt reports an item's mnemonic when it has no shortcut; those mnemonics are not counted.
- **Command:** a harmless command, run through the reader's `activate` path (`doAction(0)`), whose result must be a new window. The window was closed again.
- **Windows of PID:** how many Niri windows the window's process owns.
- **Late enable:** a window opened while `org.a11y.Status.IsEnabled` was false, then read twice: the first read switches accessibility on, and the second comes 3 s later.

The raw results are in [`appmenu-coverage-2026-11/run-full.json`](appmenu-coverage-2026-11/run-full.json). The shortcut column comes from a rerun of the four apps that have menus, in [`run-shortcuts.json`](appmenu-coverage-2026-11/run-shortcuts.json); its other columns agree with the full run (walk and reader times within 15%). The first run had counted Qt mnemonics as shortcuts.

### Windows sharing one process

The reader gives up when the focused window's process owns more than one Niri window, which is common: every Kate, Firefox, Nautilus and LibreOffice window of a session belongs to one process. For each of three apps, the test opened several windows in one process, two of them with the same title. With each window focused in turn, it listed the app's accessible frames and checked which ones match Niri's window title, which are `active`, and which are both:

| App | Focused window | Windows of PID | Same title | Frames | Match title | Active | Both | Resolved | Items in that frame | Frame walk ms | Reader items today |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Kate | Welcome — Kate (1st) | 2 | 2 | 2 | 2 | 1 | 1 | yes | 186 | 321 | 0 |
| Kate | Welcome — Kate (2nd) | 2 | 2 | 2 | 2 | 1 | 1 | yes | 186 | 343 | 0 |
| Nautilus | Home (1st) | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 10 | 0 |
| Nautilus | a temporary folder | 3 | 1 | 3 | 1 | 1 | 1 | yes | 0 | 11 | 0 |
| Nautilus | Home (2nd) | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 11 | 0 |
| Firefox | Mozilla Firefox (1st) | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 29 | 0 |
| Firefox | Gort! Klaatu barada nikto! — Mozilla Firefox | 3 | 1 | 3 | 1 | 1 | 1 | yes | 0 | 22 | 0 |
| Firefox | Mozilla Firefox (2nd) | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 16 | 0 |

**Title and active state together single out the focused window in all 8 cases.** Title alone was ambiguous in 5 of them. In every case, exactly one frame was `active`, and it moved with Niri's focus. In both Kate cases the right window's menu was found: 186 items, read in about a third of a second. So narrowing resolves the multi-window ambiguity for Kate.

For Firefox and Nautilus it resolves the ambiguity but finds no menu, which is the single-window result again. Two cases were not tested:
- whether a toolkit keeps its frame `active` while Flow's own menu surface holds keyboard focus;
- LibreOffice with several documents, which is LibreOffice's normal case.

## Findings

1. **The reader fails outright for apps whose desktop file it cannot find.** `app_name()` calls `GioUnix.DesktopAppInfo.new()`, which raises `TypeError: constructor returned NULL` in two cases: when no desktop file is named after the `app_id`, and when the desktop file's `Exec` program is not on `PATH`. The reader catches only `ImportError` and `ValueError`. So `flow-appmenu`, and its `identity` mode, return `{"error": "constructor returned NULL"}` instead of the menu. That hits installed apps too:
   - LibreOffice Writer's `app_id` is `libreoffice-writer`, but its desktop file is `writer.desktop`;
   - Electron's `app_id` is `electron`, which has no desktop file;
   - any app started from `nix shell` or by its full path.

   For LibreOffice, the walk itself returns all 1385 items, and About runs. The table therefore shows the reader both as it is and with the exception caught. For the test, the apps' own `share` and `bin` directories are put on `XDG_DATA_DIRS` and `PATH`, as an installed app's would be.
2. **Qt and GTK 3 menus are complete and invocable.** Kate, DB Browser and Mousepad return their whole menu trees, including submenus, disabled state and check items. About ran through `doAction` in each of them, and in LibreOffice. The caps cut nothing:
   - Kate goes to depth 17, but its items all lie within depth 12.
   - LibreOffice visits 2030 nodes (capped at 2000), but its menu bars come before its document, so all items are inside the cap.
3. **Modal dialogs make a successful command look like a failure.** DB Browser's About is a modal `QDialog::exec()`, so the app replies to `doAction` only once the dialog closes. The reader got `atspi_error: Did not receive a reply` and exited 1 while the dialog was on screen. Kate's About is not modal and returned at once. The shell would report *declined* for a command that ran.
4. **LibreOffice exposes its menu bar twice**: two `menu bar` nodes with the same 11 menus. The reader shows every menu twice, 22 top-level entries.
5. **GTK 4 / libadwaita apps have no menu bar for AT-SPI to read.** Their primary menus are popovers that exist only while open. GNOME Text Editor exposes 27 nodes and Nautilus 25, with no menu bar. This is the design of those apps, not a gap in AT-SPI.
6. **Browsers and Electron expose next to nothing:**
   - Firefox has an accessible menu bar (hidden by default), but no menu items until a menu has been opened: 117 nodes, 0 items.
   - Chromium has no menu bar, and exposed 3 nodes in total.
   - VSCodium's default title bar draws its menu in HTML. AT-SPI shows a menu bar at depth 31, below the reader's depth cap, with seven top-level labels and no items.
   - With `"window.titleBarStyle": "native"`, VSCodium exposes Electron's native menu bar, but with no items under it.
   - `--force-renderer-accessibility` changed none of these numbers. The Electron default app (`electron` without an app) shows no menu bar at all on Wayland, so it was replaced by VSCodium in both configurations.
7. **X11 apps cannot be mapped to their process.** Niri reports `xwayland-satellite`'s PID (1022, started at login) for VLC's window. VLC's Qt interface runs only under X11. Every X11 window therefore carries one PID, which is not on the accessibility bus. With two X11 windows, even the reader's own "several windows" check sees a single process.
8. **Qt 5 apps stay off the bus if accessibility comes on after they start.** Kate (Qt 6), Mousepad (GTK 3) and LibreOffice read fully when accessibility was switched on after launch. DB Browser (Qt 5) returned 0 items on both reads; it read fully only when started with accessibility on. The reader turns accessibility on lazily, on first use. So Qt 5 apps opened before the menu's first use stay unreadable until they are restarted.
9. **Shortcut labels are partial, and their formats differ.** Kate labels 48 of 186 items, DB Browser 16 of 67, Mousepad 37 of 230 and LibreOffice 175 of 1385. Qt gives `Ctrl+N`, or a chord such as `Ctrl+T, O`; GTK and LibreOffice give `<Primary>n`. Showing them needs one normaliser per format.
10. **Cost.** A reader call took 0.10–0.14 s for an app without a menu and 0.21–0.80 s for one with a menu. Kate took 0.4–0.5 s, faster than the 0.9 s recorded earlier on this guest. The walk costs about 0.25–0.6 ms per node: Kate 316 ms for 546 nodes, Mousepad 78 ms for 327. The reader walks again on every open and every invocation.
11. **The long-running Nautilus from login was not on the accessibility bus.** In the first full run, Nautilus showed `on bus: no`: its window joined the session's Nautilus, started at login. A fresh Nautilus process, on a private session bus with its own accessibility bus, registered normally. The cause was not found. GNOME Text Editor, started while accessibility was off, did register.

## What it means for open question 8

The question was whether a global app-menu exporter covers Qt, GTK and Electron consistently enough to justify visible chrome. AT-SPI covers Qt 5, Qt 6, GTK 3 and LibreOffice (after the reader fix) consistently. It does not cover GTK 4 / libadwaita, Chromium, Electron, Firefox, terminals or X11 apps. The research cited in the brief found no working alternative on Niri today:
- Niri does not implement KDE's `org_kde_kwin_appmenu`;
- the GTK 4 menu export reaches few apps;
- `xdg_dbus_annotation_v1` is not merged.

Recommended answer, for the spec and D-012:

- **Do not add permanent menu chrome.** Keep the App button conditional: shown only when the focused window's read returned items. Keep it off by default while the read requires accessibility to be switched on for the whole session.
- **Keep native menus, always.** No tested app could have its menu hidden: either the read returned nothing, or a command could not be confirmed (finding 3).
- **Treat AT-SPI menus as an extra command source for the apps that have one,** for the App button and for search's application commands (§8.2). Do not treat them as a menu system.
- **Before any rollout:**
  - catch the desktop-file lookup failure (finding 1);
  - narrow same-process windows by title and `active` state (the table above);
  - de-duplicate LibreOffice's second menu bar (finding 4);
  - treat a reply timeout as "sent, result unknown" rather than "declined" (finding 3);
  - decide whether accessibility comes on at session start when the menu is enabled, which Qt 5 apps need (finding 8);
  - check `active` while Flow's menu holds keyboard focus.

## Method

- **Script:** [`tests/appmenu_coverage.py`](../../tests/appmenu_coverage.py), run with [`tests/run_appmenu_coverage.sh`](../../tests/run_appmenu_coverage.sh) inside the guest's Niri session. The runner takes the Python with pyatspi from the installed `flow-appmenu` wrapper. It imports the session's `XDG_SESSION_TYPE`, `DISPLAY` and `NIXOS_OZONE_WL` from the user manager, and holds `/tmp/floe-screen.lock` for the whole run:

  ```bash
  FLOW_NIX="sudo -n nix" FLOW_NIX_ARGS="--option ssl-cert-file /etc/floe-test/host-trust.pem --option flake-registry ''" \
    tests/run_appmenu_coverage.sh --json /tmp/appmenu-coverage.json
  ```

  `--only kate,writer` restricts the run to some apps, and `--dump kate` prints an app's menu tree. A full run takes about 25 minutes.
- **Apps:**
  - Firefox, Nautilus and foot are the system's, as Flow installs them.
  - The others were built with `nix build --no-link` from the guest's pinned nixpkgs. No configuration changed and no generation was added.
  - Apps run with ozone set to Wayland where they support it.
- **Isolation:**
  - Each launch gets temporary `XDG_CONFIG/DATA/CACHE/STATE_HOME` directories and its own browser and editor profiles, with GSettings in memory.
  - Nautilus is single-instance and was already running in the session, so it runs on a private session bus. That bus gets its own runtime directory, so its accessibility bus cannot take the shared `at-spi/bus` socket path. Every process on the private bus is stopped afterward.
  - `IsEnabled` is read at the start and put back at the end, also on SIGINT, SIGTERM and SIGHUP. It was `false` before and after every run.
  - Only windows and processes the run opened are closed.
- **Measurements:**
  - The reader runs as a subprocess, exactly as the shell starts it.
  - The probe runs in a separate fresh process (no stale pyatspi cache). It repeats the reader's walk with and without the caps, counting nodes and menu bars and timing the walk. It reads key bindings, and lists the app's frames with their names and `active` state.
  - Commands go through the reader's own `activate` path, with only the `app_name` exception caught.
  - Every number comes from a single run in a VM shared with other sessions. Treat timings as ±20%.
- **Unit test:** [`tests/test_appmenu_coverage.py`](../../tests/test_appmenu_coverage.py) covers the shortcut parsing, the command lookup and the frame-narrowing rule.
