# App-menu coverage over AT-SPI — November 2026

Phase 1 of the universal-menu plan: can Flow's universal menu rely on AT-SPI? This document measures Flow's existing reader against a fixed list of apps. No product code was changed. The spec's contextual app menu is still **approved to test** (D-012, [master spec §8.1](../Flow_MASTER_SPEC_v1.1.md)), and this answers open question 8: *does toolkit coverage justify visible menu chrome?*

## Answer

**No, not as chrome that is always there.** Of the ten apps in the brief, four give the reader a usable menu: Kate (Qt 6), DB Browser for SQLite (Qt 5), Mousepad (GTK 3) and LibreOffice Writer. LibreOffice only works once a reader bug is fixed (finding 1). For the other six, the reader gets nothing it can invoke, and for most of them there is nothing to get:
- Nautilus and GNOME Text Editor (GTK 4 / libadwaita) have no menu bar.
- Firefox's menu bar has no items until a menu is opened.
- Chromium has no menu bar.
- VSCodium (Electron) exposes either seven top-level labels with no items or an empty bar, with or without `--force-renderer-accessibility`.
- foot is not on the accessibility bus.

X11 apps add a seventh case: VLC's menu is fully readable, but Niri gives its window xwayland-satellite's PID, so the reader can't find it.

The browser, Electron, GNOME and terminal rows are the apps people use most, and all of them come up empty. So the menu should stay a conditional affordance: the App button shows only when the focused window's menu read returned items. Native menus stay, and no screen space is reserved for a menu strip.

Where AT-SPI does work, it works well:
- About ran through `doAction` in all four apps (in DB Browser the call timed out, finding 3).
- Reads take 0.2–0.8 s.
- Menus come back whole.
- Same-process windows can be told apart.

Four things in the reader need fixing before it can be trusted:
- the desktop-file `TypeError`;
- multi-window narrowing, which has to happen when the menu opens rather than when the command runs, because no frame stays `active` under Flow's own menu surface;
- the modal-dialog timeout;
- the PID-only lookup that misses X11 apps.

There is also a deployment requirement: the session must start with at-spi2-core already enabled. A session started before it was enabled exports `NO_AT_BRIDGE=1` and `GTK_A11Y=none` to every app (finding 6).

## Table

The table was generated with `tests/appmenu_coverage.py --table docs/engineering/appmenu-coverage-2026-11/run.json` from the one run committed there. The run took place on 2026-11-22 in the UTM ARM64 guest: NixOS 26.11, Niri 26.04, at-spi2-core 2.62.0, Qt 6.12.0 and 5.15.19, GTK 3.24.52 and 4.24.0, one 1280×800 output. Every row is one window, opened on an empty workspace and focused.

Versions: Kate 26.08.3, DB Browser for SQLite 3.13.1, VLC 3.0.24, LibreOffice 26.8.0.3, Mousepad 0.7.0, Nautilus 51.0.1, GNOME Text Editor 51.0, Firefox 159.0.1, Chromium 157, VSCodium 1.143 (Electron 43), foot 1.28.0.

