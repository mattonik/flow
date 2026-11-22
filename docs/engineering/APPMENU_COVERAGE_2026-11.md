# App-menu coverage over AT-SPI — November 2026

Phase 1 of the universal-menu plan: can Flow's universal menu rely on AT-SPI? This document measures Flow's existing reader against a fixed list of apps. No product code was changed. The spec's contextual app menu is still **approved to test** (D-012, [master spec §8.1](../Flow_MASTER_SPEC_v1.1.md)), and this answers open question 8: *does toolkit coverage justify visible menu chrome?*

## Answer

**No, not as chrome that is always there.** Of the ten apps in the brief, four have a menu the reader can use:
- Kate (Qt 6);
- Mousepad (GTK 3);
- LibreOffice Writer, once a reader bug is fixed (finding 1);
- DB Browser for SQLite (Qt 5), but only when accessibility was on before the app started (finding 9).

For the other six, the reader gets nothing it can invoke, and for most of them there is nothing to get:
- Nautilus and GNOME Text Editor (GTK 4 / libadwaita) have no menu bar.
- Firefox's menu bar is hidden, and its 7 menus have no items until opened.
- Chromium has no menu bar.
- VSCodium (Electron) exposes no menu bar at all unless renderer accessibility is forced, and with it, seven top-level labels or an empty bar.
- foot is not on the accessibility bus.

X11 apps add a seventh case: VLC's menu is readable, but Niri gives its window xwayland-satellite's PID, so the reader can't find it.

The browser, Electron, GNOME and terminal rows are the apps people use most, and all of them come up empty. So the menu should stay a conditional affordance: the App button shows only when the focused window's menu read returned items. Native menus stay, and no screen space is reserved for a menu strip.

Where AT-SPI does work, it works well:
- About ran through `doAction` in all four apps (in DB Browser the call timed out, finding 3).
- Reads take 0.2–0.8 s (median of three; LibreOffice's first read just after accessibility came on took 0.87 s).
- Menus come back with their items, a few dropped nodes aside (finding 2).
- In Kate, windows that share a process can be told apart, provided the frame is chosen before Flow's menu opens.

Four things in the reader need fixing before it can be trusted:
- the desktop-file `TypeError`;
- multi-window narrowing, which has to happen when the menu opens rather than when the command runs, because no frame stays `active` under Flow's own menu surface;
- the modal-dialog timeout;
- the PID-only lookup that misses X11 apps.

There are also two deployment facts. The session must start with at-spi2-core already enabled, because a session started before that exports `NO_AT_BRIDGE=1` and `GTK_A11Y=none` to every app (finding 6). And the accessibility switch the reader turns on is persisted in the user's dconf (finding 9).

## Table

The tables below were generated with `tests/appmenu_coverage.py --table docs/engineering/appmenu-coverage-2026-11/run.json` from the one run committed there. That run used the script as of commit f3cf54c; later commits only hardened its isolation and added tests, and changed nothing it measures. The run took place on 2026-11-22 in the UTM ARM64 guest, on one 1280×800 output: NixOS 26.11.20261107, Niri 26.04 and at-spi2-core 2.62.0 (`env.versions` in the JSON), with Qt 6.12.0 and 5.15.19 and GTK 3.24.52 and 4.24.0 (from the guest's store, not recorded in the JSON). The apps' store paths are in `env.roots` and `env.system`: Kate 26.08.3, DB Browser for SQLite 3.13.1, VLC 3.0.24, LibreOffice 26.8.0.3, Mousepad 0.7.0, Nautilus 51.0.1, GNOME Text Editor 51.0, Firefox 159.0.1, Chromium 157.0.8103.38, VSCodium 1.143.17183, foot 1.28.0.

Every row is one window, opened on an empty workspace and focused. Each launch had its own session bus, accessibility bus, runtime directory and home (see *Method*).

<!-- generated table: start -->
| App | Toolkit | On a11y bus | Menu bars (showing) | Reader items, as is | Items, app_name fixed | Nodes visited, capped (uncapped) | Dropped menu nodes | Walk ms | Reader s | Shortcut labels | Command via doAction | Windows of PID | Late enable, 1st / 2nd call |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Kate | Qt 6 / KF6 | yes | 1 (1) | 186 | 186 | 546 (603) | 6 | 319 | 0.4 | 48 of 186 | Help > About Kate: ran, opened 'About Kate' | 1 | 186 / 186 |
| DB Browser for SQLite | Qt 5 | yes | 1 (1) | 67 | 67 | 815 (821) | 0 | 369 | 0.46 | 16 of 67 | Help > About: error after 15.15 s (atspi_error: Did not receive a reply. Po), opened 'About DB Browser for SQLite' | 1 | 0 / 0 |
| VLC | Qt 5 on X11 (xwayland-satellite) | no (window PID 1022); own PID 534556: yes | — | 0 | 0 | — | — | — | 0.08 | — | no item | 1 | 0 / 0 |
| LibreOffice Writer | VCL, gtk3 plugin | yes | 2 (2) | error | 1385 | 2030 (2113) | 1 | 575 | 0.77* | 190 of 1385 | Help > About LibreOffice: ran, opened 'About LibreOffice' | 1 | 1385 / 1385 |
| Mousepad | GTK 3 | yes | 1 (1) | 230 | 230 | 327 (327) | 1 | 104 | 0.19 | 37 of 230 | Help > About: ran, opened 'About mousepad' | 1 | 230 / 230 |
| Mousepad, login session's environment | GTK 3 | no (window PID 535524); own PID 535524: no | — | 0 | 0 | — | — | — | 0.08 | — | no item | 1 | 0 / 0 |
| Nautilus | GTK 4 / libadwaita | yes | 0 (0) | 0 | 0 | 25 (140) | 0 | 10 | 0.09 | — | no menu | 1 | 0 / 0 |
| GNOME Text Editor | GTK 4 / libadwaita | yes | 0 (0) | 0 | 0 | 27 (83) | 0 | 13 | 0.12 | — | no menu | 1 | 0 / 0 |
| Firefox | Gecko, GTK 3 | yes | 1 (0) | 0 | 0 | 117 (118) | 7 | 23 | 0.14 | — | no item | 1 | 0 / 0 |
| Chromium | Chromium views | yes | 0 (0) | 0 | 0 | 3 (3) | 0 | 1 | 0.1 | — | no menu | 1 | 0 / 0 |
| Chromium --force-renderer-accessibility | Chromium views | yes | 0 (0) | 0 | 0 | 218 (218) | 0 | 31 | 0.14 | — | no menu | 1 | 0 / 0 |
| VSCodium, own title bar (default) | Electron 43 | yes | 0 (0) | 0 | 0 | 3 (3) | 0 | 1 | 0.09 | — | no item | 1 | 0 / 0 |
| VSCodium, own title bar, --force-renderer-accessibility | Electron 43 | yes | 1 (1) | 0 | 0 (uncapped 7) | 29 (181) | 0 | 7 | 0.11 | 0 of 7 | no item | 1 | 0 / 0 |
| VSCodium, native title bar | Electron 43 native menu bar | yes | 0 (0) | 0 | 0 | 3 (3) | 0 | 1 | 0.1 | — | no item | 1 | 0 / 0 |
| VSCodium, native title bar, --force-renderer-accessibility | Electron 43 native menu bar | yes | 1 (1) | 0 | 0 | 41 (196) | 0 | 8 | 0.09 | — | no item | 1 | 0 / 0 |
| foot | none | no (window PID 542145); own PID 542145: no | — | 0 | 0 | — | — | — | 0.09 | — | no menu | 1 | 0 / 0 |

Focused:

| App | Focused window | Windows of PID | With this title | Frames | Match title | Active | Both | Resolved | Items in that frame | Frame walk ms |
|---|---|---|---|---|---|---|---|---|---|---|
| kate | Welcome  — Kate | 2 | 2 | 2 | 2 | 1 | 1 | yes | 186 | 324 |
| kate | Welcome  — Kate | 2 | 2 | 2 | 2 | 1 | 1 | yes | 186 | 316 |
| nautilus | Home | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 11 |
| nautilus | flow-appmenu-nautilus-ldf_w6ll | 3 | 1 | 3 | 1 | 1 | 1 | yes | 0 | 9 |
| nautilus | Home | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 9 |
| firefox | Mozilla Firefox | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 21 |
| firefox | Gort! Klaatu barada nikto! — Mozilla Firefox | 3 | 1 | 3 | 1 | 1 | 1 | yes | 0 | 27 |
| firefox | Mozilla Firefox | 3 | 2 | 3 | 2 | 1 | 1 | yes | 0 | 20 |


Focused, exclusive-keyboard layer up:

| App | Focused window | Windows of PID | With this title | Frames | Match title | Active | Both | Resolved | Items in that frame | Frame walk ms |
|---|---|---|---|---|---|---|---|---|---|---|
| kate | Welcome  — Kate | 2 | 2 | 2 | 2 | 0 | 0 | no | — | — |
| kate | Welcome  — Kate | 2 | 2 | 2 | 2 | 0 | 0 | no | — | — |
| nautilus | Home | 3 | 2 | 3 | 2 | 0 | 0 | no | — | — |
| nautilus | flow-appmenu-nautilus-ldf_w6ll | 3 | 1 | 3 | 1 | 0 | 0 | no | — | — |
| nautilus | Home | 3 | 2 | 3 | 2 | 0 | 0 | no | — | — |
| firefox | Mozilla Firefox | 3 | 2 | 3 | 2 | 0 | 0 | no | — | — |
| firefox | Gort! Klaatu barada nikto! — Mozilla Firefox | 3 | 1 | 3 | 1 | 0 | 0 | no | — | — |
| firefox | Mozilla Firefox | 3 | 2 | 3 | 2 | 0 | 0 | no | — | — |


Ground truth (in a fresh pair of windows, File > New through the frame chosen for one):

| App | Focused window | Windows with its title | Frame invoked through | Invoked | Window that should change | Windows whose title changed | Correct |
|---|---|---|---|---|---|---|---|
| kate | 2939 (Welcome  — Kate) | 2 | focused | yes | 2939 | [2939] | yes |
| kate | 2942 (Welcome  — Kate) | 2 | focused | yes | 2942 | [2942] | yes |
| kate | 2943 (Welcome  — Kate) | 2 | frame chosen, then layer | yes | 2943 | [2943] | yes |
| kate | 2946 (Welcome  — Kate) | 2 | frame chosen, then layer | yes | 2946 | [2946] | yes |
| kate | 2947 (Welcome  — Kate) | 2 | other window's frame | yes | 2948 | [2948] | yes |
| kate | 2950 (Welcome  — Kate) | 2 | other window's frame | yes | 2949 | [2949] | yes |


The session around the run: accessibility switch (`org.a11y.Status.IsEnabled`) b false before, b false after; dconf database unchanged; home directory entries added none, removed none. Run from 2026-11-22T17:07:21+00:00 to 2026-11-22T17:16:48+00:00.
<!-- generated table: end -->

Columns:
- **On a11y bus:** an AT-SPI application with the window's PID. For a window whose PID belongs to another process, the process the run started is looked up as well.
- **Menu bars (showing):** `menu bar` nodes, and how many of them have `STATE_SHOWING`.
- **Reader items, as is:** the menu items (leaves) that this checkout's `session/appmenu.py` returns. It runs in a fresh process, as the shell runs `flow-appmenu`.
- **Items, `app_name` fixed:** the same reader with only the `TypeError` of finding 1 caught.
- **Nodes visited:** AT-SPI nodes the reader's walk counts under its 2000-node and depth-12 caps, the refused ones past the cap included; in brackets, without the caps.
- **Dropped menu nodes:** menus and menu items that the walk drops because they yield no items: unpopulated submenus, or items whose children aren't menu nodes.
- **Walk ms:** the reader's walk alone, including its state reads.
- **Reader s:** the whole reader call as is, median of three, including about 0.1 s of process start. `*` marks the fixed reader, used where the reader as is fails.
- **Shortcut labels:** items whose `Action.getKeyBinding` holds a shortcut. A Qt mnemonic (`Alt+N` on an item without a shortcut) doesn't count.
- **Command:** a harmless command, run through the reader's `activate` path (`doAction(0)`), whose result must be a new window of the app's process. The window was closed again.
- **Windows of PID:** how many Niri windows the window's process owns.
- **Late enable:** a window opened while the accessibility switch was off, then read twice: the first read switches it on, and the second comes 3 s later.

**Windows sharing one process.** The reader gives up when the focused window's process owns more than one Niri window, which is common: every Kate, Firefox, Nautilus and LibreOffice window of a session belongs to one process. For each of three apps, the run opened several windows in one process, two of them with the same title, and focused each in turn. Then it listed the app's accessible frames and checked which match Niri's window title, which are `active`, and which are both. It did this once with only the window focused, and once with an exclusive-keyboard layer surface over it, as Flow's own menu surface is (`session/shell.qml`, `WlrKeyboardFocus.Exclusive`).

For the ground truth, each case opened a fresh pair of Kate windows with one title. The run chose the frame for the focused window, either while it was focused or just before the layer went up, then ran File > New through that frame's own menu and checked which Niri window changed title. As a negative control, two more cases ran it through the other window's frame; there the other window has to change. *Items in that frame* walks each frame with its own cap, whereas the reader caps the whole app.

## Findings

1. **The reader fails outright when it can't find the app's desktop file.** `app_name()` calls `GioUnix.DesktopAppInfo.new()`, which raises `TypeError: constructor returned NULL` in two cases: when no desktop file is named after the `app_id`, and when the file's `Exec` program is not on `PATH`. The reader catches only `ImportError` and `ValueError`. So `flow-appmenu`, and its `identity` mode, return `{"error": "constructor returned NULL"}` instead of the menu. Examples:
   - LibreOffice Writer's `app_id` is `libreoffice-writer`, but its desktop file is `writer.desktop`: this table.
   - Kate built outside a profile: its desktop file was on `XDG_DATA_DIRS`, but `kate` was not on `PATH`. Checked by hand.
   - The Electron default app, with `app_id` `electron`: a preliminary run, not in the committed data.

   For LibreOffice, the walk itself returns all 1385 items and About runs. For the test, the apps' own `share` and `bin` directories are put on `XDG_DATA_DIRS` and `PATH`, as an installed app's would be. CI didn't catch the bug because `tests/test_appmenu.py` mocks `DesktopAppInfo.new` as returning `None`, which PyGObject never does: it raises.
2. **Qt and GTK 3 menus are read with their items, and commands run.** Kate, DB Browser and Mousepad each have one showing menu bar. The reader returns 186, 67 and 230 items, and About ran through `doAction` in all three and in LibreOffice. The walk drops 6 menu nodes in Kate, 1 in Mousepad and none in DB Browser; those are submenus or items that yield no items when closed. Whether every visible menu item also appears in AT-SPI was not compared item by item. The caps cut no items here:
   - Kate goes to depth 17, but all its items lie within depth 12.
   - In LibreOffice Writer the walk hit the node cap (2030 counted against 2000, 2113 uncapped). Its menu bars come before its document, so the menus fit, by a margin not measured. Calc and Impress were not tested.
3. **Modal dialogs make a command that ran look like a failure.** DB Browser's About is a modal dialog, so the app replies to `doAction` only when the dialog closes. The reader waited 15.2 s, then exited 1 with `atspi_error: Did not receive a reply`, while the dialog was on screen. Kate's About, which is not modal, returned in 0.43 s. The shell would show that error text, after 15 s, for a command that ran.
4. **LibreOffice exposes two showing menu bars, both in its document frame** (frame index 0). They hold 683 and 702 items, with 11 menus each, so the reader lists 22 top-level menus. They are not identical copies, and the data does not show which one the user sees. Dropping "the duplicate" therefore needs that settled first.
5. **GTK 4 / libadwaita apps have no menu bar to read.** Their primary menus are popovers that exist only while open. GNOME Text Editor's capped walk visits 27 nodes and Nautilus's 25, and neither has a menu bar. That is the design of those apps, not a gap in AT-SPI.
6. **Apps started in a session from before at-spi2-core was enabled are invisible.** The guest's login session predates the Flow module's at-spi2-core. NixOS then exports `NO_AT_BRIDGE=1` and `GTK_A11Y=none`, and they are still in the user manager's environment, so every app the session starts inherits them: Niri, the login Nautilus, the login Firefox.
   - Mousepad started with those two variables (the row "login session's environment") is not on the bus at all.
   - The same Mousepad without them reads all 230 items.
   - An earlier run's "Nautilus not on the bus" was the login Nautilus, for this reason.

   The table describes a session started after at-spi2-core was enabled: the runner clears both variables. **Enabling at-spi2-core takes effect only after a new login.**
7. **Browsers and Electron expose next to nothing:**
   - Firefox has one menu bar, not showing. Its 7 menus have no items, all dropped by the walk.
   - Chromium has no menu bar. Without `--force-renderer-accessibility` it exposes 3 nodes, with the flag 218, but neither has a menu.
   - VSCodium without the flag exposes 3 nodes and no menu bar, whichever title bar it uses.
   - With the flag, its default HTML title bar gives a showing menu bar of seven top-level labels with no items under them. That bar sits below the reader's depth cap (the tree goes 31 levels deep), so the capped walk doesn't find even those labels.
   - With the flag and `"window.titleBarStyle": "native"`, VSCodium exposes Electron's native menu bar: 8 children, none of them a menu node, and no items.

   Forcing renderer accessibility is therefore necessary for Electron, but not sufficient, and it sticks. In an earlier run that passed the flag with the real home directory, VSCodium wrote `"force-renderer-accessibility": true` into `~/.vscode-oss/argv.json`, and later launches without the flag still had it. Here each launch has a fresh home, so only a first launch was measured. A preliminary run found that the Electron default app (`electron` without an app) draws no menu bar on Wayland, so VSCodium stands in for Electron.
8. **X11 apps are readable, but not by the reader.** Niri reports `xwayland-satellite`'s PID (1022, started at login) for VLC's window. VLC's Qt interface runs only under X11. VLC's own process is on the bus, with a showing menu bar: 8 menus, 62 items, 14 dropped nodes, 24 shortcuts. The reader matches only by PID, so it finds nothing.

   Inferred, not measured: since every X11 window carries that one PID, the reader would see two X11 windows as one process with several windows. Matching by `app_id` and title would reach X11 apps.
9. **Qt 5 apps stay unreadable if accessibility comes on after they start, and the switch is persistent.**
   - Kate (Qt 6), Mousepad (GTK 3) and LibreOffice read fully when accessibility was switched on after launch.
   - DB Browser (Qt 5) returned 0 items on both reads, and VLC (Qt 5) was not on the bus 3 s later. Both read fully when started with accessibility on.
   - The switch, `org.a11y.Status.IsEnabled`, is stored by the at-spi bus launcher as the user's GSettings key `org.gnome.desktop.interface toolkit-accessibility` (`g_settings_set_boolean` in the guest's launcher). The reader's first use therefore turns accessibility on for every later login too.

   So the Qt 5 gap lasts until those apps are restarted after the first use, once per user. The other cost is that accessibility stays on for that user's sessions from then on.
10. **Same-process windows can be told apart, but not while Flow's menu is open:**
    - With the window focused, title and `active` together picked exactly one frame in all 8 cases (Kate, Nautilus, Firefox). Title alone was ambiguous in 6 of them, and `active` alone picked one frame every time.
    - Under an exclusive-keyboard layer surface, which is how Flow's menu takes focus, **no frame was `active` in any of the 8 cases**. The reader re-checks during `activate`, while that surface is up, so title and `active` there would always fail.
    - The Kate ground truth agrees with the choice made while the window was focused, in all 4 cases, each with two same-titled windows. File > New, sent through the chosen frame's menu, changed only that window's title. That held in both cases where the frame was chosen and the layer then went up before the command.
    - In both negative-control cases, sent through the other window's frame, only the other window changed. So the frame Kate's command goes through decides the window. The command does not simply go to the focused one.

    So narrowing works if the reader chooses the frame when the menu opens, before Flow's surface maps, and remembers it. For Firefox and Nautilus, title and `active` single out one frame, but there is no ground truth for them and no menu in it.
11. **Shortcut labels are partial, and their formats differ.** Kate labels 48 of its 186 items, DB Browser 16 of 67, Mousepad 37 of 230 and LibreOffice 190 of 1385. Qt gives `Ctrl+N` or chords like `Ctrl+T, O`; GTK and LibreOffice give `<Primary>n`. Showing them needs one normaliser per format.
12. **Cost.** A reader call took 0.08–0.14 s for an app without a menu, and 0.19–0.77 s for one with a menu; Kate took 0.40 s. The walk, including its state reads, costs about 0.28–0.58 ms per node: LibreOffice 575 ms for 2030 nodes, Mousepad 104 ms for 327, Kate 319 ms for 546. The reader walks again on every open and every command.

## What it means for open question 8

The question was whether a global app-menu exporter covers Qt, GTK and Electron consistently enough to justify visible chrome. AT-SPI covers Qt 6, GTK 3 and LibreOffice consistently, and Qt 5 provided accessibility is on before the app starts. LibreOffice needs finding 1 fixed, and X11 apps need a lookup other than PID. AT-SPI does not cover GTK 4 / libadwaita, Chromium, Electron, Firefox or terminals.

The research behind the brief found no working alternative on Niri today:
- Niri does not implement KDE's `org_kde_kwin_appmenu`: a proof of concept, [niri-wm/niri#2794](https://github.com/niri-wm/niri/pull/2794), was closed.
- The GTK 4 menu export reaches few apps.
- `xdg_dbus_annotation_v1` ([wayland-protocols!493](https://gitlab.freedesktop.org/wayland/wayland-protocols/-/merge_requests/493), tracked in [niri-wm/niri#3646](https://github.com/niri-wm/niri/issues/3646)) is not merged.

Recommended answer, for the spec and D-012:

- **Do not add permanent menu chrome.** Keep the App button conditional: shown only when the focused window's read returned items. Keep the menu off by default.
- **Keep native menus, always.** One harmless command per app shows that invocation works, not that every command does. Items that appear only while a menu is open (Firefox), or that the walk drops (finding 2), could not be offered at all.
- **Treat AT-SPI menus as an extra command source for the apps that have one,** for the App button and for search's application commands (§8.2). Do not treat them as a menu system.
- **Before any rollout** (status after the resident reader, 2026-11-22: `flow-appmenu serve`, [F-020](../IMPLEMENTATION_STATUS.md)):
  - catch the desktop-file lookup failure, and fix `tests/test_appmenu.py`'s mock (finding 1). *Done:* `TypeError` falls back to the `app_id` or title, and the test's mock raises as PyGObject does.
  - choose and remember the window's frame (title and `active`) when the menu opens (finding 10). *Done, earlier than that:* the reader chooses the frame at the window's first probe, when focus settles and before any Flow surface takes the keyboard, and remembers it until the window closes. The guest smoke's two same-titled Kate windows each got File > New only in themselves.
  - match X11 windows by `app_id` and title (finding 8). *Done:* a window whose PID is xwayland-satellite's is matched among the frames named as its title, preferring apps named as its `app_id`. VLC's menu (8 menus, 60 items) is found.
  - treat a reply timeout as "sent, result unknown", and shorten the wait (finding 3). *Done:* `doAction` waits 1 s (`Atspi.set_timeout`), and a timeout closes the menu with no error; DB Browser's modal About closes the menu within 2 s.
  - find out which of LibreOffice's two menu bars the user sees (finding 4). *Done:* both are showing, but one is 0 pixels high (in the frame's root pane); the one in the scroll pane above the document is 29 pixels high and is the only one listed (11 menus, 683 items).
  - decide, and tell the user, that enabling the menu turns accessibility on persistently (finding 9), and that Electron apps may keep forced renderer accessibility in their own configuration (finding 7). *Done for finding 9:* the setting's text says so, and turning the setting off switches accessibility off again only if Flow switched it on (and no screen reader runs). Finding 7 is moot while Flow never forces renderer accessibility.
  - say that enabling at-spi2-core needs a new login (finding 6). *Done:* in the setting's text.

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

  The committed run took 9½ minutes.
- **Apps:**
  - Firefox, Nautilus and foot are the system's, as Flow installs them.
  - The others were built with `nix build --no-link` from the guest's pinned nixpkgs. No configuration changed and no generation was added.
  - Apps run with ozone set to Wayland where they support it.
- **Isolation:**
  - Every launch runs on a session bus of its own. That bus starts its own accessibility bus. Each launch also gets its own runtime directory, home, XDG directories and profiles. GSettings goes to a key file in its configuration directory, so the private launcher's switch reaches the apps as dconf would.
  - The reader, the probe and `busctl` run only on that bus, and the script refuses to run them without it. The session's accessibility switch, which at-spi persists in dconf, is therefore never set. Apps that write to `$HOME` write to the temporary one; VSCodium writes `~/.vscode-oss`.
  - The run records the session's switch, the dconf database and the home directory's entries before and after; the line under the tables gives them.
  - Only the run's own processes and their windows are measured, closed and stopped. A process is the run's own if it is this user's and carries the run's marker in its environment, names one of the run's temporary directories in its command line (VSCodium's main process detaches and rewrites its environment's memory), or descends from such a process.
  - An X11 window is taken by its `app_id`, so another session's VLC window would be taken too.
  - Earlier development runs, before this isolation, did write to the shared guest:
    - VSCodium created `~/.vscode-oss` and `~/.vscode-oss-shared`, which were moved out of the home directory.
    - The switch was written to the user's dconf, which now holds an explicit `toolkit-accessibility=false`. If the key was unset before, `dconf reset /org/gnome/desktop/interface/toolkit-accessibility` would restore it exactly.

    - Some of the 35 stale `at-spi2-*` directories in the user's runtime directory likely come from those runs as well.

    Finding 9's dconf claim rests on those runs and on the launcher's code, since this run, by design, cannot show it.
- **Measurements:**
  - The reader runs as a subprocess, exactly as the shell starts it.
  - The probe runs in a separate fresh process (no stale pyatspi cache). Its `menu_walk` is the reader's walk, state reads included, with counters. Unit tests run both walks on one tree, including trees at the depth and node caps, and count both walks' state reads.
  - Commands go through the reader's own `activate` path, with only the `app_name` exception caught.
  - The layer surface is a 48-pixel Quickshell overlay with exclusive keyboard focus. Before measuring, the run confirms through `niri msg layers` that the surface is up.
  - Every number comes from a single run in a VM shared with other sessions. Timings vary by up to about 30% between runs.
- **Unit tests:** [`tests/test_appmenu_coverage.py`](../../tests/test_appmenu_coverage.py) covers:
  - the shortcut parsing, the command lookup, the narrowing rule and the ground truth's changed-window rule;
  - that the probe's walk matches the reader's, at the caps and in state reads;
  - that only the run's own processes and windows are taken, closed at a phase's end and stopped by `--cleanup`, detached processes and descendants included, never the run itself and never another user's;
  - that SIGTERM survivors get SIGKILL;
  - that the reader, the probe, `busctl` and every app the run starts refuse to run without the private bus and use it when it is up, apps in their own home and runtime directory;
  - that no variable naming a bus directly (`AT_SPI_BUS_ADDRESS`, `DBUS_STARTER_ADDRESS`) is passed on;
  - that the private bus starts without the session's bus address, in its own runtime and settings, and that a bus that fails to start, or hangs, stops the run;
  - that its clients are found by their environment, and that a phase's end and `restore()` stop the bus and the processes;
  - the ground truth in both modes: correct only when the expected window changed, and the negative control expecting the other window;
  - that a signal restores;
  - that the state file names the phase's directory before anything starts in it.

  Each of these was checked by neutralising the mechanism and watching its test fail. These have no unit test:
  - the check that the reader read the measured window;
  - installing the signal handlers;
  - the before-and-after snapshot of the session;
  - the check that the layer surface is up;
  - saving the state file when a window appears;
  - `--cleanup` removing the temporary directories;
  - `restore()` removing the state file and focusing the original workspace again.