<!-- generated table: start -->
| App | Toolkit | On a11y bus | Menu bars (showing) | Reader items, as is | Items, app_name fixed | Nodes visited, capped (uncapped) | Empty menus | Walk ms | Reader s | Shortcut labels | Command via doAction | Windows of PID | Late enable, 1st / 2nd call |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Kate | Qt 6 / KF6 | yes | 1 (1) | 186 | 186 | 546 (603) | 0 | 292 | 0.5 | 48 of 186 | Help > About Kate: ran, opened 'About Kate' | 1 | 186 / 186 |
| DB Browser for SQLite | Qt 5 | yes | 1 (1) | 67 | 67 | 815 (821) | 0 | 402 | 0.56 | 16 of 67 | Help > About: error after 15.2 s (atspi_error: Did not receive a reply. Po), opened 'About DB Browser for SQLite' | 1 | 0 / 0 |
| VLC | Qt 5 on X11 (xwayland-satellite) | no (window PID 1022); own PID 505890: yes | — | 0 | 0 | — | — | — | 0.13 | — | no item | 1 | 0 / 0 |
| LibreOffice Writer | VCL, gtk3 plugin | yes | 2 (2) | error | 1385 | 2030 (2113) | 1 | 541 | 0.79* | 190 of 1385 | Help > About LibreOffice: ran, opened 'About LibreOffice' | 1 | 1385 / 1385 |
| Mousepad | GTK 3 | yes | 1 (1) | 230 | 230 | 327 (327) | 1 | 90 | 0.24 | 37 of 230 | Help > About: ran, opened 'About mousepad' | 1 | 230 / 230 |
| Mousepad, login session's environment | GTK 3 | no (window PID 506447); own PID 506447: no | — | 0 | 0 | — | — | — | 0.09 | — | no item | 1 | 0 / 0 |
| Nautilus | GTK 4 / libadwaita | yes | 0 (0) | 0 | 0 | 25 (170) | 0 | 10 | 0.13 | — | no menu | 1 | 0 / 0 |
| GNOME Text Editor | GTK 4 / libadwaita | yes | 0 (0) | 0 | 0 | 27 (83) | 0 | 15 | 0.13 | — | no menu | 1 | 0 / 0 |
| Firefox | Gecko, GTK 3 | yes | 1 (0) | 0 | 0 | 117 (118) | 7 | 28 | 0.11 | — | no item | 1 | 0 / 0 |
| Chromium | Chromium views | yes | 0 (0) | 0 | 0 | 3 (3) | 0 | 1 | 0.14 | — | no menu | 1 | 0 / 0 |
| Chromium --force-renderer-accessibility | Chromium views | yes | 0 (0) | 0 | 0 | 218 (218) | 0 | 44 | 0.16 | — | no menu | 1 | 0 / 0 |
| VSCodium, own title bar (default) | Electron 43 | yes | 1 (1) | 0 | 0 (uncapped 7) | 29 (181) | 0 | 6 | 0.1 | 0 of 7 | no item | 1 | 0 / 0 |
| VSCodium, own title bar, --force-renderer-accessibility | Electron 43 | yes | 1 (1) | 0 | 0 (uncapped 7) | 29 (181) | 0 | 6 | 0.12 | 0 of 7 | no item | 1 | 0 / 0 |
| VSCodium, native title bar | Electron 43 native menu bar | yes | 1 (1) | 0 | 0 | 41 (190) | 0 | 9 | 0.14 | — | no item | 1 | 0 / 0 |
| VSCodium, native title bar, --force-renderer-accessibility | Electron 43 native menu bar | yes | 1 (1) | 0 | 0 | 41 (196) | 0 | 8 | 0.1 | — | no item | 1 | 0 / 0 |
| foot | none | no (window PID 511886); own PID 511886: no | — | 0 | 0 | — | — | — | 0.12 | — | no menu | 1 | 0 / 0 |

Focused:

| App | Focused window | Windows of PID | With this title | Frames | Match title | Active | Both | Resolved | Items in that frame | Frame walk ms |
|---|---|---|---|---|---|---|---|---|---|---|
| kate | Welcome  — Kate | 2 | 2 | 2 | 2 | 1 | 1 | yes | 186 | 292 |
| kate | Welcome  — Kate | 2 | 2 | 2 | 2 | 1 | 1 | yes | 186 | 300 |
| nautilus | Home | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 14 |
| nautilus | flow-appmenu-nautilus-an4t90xd | 3 | 1 | 3 | 1 | 1 | 1 | yes | 0 | 10 |
| nautilus | Home | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 10 |
| firefox | Mozilla Firefox | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 22 |
| firefox | Gort! Klaatu barada nikto! — Mozilla Firefox | 3 | 1 | 3 | 1 | 1 | 1 | yes | 0 | 22 |
| firefox | Mozilla Firefox | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 21 |


Focused, exclusive-keyboard layer up:

| App | Focused window | Windows of PID | With this title | Frames | Match title | Active | Both | Resolved | Items in that frame | Frame walk ms |
|---|---|---|---|---|---|---|---|---|---|---|
| kate | Welcome  — Kate | 2 | 2 | 2 | 2 | 0 | 0 | no | — | — |
| kate | Welcome  — Kate | 2 | 2 | 2 | 2 | 0 | 0 | no | — | — |
| nautilus | Home | 3 | 2 | 3 | 2 | 0 | 0 | no | — | — |
| nautilus | flow-appmenu-nautilus-an4t90xd | 3 | 1 | 3 | 1 | 0 | 0 | no | — | — |
| nautilus | Home | 3 | 2 | 3 | 2 | 0 | 0 | no | — | — |
| firefox | Mozilla Firefox | 3 | 2 | 3 | 2 | 0 | 0 | no | — | — |
| firefox | Gort! Klaatu barada nikto! — Mozilla Firefox | 3 | 1 | 3 | 1 | 0 | 0 | no | — | — |
| firefox | Mozilla Firefox | 3 | 2 | 3 | 2 | 0 | 0 | no | — | — |


Ground truth (File > New through the chosen frame's menu):

| App | Window | How the frame was chosen | Invoked | Windows whose title changed | Correct |
|---|---|---|---|---|---|
| kate | 2770 (Welcome  — Kate) | focused | yes | [2770] | yes |
| kate | 2771 (Welcome  — Kate) | focused | yes | [2771] | yes |
| kate | 2770 (Untitled  — Kate) | frame chosen, then layer | yes | [2770] | yes |
| kate | 2771 (Untitled (2)  — Kate) | frame chosen, then layer | yes | [2771] | yes |
<!-- generated table: end -->

Columns:
- **On a11y bus:** an AT-SPI application with the window's PID. For a window whose PID belongs to another process, the process the run started is looked up as well.
- **Menu bars (showing):** `menu bar` nodes, and how many of them have `STATE_SHOWING`.
- **Reader items, as is:** the menu items (leaves) that this checkout's `session/appmenu.py` returns. It runs in a fresh process, as the shell runs `flow-appmenu`.
- **Items, `app_name` fixed:** the same reader with only the `TypeError` of finding 1 caught.
- **Nodes visited:** AT-SPI nodes the reader's walk visits under its 2000-node and depth-12 caps; in brackets, without them.
- **Empty menus:** menus the walk drops because they have no items.
- **Walk ms:** the walk alone.
- **Reader s:** the whole reader call as is, median of three. `*` marks the fixed reader, used where the reader as is fails.
- **Shortcut labels:** items whose `Action.getKeyBinding` holds a shortcut. A Qt mnemonic (`Alt+N` on an item without a shortcut) doesn't count.
- **Command:** a harmless command, run through the reader's `activate` path (`doAction(0)`), whose result must be a new window of the app's process. The window was closed again.
- **Windows of PID:** how many Niri windows the window's process owns.
- **Late enable:** a window opened while `org.a11y.Status.IsEnabled` was false, then read twice: the first read switches accessibility on, and the second comes 3 s later.

**Windows sharing one process.** The reader gives up when the focused window's process owns more than one Niri window, which is common: every Kate, Firefox, Nautilus and LibreOffice window of a session belongs to one process. For each of three apps, the run opened several windows in one process, two of them with the same title, and focused each in turn. Then it listed the app's accessible frames and checked which match Niri's window title, which are `active`, and which are both. It did this once with only the window focused, and once with an exclusive-keyboard layer surface over it, as Flow's own menu surface is (`session/shell.qml`, `WlrKeyboardFocus.Exclusive`). For Kate it then ran File > New through the chosen frame's own menu and checked which Niri window changed title. That is the ground truth.

## Findings

1. **The reader fails outright when it can't find the app's desktop file.** `app_name()` calls `GioUnix.DesktopAppInfo.new()`, which raises `TypeError: constructor returned NULL` in two cases: when no desktop file is named after the `app_id`, and when the file's `Exec` program is not on `PATH`. The reader catches only `ImportError` and `ValueError`. So `flow-appmenu`, and its `identity` mode, return `{"error": "constructor returned NULL"}` instead of the menu. Examples:
   - LibreOffice Writer's `app_id` is `libreoffice-writer`, but its desktop file is `writer.desktop`: this table.
   - Kate built outside a profile: its desktop file was on `XDG_DATA_DIRS`, but `kate` was not on `PATH`. Checked by hand.
   - Electron's default app, with `app_id` `electron`: a preliminary run, not in the committed data.

   For LibreOffice, the walk itself returns all 1385 items and About runs. For the test, the apps' own `share` and `bin` directories are put on `XDG_DATA_DIRS` and `PATH`, as an installed app's would be. CI didn't catch the bug because `tests/test_appmenu.py` mocks `DesktopAppInfo.new` as returning `None`, which PyGObject never does: it raises.
2. **Qt and GTK 3 menus are complete, and commands run.** Kate, DB Browser and Mousepad each have one showing menu bar. The reader returns their whole tree: Kate 186 items, DB Browser 67, Mousepad 230, with submenus, disabled state and check items. Mousepad has 1 empty menu (one without items) and Kate and DB Browser none, so almost nothing is lost to submenus filled only when opened. About ran through `doAction` in all three and in LibreOffice. The caps cut nothing:
   - Kate goes to depth 17, but all its items lie within depth 12.
   - LibreOffice visits 2030 nodes (capped at 2000), but its menu bars come before its document.

   Whether every visible menu item also appears in AT-SPI was not compared item by item.
3. **Modal dialogs make a command that ran look like a failure.** DB Browser's About is a modal dialog, so the app replies to `doAction` only when the dialog closes. The reader waited 15.2 s, then exited 1 with `atspi_error: Did not receive a reply`, while the dialog was on screen. Kate's About, which is not modal, returned in 0.47 s. The shell would show *declined* after 15 s for a command that ran.
4. **LibreOffice exposes two menu bars, both showing, in its one frame.** They hold 683 and 702 items, with 11 menus each, so the reader lists 22 top-level menus. They are not identical copies, and the data does not show which one the user sees. Dropping "the duplicate" therefore needs that settled first.
5. **GTK 4 / libadwaita apps have no menu bar to read.** Their primary menus are popovers that exist only while open. GNOME Text Editor's capped walk visits 27 nodes and Nautilus's 25, and neither has a menu bar. That is the design of those apps, not a gap in AT-SPI.
6. **Apps started in a session from before at-spi2-core was enabled are invisible.** The guest's login session predates the Flow module's at-spi2-core. NixOS then exports `NO_AT_BRIDGE=1` and `GTK_A11Y=none`, and they are still in the user manager's environment, so every app the session starts inherits them: Niri, the login Nautilus, the login Firefox.
   - Mousepad started with those two variables (the row "login session's environment") is not on the bus at all.
   - The same Mousepad without them reads all 230 items.
   - An earlier run's "Nautilus not on the bus" was the login Nautilus, for this reason.

   The table itself describes a session started after at-spi2-core was enabled: the runner clears both variables. **Enabling at-spi2-core takes effect only after a new login.**
7. **Browsers and Electron expose next to nothing:**
   - Firefox has one menu bar, not showing, with 7 menus that stay empty until opened.
   - Chromium has no menu bar. Without `--force-renderer-accessibility` it exposes 3 nodes, with the flag 218, but neither has a menu.
   - VSCodium's default title bar draws its menu in HTML. AT-SPI shows a showing menu bar with seven top-level labels and no items under them. It sits below the reader's depth cap (its tree goes 31 levels deep), so the capped walk doesn't find even those labels.
   - With `"window.titleBarStyle": "native"`, VSCodium exposes Electron's native menu bar, but with nothing under it.
   - `--force-renderer-accessibility` changed none of the VSCodium numbers. The Electron default app (`electron` without an app) draws no menu bar on Wayland, so VSCodium stands in for Electron.
8. **X11 apps are readable, but not by the reader.** Niri reports `xwayland-satellite`'s PID (1022, started at login) for VLC's window. VLC's Qt interface runs only under X11. VLC's own process is on the bus, with a showing menu bar: 8 menus, 60 items, 24 shortcuts. The reader matches only by PID, so it finds nothing. Every X11 window carries that one PID, so the reader also sees two X11 windows as a single process with several windows. Matching by `app_id` and title would reach X11 apps.
9. **Qt 5 apps stay unreadable if accessibility comes on after they start.** Kate (Qt 6), Mousepad (GTK 3) and LibreOffice read fully when accessibility was switched on after launch. DB Browser (Qt 5) returned 0 items on both reads, and read fully only when started with accessibility on. Because the reader turns accessibility on at its first use, Qt 5 apps opened before that stay unreadable until they are restarted.
10. **Same-process windows can be told apart, but not while Flow's menu is open:**
    - With the window focused, title and `active` together picked exactly one frame in all 8 cases (Kate, Nautilus, Firefox). Title alone was ambiguous in 6 of them, and `active` alone picked one frame every time.
    - Under an exclusive-keyboard layer surface, which is how Flow's menu takes focus, **no frame was `active` in any of the 8 cases**. The reader re-checks during `activate`, while that surface is up, so title and `active` there would always fail.
    - For Kate, the ground truth agrees with the choice made while the window was focused: File > New, sent through the chosen frame's menu, changed only the focused window. It did so in all 4 runs, including 2 where the layer went up after the choice and before the command.

    So narrowing works if the reader chooses the frame when the menu opens, before Flow's surface maps, and remembers it. For Firefox and Nautilus, narrowing finds the right frame but no menu in it.
11. **Shortcut labels are partial, and their formats differ.** Kate labels 48 of its 186 items, DB Browser 16 of 67, Mousepad 37 of 230 and LibreOffice 190 of 1385. Qt gives `Ctrl+N` or chords like `Ctrl+T, O`; GTK and LibreOffice give `<Primary>n`. Showing them needs one normaliser per format.
12. **Cost.** A reader call took 0.09–0.16 s for an app without a menu, and 0.24–0.79 s for one with a menu. Kate took 0.5 s, faster than the 0.9 s recorded earlier on this guest. The walk costs about 0.27–0.54 ms per node: Mousepad 90 ms for 327 nodes, Kate 292 ms for 546, LibreOffice 541 ms for 2030. The reader walks again on every open and every command.

## What it means for open question 8

The question was whether a global app-menu exporter covers Qt, GTK and Electron consistently enough to justify visible chrome. AT-SPI covers Qt 6, GTK 3 and LibreOffice consistently, and Qt 5 provided accessibility is on before the app starts. LibreOffice needs finding 1 fixed, and X11 apps need a lookup other than PID. AT-SPI does not cover GTK 4 / libadwaita, Chromium, Electron, Firefox or terminals.

The research cited in the brief found no working alternative on Niri today:
- Niri does not implement KDE's `org_kde_kwin_appmenu`;
- the GTK 4 menu export reaches few apps;
- `xdg_dbus_annotation_v1` is not merged.

Recommended answer, for the spec and D-012:

- **Do not add permanent menu chrome.** Keep the App button conditional: shown only when the focused window's read returned items. Keep the menu off by default.
- **Keep native menus, always.** No tested app could have its menu hidden: either the read returned nothing, or a command could not be confirmed (finding 3).
- **Treat AT-SPI menus as an extra command source for the apps that have one,** for the App button and for search's application commands (§8.2). Do not treat them as a menu system.
- **Before any rollout:**
  - catch the desktop-file lookup failure, and fix `tests/test_appmenu.py`'s mock (finding 1);
  - choose and remember the window's frame (title and `active`) when the menu opens (finding 10);
  - match X11 windows by `app_id` and title (finding 8);
  - treat a reply timeout as "sent, result unknown" rather than "declined", and shorten the wait (finding 3);
  - find out which of LibreOffice's two menu bars the user sees (finding 4);
  - turn accessibility on at login when the menu is enabled, which Qt 5 apps need (finding 9), and say that enabling at-spi2-core needs a new login (finding 6).

## Method

- **Script:** [`tests/appmenu_coverage.py`](../../tests/appmenu_coverage.py), run with [`tests/run_appmenu_coverage.sh`](../../tests/run_appmenu_coverage.sh) inside the guest's Niri session. The runner:
  - takes the Python with pyatspi from the installed `flow-appmenu` wrapper;
  - imports `XDG_SESSION_TYPE`, `DISPLAY`, `NIXOS_OZONE_WL` and `WAYLAND_DISPLAY` from the user manager;
  - clears `NO_AT_BRIDGE` and `GTK_A11Y` (finding 6);
  - holds `/tmp/floe-screen.lock` for the whole run.

  ```bash
  FLOW_NIX="sudo -n nix" FLOW_NIX_ARGS="--option ssl-cert-file /etc/floe-test/host-trust.pem --option flake-registry ''" \
    tests/run_appmenu_coverage.sh --json /tmp/appmenu-coverage.json
  ```

  Other options:
  - `--only kate,writer` restricts the run to some apps;
  - `--dump kate` prints an app's menu tree;
  - `--table run.json` prints this document's tables from a run;
  - `--cleanup` undoes a run that was killed.

  A full run takes about 35 minutes.
- **Apps:**
  - Firefox, Nautilus and foot are the system's, as Flow installs them.
  - The others were built with `nix build --no-link` from the guest's pinned nixpkgs. No configuration changed and no generation was added.
  - Apps run with ozone set to Wayland where they support it.
- **Measurements:**
  - The reader runs as a subprocess, exactly as the shell starts it.
  - The probe runs in a separate fresh process (no stale pyatspi cache). Its `menu_walk` is the reader's walk with counters, and a unit test runs both walks on one tree, so the two can't drift apart. With and without the caps, the probe counts nodes, menu bars and empty menus and times the walk. It also reads key bindings, describes each menu bar, and lists the app's frames with their names and `active` state.
  - Commands go through the reader's own `activate` path, with only the `app_name` exception caught.
  - The layer surface is a 48-pixel Quickshell overlay with exclusive keyboard focus. Before measuring, the run confirms through `niri msg layers` that the surface is up.
  - Every number comes from a single run in a VM shared with other sessions; treat timings as ±20%. In the previous full run, the walk times agreed within 20%.
- **On a shared session:**
  - Only the run's own windows are measured and closed. A window is the run's own if its process carries the run's marker in its environment, has one of the run's temporary directories in its command line (VSCodium's main process detaches), descends from such a process, or is the app process the run's windows joined. An X11 window is taken by its `app_id`. Other sessions' windows that opened during the runs were left alone.
  - Each launch gets temporary `XDG_CONFIG/DATA/CACHE/STATE_HOME` directories and its own profiles, with GSettings in memory.
  - Nautilus is single-instance, so it runs on a private session bus. That bus gets its own runtime directory, so its accessibility bus can't take the shared `at-spi/bus` socket path, and every process on the bus is stopped afterward. A private bus that doesn't start stops the run.
  - `IsEnabled` is read at the start and put back at the end, also on SIGINT, SIGTERM and SIGHUP. If someone else changed it meanwhile, the run reports that and leaves it alone. It was `false` before and after.
- **Unit tests:** [`tests/test_appmenu_coverage.py`](../../tests/test_appmenu_coverage.py) covers:
  - the shortcut parsing, the command lookup and the narrowing rule;
  - that the probe's walk matches the reader's, caps included;
  - that only the run's own windows and processes are taken, detached ones included;
  - that `IsEnabled` is put back only when untouched;
  - private-bus clients, and a private bus that fails to start;
  - the state file.

  Each safety test fails when its mechanism is neutralised.
