# UTM performance observations

2026-11-11, existing ARM64 NixOS VM, UTM QEMU/virgl, 8 GiB RAM, one display, with Nautilus, Kate, KWrite, Ark, two terminals and the Flow shell open. This is a small interactive development VM, not a controlled benchmark or a measure of physical-device performance.

- At the sampling point, Linux reported 7,897 MiB RAM total and 6,626 MiB available; the root filesystem had 9.4 GiB free (71% used). No swap was configured.
- Process RSS at that point: Quickshell about 158 MiB, Niri about 156 MiB, Nautilus about 206 MiB, Kate about 135 MiB. RSS includes shared pages and these figures must not be summed to estimate total use.
- Three `flow-spaces refresh` invocations took 53–73 ms wall time. This is a process-launch-plus-IPC measurement, not a frame-time measurement; the shell currently refreshes every three seconds.
- Reading the focused Kate accessibility menu took 887–936 ms across three runs before the change and 907–926 ms after it. The expensive full traversal remains on demand when opening the menu.
- The new focused-application identity request, which does not traverse accessibility menus, took 41–55 ms over three runs. Flow uses it on focus changes instead of reading the entire menu eagerly.

Measurements were taken with the VM running interactively and its caches warm, using wall-clock timestamps; they are indicative only. CPU percentages from `ps` are lifetime averages and are not an idle-load benchmark. No controlled startup latency, frame pacing, GPU utilization, battery usage, touch latency or physical hardware measurements were collected. For useful comparisons, repeat with the same guest configuration, workload and sample method; measure frame times separately before making smoothness claims.

## Event-driven space refresh

The shell now schedules a debounced refresh for Niri window and workspace events, with a 30-second fallback while the event stream is running and a three-second fallback if it stops. It retries the event stream after five seconds. In the ARM64 VM, a temporary wrapper counted two startup refreshes and no further refreshes over the next ten idle seconds; changing focus caused new refreshes. The switcher displayed the existing applications and stack. Killing the event-stream process resulted in a new event-stream process after the retry. This is a process-count smoke test, not a CPU or battery benchmark.

## Under a stream of compositor events (2026-11-22)

An audit of the guest found idle cheap (Quickshell 0.17% of a core, 177 MB RSS) but every Niri event costly. Each event restarted a 150 ms timer that then ran `flow-spaces refresh`. A terminal retitling itself every 0.3 s caused a refresh per title, and one retitling every 0.05 s held the timer off, so the shell's state stayed stale for as long as the titles kept changing. Each `flow-spaces` run took 58–81 ms, of which Flow's own logic was about 2 ms. Four `niri msg` processes took 24–31 ms, compiling `spaces.py` 13 ms, and the bash wrapper also forked `id`. Two timers polled when nothing happened. The opt-in app menu asked `flow-appmenu identity` every 1.5 s while it held no menu, for example on an empty workspace. Automatic layout ran `flow-spaces auto-layout` every 2 s while a Group had automatic layout or a shaped Layer was open. Search ran a writing `flow-spaces refresh` inside `flow-search`.

What changed:

- **Refreshes are coalesced.** A refresh runs 150 ms after the last Niri event, but never more than 1 s after the first unanswered one, and never sooner than 0.5 s after the previous refresh began. These are three timers, not clock arithmetic, so a clock that jumps cannot hold refreshes back. A refresh or automatic layout pass that starts for any reason answers the events before it.
- **A title change needs no refresh.** The shell keeps Niri's windows as the event stream describes them: the full list, opened or changed windows, closed windows, focus, focus timestamps and layouts. A `WindowOpenedOrChanged` that differs from the known window only in its title relabels that window in place. Anything else, or a window not yet known, asks for a refresh. The rules stay in `flow-spaces`, which sends with each label taken from a window's title what the label is without one (`untitled`). A space named after its windows also says how to join their labels (`label_join`). The shell applies only `title || untitled`, and never touches a Group's name. After every `flow-spaces` answer it applies the stream's titles again, because a refresh that read Niri before a title change can finish after it. When the stream stops, its windows are forgotten, so the fallback refreshes' labels stand.
- **`flow-spaces` reads Niri over its socket.** One connection answers outputs, workspaces and windows, about 2 ms for all three. Workspaces are no longer read twice. Actions still go through `niri msg`.
- **Helpers start faster.** Flow's Python is compiled into the store, and the wrappers run `python3 -P -m <module>`. That loads the compiled bytecode and never imports from the caller's folder. `flow-spaces` and `flow-recovery` also pass `-S`, which skips `site` (4 ms). The state writers import `tempfile` only when they write (about 2.7 ms), and the wrappers use `$USER` instead of forking `id`. What is left of a run: the interpreter (6 ms), standard-library imports (about 12 ms for `json`, `pathlib`, `subprocess` and `socket`, which share `re`, `enum` and `collections`), bash (1 ms) and Flow's logic (1–2 ms). A resident process is the next step (phase 2).
- **Search observes.** `flow-search` reads pins and windows with `flow-spaces observe`, which never reconciles pins, moves windows or writes state.
- **The app menu asks on focus.** It asks for the focused app's identity when focus moves, when the menu is turned on, and when Desk, Niri's overview or the menu closes. It never asks twice for the same focus: `identity` reads only Niri, so asking again gives the same answer.
- **Automatic layout runs when it is needed.** `flow-spaces` reports the focused display's orientation. The shell runs a pass when a Group with automatic layout does not suit it: after a rotation, a move or the toggle. A pass that leaves the same Groups unsuited is not repeated until something changes. A display that changes size also asks for a refresh, which reshapes the Layers Flow shaped.

### The performance smoke

`tests/niri_performance_smoke.py` starts a development shell with its own state and the app menu on, on the focused display's empty workspace. That workspace holds two terminals and a third shaped as a Layer. Recorders on the shell's PATH log every `flow-spaces`, `flow-search` and `flow-appmenu` it starts. Since the resident app-menu reader (below), `flow-appmenu` is the real one, and the shell runs on a private session bus of the smoke's own, so the reader switches that bus's accessibility on, never the session's. CPU comes from `/proc`: the shell's own, and its reaped children's. The shell times its own surfaces through an `open_took` IPC call, from the request to the content. For Desk, the content is a `flow-spaces` answer started after Desk opened. Run it in the guest with the installed shell stopped:

```sh
systemctl --user stop flow-shell
FLOW_SHELL=$PWD/session/shell.qml PATH=<this checkout's helpers>:$PATH python3 tests/niri_performance_smoke.py
systemctl --user start flow-shell
```

Main was measured with its own `shell.qml` plus only the read-only instrumentation the smoke reads (`window_labels`, `open_took`), and with the installed helpers, which were main's. The 2026-11-22 change was measured with its shell and with helpers built from its `checks.aarch64-linux.module`. Both runs used the same guest, one after the other.

| Check | Limit | Main | 2026-11-22 |
|---|---|---|---|
| idle: shell CPU | < 1 % of a core | 0.47% | 0.13% |
| idle: flow-spaces runs | <= 3 | 30 ✗ | 2 |
| idle: flow-appmenu runs | <= 3 | 40 ✗ | 0 |
| flow-spaces refresh median | < 30 ms | 66 ms ✗ | 22 ms |
| flow-spaces observe median | < 30 ms | 67 ms ✗ | 22 ms |
| flow-search --json median | < 200 ms | 160 ms | 108 ms |
| flow-search asks flow-spaces | observe only | refresh ✗ | observe |
| Overview shown | < 200 ms | 43 ms | 41 ms |
| Desk with a fresh answer | < 150 ms | 128 ms | 67 ms |
| search rows shown | < 300 ms | 236 ms | 143 ms |
| title every 0.3 s: flow-spaces runs | <= 12 | 37 ✗ | 0 |
| title every 0.3 s: children CPU | < 10 % of a core | 32.02% ✗ | 0.0% |
| title every 0.3 s: shell CPU | < 5 % of a core | 3.78% | 3.87% |
| title every 0.05 s: longest stale label | < 1.5 s | 2.13 s ✗ | 1.04 s |
| title every 0.05 s: shell CPU | < 10 % of a core | 1.63% | 1.87% |
| focus every 0.05 s: longest gap between refreshes | < 1.5 s | 2.0 s ✗ | 1.05 s |
| focus every 0.25 s: flow-spaces runs | <= 16 | 26 ✗ | 12 |
| **Result** | | **fail** (10 of 17 checks) | **pass** (17 of 17) |

Helpers ran 11 times after 3 that were not counted; surfaces opened 5 times. Every sample is in the smoke's output. Main's 2 s gaps come from its automatic-layout poll, which ran only because a shaped Layer was open. Without one, the labels and refreshes stayed stale for as long as the stream lasted. On the title storms the shell's own CPU is unchanged (3.8–3.9% and 1.6–1.9%), while its children's dropped from 32% to nothing.

Desk skips its own refresh when one is already running, as it did before. That run's answer is nearly as fresh, but the check waits for a refresh begun after Desk opened. Once, on the code merged with main, that meant the next event-driven refresh, and Desk read 151–167 ms. The same shell measured 44–45 ms in a separate probe and 51 ms when the smoke was run again.

Helpers are timed with a plain blocking wait. A first version of the smoke passed `timeout=` to `subprocess.run`, and Python then waits for the child by polling at doubling intervals, about 0.5, 1.5, 3.5, 7.5, 15.5, 31.5, 63.5 and 127.5 ms. A 20 ms `flow-spaces` read as 33–35 ms, and main read as 65 or 116–127 ms. `strace` had shown no wait inside the process. Each helper is timed 11 times, after 3 runs that are not counted, because the first run after the guest has been idle takes several times as long.

Each fix was neutralised in turn, keeping its symbols, and the smoke was run again with a 20 s idle phase. Each one fails its own check:

| Neutralised | How | Failing check (limit) |
|---|---|---|
| In-place titles | a title-only change asks for a refresh | title every 0.3 s: 21 runs (≤ 12) |
| Title cap | relabel on every title (1 ms throttle) | shell CPU 7.3% at 0.3 s (< 5), 16.1% at 0.05 s (< 10) |
| Maximum wait | `refreshMaxWait` 10⁸ ms | focus every 0.05 s: 6.05 s without a refresh (< 1.5) |
| Minimum interval | `refreshMinInterval` 1 ms | focus every 0.25 s: 24 runs (≤ 16) |
| App menu on focus | identity timer repeating every 1.5 s | idle: 13 `flow-appmenu` in 20 s (≤ 3 in 60 s) |
| Automatic layout on demand | the 2 s poll restored beside the new check | idle: 10 `flow-spaces` in 20 s (≤ 3 in 60 s) |
| Socket | `snapshot`, `display_state` and `full_state` through `niri msg` again | refresh 43 ms, observe 41 ms (< 30) |
| Bytecode | wrappers run `spaces.py`, `restore.py` and `search.py` by path, with `site` | refresh 42 ms, observe 41 ms (< 30) |
| Search observes | `flow-search` runs `refresh` | asks `refresh` (observe only) |

No variant failed any other check.

The title-every-0.05-s check is guarded by two fixes, the in-place titles and the maximum wait: either one alone keeps the label fresh within 1.5 s. Neutralising one at a time therefore leaves it passing.

## A resident controller (2026-11-22)

Phase 2 moves Flow's refresh into a resident process, `flow-controller` ([ADR-0011](engineering/ADR-0011-resident-controller.md)). The shell starts it. It reads Niri's event stream from Niri's socket into the ADR-0004 projection and refreshes in-process when the events ask for it, by phase 1's rules. It sends the state to the shell and to the Platform API over a Unix socket. The shell keeps no event stream of its own and starts no `flow-spaces` to refresh; the session runs one event stream instead of two, and the API reads state without starting a process.

The performance smoke now counts the controller's refreshes (it reports them on its socket) and adds the controller's CPU to the helpers'. It has three new checks: the helpers' CPU at idle and under focus changes every 0.25 s, and the controller's own refresh time. Phase 1, as installed from main, and the controller were measured one after the other in the guest:

| Check | Limit | Phase 1 (main) | Controller |
|---|---|---|---|
| idle: shell CPU | < 1 % of a core | 0.27% | 0.17% |
| idle: helpers CPU | < 1 % of a core | 0.27% | 0.07% |
| idle: refreshes | <= 3 | 2 | 2 |
| idle: flow-appmenu runs | <= 3 | 0 | 0 |
| flow-spaces refresh median | < 30 ms | 21 ms | 23 ms |
| flow-spaces observe median | < 30 ms | 20 ms | 21 ms |
| flow-search --json median | < 200 ms | 104 ms | 109 ms |
| flow-search asks flow-spaces | observe only | observe | observe |
| Overview shown | < 200 ms | 39 ms | 40 ms |
| Desk with a fresh answer | < 150 ms | 64 ms | 54 ms |
| search rows shown | < 300 ms | 172 ms | 146 ms |
| title every 0.3 s: refreshes | <= 12 | 0 | 11 |
| title every 0.3 s: helpers CPU | < 10 % of a core | 0.0% | 0.55% |
| title every 0.3 s: shell CPU | < 5 % of a core | 4.15% | 2.84% |
| title every 0.05 s: longest stale label | < 1.5 s | 1.08 s | 1.02 s |
| title every 0.05 s: shell CPU | < 10 % of a core | 3.6% | 2.86% |
| focus every 0.05 s: longest gap between refreshes | < 1.5 s | 1.05 s | 1.06 s |
| focus every 0.25 s: refreshes | <= 16 | 12 | 12 |
| focus every 0.25 s: helpers CPU | < 2.5 % of a core | 8.51% ✗ | 1.06% |
| controller refresh mean | < 10 ms | — | 6.16 ms |

Phase 1 fails one check, the one this phase is for: a refresh as a process costs about 20 ms of CPU, so 12 refreshes in 6 s kept 8.5% of a core busy, against 1.1% for the controller. In the controller a refresh takes about 6 ms. Under a title changing every 0.3 s the controller refreshes once a second (11 times), where phase 1 relabelled in the shell. Both stay within the limits, and the shell itself spends less (2.9% against 4.2%), because each refresh hands it the whole state once a second instead of a relabelling per title.

With the controller neutralised to start `flow-spaces refresh` processes instead of refreshing in-process, the smoke fails the helpers' CPU under focus changes (8.2%) and the controller's refresh time (40 ms on average). The controller's own behaviour is unit-tested (`tests/test_flow_controller.py`): when it refreshes, the title-only test (with focus timestamps), the forwarded events, the answer to a refresh asked for and that it skips the minimum interval, retrying every 0.1 s while an action holds the lock without spinning, desktop names read afresh, a client that floods dropped, reshaping left to a process of its own, a failing refresh not stopping the loop, and one controller per runtime folder and Flow state. Each of these, neutralised, fails its test.

## Hidden surfaces and the busy indicator (2026-11-22)

The last part of phase 2. A probe in the guest measured a development shell with five terminals as spaces (`surface-probe`, not committed; the smoke now checks the same). The display runs at 120 Hz.

| Shell, on main | CPU (one core) |
|---|---|
| Idle | 0.5–0.7% |
| Focus moving every 0.25 s | 6.2–6.5% |
| An activity without a known amount, the island showing it | 20% |
| The same, with Overview open | 20.7% |

**The busy indicator.** An activity without a known amount shows a breathing dot in the island, in Overview's island and in Desk's, and a sweeping bar on Desk's card. One clock drove them, a `NumberAnimation` advancing on every frame. Each frame repainted the island's surface, so a 6 px dot held a fifth of a core for as long as the activity lasted. Three variants were tried with the island showing the activity (the Overview figures were taken with Overview's own island left out of the clock, a bug, so they are not given):

| Variant | Island showing |
|---|---|
| Main: every frame | 20% |
| Every frame, only while shown | 19.8% |
| Only while shown, stepping every 80 ms | 3.1% |
| Only while shown, the dot on the render thread (`OpacityAnimator`) | 17.1% |

The cost is the frame rate, not the bindings. So the clock is a `Timer` that steps every 80 ms (12.5 times a second, enough for a breathing dot), every 16 ms while a sweeping bar is on screen (Desk), and advances by elapsed time. The clock runs only while an indicator is on screen: an island whose chip shows a breathing dot (the work island, Overview's, or Desk's, each saying whether its dot breathes) in a window that is shown, or Desk showing a sweeping bar. So it stops for an activity with a known amount, for a chip too narrow for its dot, and with reduced motion. The first version left Overview's own island out and froze a visible dot there; the review found it. A second version counted indicators as they appeared and went away, and missed some: the clock kept running after the activity ended. The smoke caught that through the clock's own IPC call.

**Hidden surfaces.** Desk, Overview, the switcher and the Notifications panel stay hidden most of the time, yet each state push rebuilt their lists (pins, spaces, Layers, Groups' repair cards, activities, notifications), as did the work indicator's Group members while Desk or Overview hid it. A hidden surface's lists are now empty, so it builds nothing. They are built when it opens. Overview builds its cards while it takes the snapshot that comes before it, so its first frame already shows every snapshot: [before and after, 50 ms after opening](screenshots/2026-11-22-overview-first-frame).

The windows themselves were not moved into `LazyLoader`s. Their cost while hidden was the lists, now gone. What stays is their static parts: deleting Desk, Overview, search and the switcher outright took the shell from 175 to 164 MB, the most `LazyLoader`s could save while those surfaces are closed. In exchange, a `LazyLoader` would rebuild a whole window (Desk's ambient grid, its shapes, every binding) on every open, against the 150 and 200 ms budgets, where a list built at open costs a handful of delegates. Code outside the windows also refers to ids inside them in about 50 places. A follow-up could key the lists by ID, so that a visible list does not rebuild its delegates on every push either.

The same probe, run twice on each, main and this change one after the other:

| Shell (one core) | Main | 2026-11-22 |
|---|---|---|
| Idle | 0.4–0.5% | 0.3–0.5% |
| Focus moving every 0.25 s | 6.2–7.1% | 1.1–1.2% |
| An activity without a known amount, the island showing it | 19.4% | 2.7–3.1% |
| The same, with Overview open (its own island breathing) | 20.8–20.9% | 4.7–4.9% |
| RSS at start | 180–181 MB | 174–176 MB |
| RSS after opening Desk, Overview and search once | 191–192 MB | 186 MB |

The performance smoke, main (as installed) and this change one after the other; the rows this change concerns (the focus row from later runs across five spaces). Main's shell has no `busy_clock` call, so its clock rows cannot be read. Its Overview figure ends when Overview is set to show, before any card is built, where this change's ends at the first frame with every card (160 ms, within the 200 ms limit).

| Check | Limit | Main | 2026-11-22 |
|---|---|---|---|
| idle: shell CPU | < 1 % of a core | 0.38% | 0.25% |
| Overview's first frame | < 200 ms | 43 ms | 160 ms |
| Desk with a fresh answer | < 150 ms | 115 ms | 97 ms |
| search rows shown | < 300 ms | 177 ms | 154 ms |
| title every 0.3 s: shell CPU | < 5 % of a core | 2.39% | 0.37% |
| title every 0.05 s: shell CPU | < 10 % of a core | 2.11% | 0.62% |
| focus every 0.25 s: shell CPU (five spaces, later runs) | < 2.5 % of a core | 4.85–5.31% ✗ | 1.06% |
| busy activity, island showing: shell CPU | < 5 % of a core | 20.19% ✗ | 2.8% |
| busy activity, island showing: the dot breathes | every 80 ms | — ✗ | 80 |
| busy activity in Overview: shell CPU | < 5 % of a core | 20.79% ✗ | 4.29% |
| busy activity in Overview: the dot breathes | every 80 ms | — ✗ | 80 |
| busy activity on Desk: the bar sweeps | every 16 ms | — ✗ | 16 |
| an activity with a known amount: the clock stops | stopped | — ✗ | stopped |
| no activity: the clock stops | stopped | — ✗ | stopped |

The other checks pass on both, as in the controller's table above. The whole guest suite passes with this change (the two-display smoke needs a second active output).

Each change neutralised in turn, keeping its symbols (idle 20 s):

| Neutralised | How | Failing check (limit) |
|---|---|---|
| Hidden surfaces build nothing | every list built again while hidden | shell CPU under focus changes every 0.25 s across five spaces: 5.78% (< 2.5) |
| 12.5 Hz | the clock steps every 8 ms | shell CPU with the island showing a busy activity 21.8% (< 5), in Overview 25.8% (< 5); the clock's interval 8 ms (80 and 16) |
| Only while an indicator shows | the clock runs while any activity exists | the clock does not stop for an activity with a known amount |

The focus phase flips across five spaces, since what a push costs grows with the spaces each surface lists. With two, main read 0.6–2.0% and this change 0.6–1.1%, too close to tell apart. With five, this change read 1.06% twice and main 4.85–5.31%; the limit is 2.5%.

## A resident app-menu reader (2026-11-22)

With the opt-in app menu on, the shell used to start `flow-appmenu identity` after every focus change and `flow-appmenu` for every menu it opened and every command, each a new Python with PyGObject (0.08–0.14 s of start-up, plus the menu walk). Now it starts one `flow-appmenu serve` while the setting is on, supervised like the controller, and sends it JSON lines: a probe when focus settles, a read when the menu opens, a command. A window's first probe chooses its accessible frame and remembers it and whether it has a menu; later probes answer from memory with one request on Niri's socket ([F-020](IMPLEMENTATION_STATUS.md)).

The performance smoke runs the real reader now, on its own session bus, adds the reader's CPU to the helpers', and has three changed or new checks: no `flow-appmenu` run at idle, one `flow-appmenu serve` for the whole run, and the median probe of an app without a menu under 50 ms, as the shell times it from its request to the answer. The probes counted are those while a Nautilus window (GTK 4: on the shell's accessibility bus, with no menu bar) opens on the smoke's bus and focus then moves between it and a terminal (on no accessibility bus) every 0.5 s for 5 s. With this change, in the guest:

| Check | Limit | Resident reader |
|---|---|---|
| idle: shell CPU | < 1 % of a core | 0.25% |
| idle: helpers CPU (the reader included) | < 1 % of a core | 0.03% |
| idle: flow-appmenu runs | 0 | 0 |
| app menu: probe of an app without a menu (a terminal, Nautilus), median | < 50 ms | 4 ms |
| app menu: flow-appmenu processes started | 1 (serve) | 1 |
| focus every 0.25 s: helpers CPU | < 2.5 % of a core | 1.06% |

Every other check passed too (30 of 30). The probes were 2–7 ms but one: Nautilus's first, as its window opened, took 348 ms, while it chose the frame and looked for a menu bar in an app still starting up. A window's first probe is paid once; the median is the cost of a focus change. Probes of apps with a menu, measured separately in a fresh reader: a window's first probe 20–40 ms (LibreOffice's first 170 ms), later ones 4–13 ms; reading the whole menu 108 ms (Mousepad, 230 items) to 352 ms (LibreOffice, 683 items).

The shell now runs on the smoke's own session bus, so no figure in this run includes the session bus's traffic; runs before this change are not like for like in that respect. The helpers were the installed ones for `flow-spaces` and `flow-search` (unchanged here) and this checkout's `flow-appmenu`. A first run with wrappers that ran `spaces.py` by path, without bytecode, read 36 ms for refresh and observe, as the bytecode row above predicts, and timed out waiting for the 0.05 s storm's final title; with the installed helpers neither happened.

Each change neutralised in turn, keeping its symbols (idle 20 s):

| Neutralised | How | Failing check (limit) |
|---|---|---|
| A resident reader | each probe also starts a Python that reads the bus, as a process per call would | probe median 91 ms (< 50) |
| The reader blocks until a request comes | a 1 ms GLib timer keeps its loop awake | idle helpers CPU 2.35% (< 1), and 3.62% under focus changes every 0.25 s (< 2.5) |
| No process per focus change | main's `shell.qml`, which starts `flow-appmenu identity` after each focus change | 41 `flow-appmenu` processes (1); no probe timings |

No variant failed any other check but main's shell, whose Desk read 151 ms (< 150) once among its processes.

## Startup, many spaces, frames and HiDPI (2026-11-22)

Phase 3 of the plan was meant for real hardware: frame pacing, the cost of the icons' multisampled layers, battery, snapshots on a HiDPI display, startup time and realistic numbers of apps. No physical machine runs Flow yet, so `tests/niri_scale_probe.py` measures all of it on whatever it runs on and reports the display it ran on. It ran in the UTM guest (ARM64, virgl, 1280 × 800 at 120 Hz, and 3840 × 2160 at 60 Hz and scale 2 for the HiDPI rows); the same command on a laptop gives that laptop's numbers. Frame times come from Qt itself: the probe runs the shell with `qt.scenegraph.time.renderloop` logging on and reads, for every frame of every shell window, the time to sync, render and swap, and the time since the window's previous frame. The guest's GPU timings are virgl's, which says nothing about an Intel, AMD or Arm GPU; what follows separates what holds anywhere from what is the guest's.

**What main measured** (scale 1; empty terminals as spaces, two runs):

| | 5 spaces | 24 spaces |
|---|---|---|
| Startup: IPC answering / first frame / every space shown | 323 / 326 / 708 ms | 364 / 370 / 744 ms |
| Shell memory (RSS) / controller | 175 / 23 MB | 175 / 23 MB |
| Idle, 20 s: frames drawn | 0 | 0 |
| Idle wakeups per second: shell / controller / Niri | 0–0.9 / 0 / 3.4–4.2 | 0–0.8 / 0 / 6.8–7.3 |
| Focus every 0.25 s: shell / helpers CPU | 1.5–2.3% / 0.8–1.1% | 1.7–3.3% / 0.8–1.1% |
| The controller's refresh | 4.0–4.6 ms | 5.0–5.7 ms |
| The state the controller sends the shell | 2.8 KB | 10.6 KB |
| Overview / Desk / search, shell's own timing (medians of 5) | 90–121 / 52–61 / 156–164 ms | 101–123 / 44–52 / 125–190 ms |

Idle holds up: the shell draws nothing, the clock's minute aside, and wakes less than once a second. Its controller does not wake at all. Twenty-four spaces cost the shell no memory at start (before Overview has shown a card; see below for with it) and the controller about a millisecond per refresh; every surface stays within its budget.

**Every surface's window is built afresh each time it shows.** Quickshell 0.3.2 deletes a layer-shell window whenever it is hidden (`WlrLayershell::deleteOnInvisible()` always returns true, to avoid a Wayland protocol error) and builds a new one, with its own OpenGL context and scene graph, when it shows again. So each opening of Desk, Overview or search builds that surface's window, and each closing builds the island, the home indicator and the work indicator again, which hide while Desk or Overview is open. The frame log shows it: an opening draws at least one new window, a closing at least two (counted by window address, which a new window can reuse). What that costs the render threads (sync and render, not the swap's wait for the display) is small: 10–23 ms an opening and 13–21 ms a closing, at either size. The long gaps the log shows, 150–310 ms, are the first frame of a new window waiting on virgl, mostly in its swap. Keeping the three small windows mapped with their content hidden was tried: closing then built nothing, but Overview's opening slowed from 149 to 296 ms, because the small windows now redrew in the same moment as Overview's first frame. Keeping Desk or Overview mapped while hidden is no better a trade: Niri would blend an invisible full-screen layer into every frame it draws. Nothing was changed.

**The icons' multisampled layers.** Each `FlowIcon` draws into a layer with 4× multisampling, because the curve renderer's strokes break up under virgl. With the layers off, the guest showed no difference in any opening or closing, so they stay. Whether they cost anything on a real GPU is for the probe to show there.

**HiDPI: Overview waited for a slow snapshot.** Overview takes a snapshot of the space on screen before it shows. At 3840 × 2160 and scale 2, Overview's first frame took 347–414 ms against its 200 ms budget. Most of it was `grim` scaling the picture down to 640 px wide: on a screen of text that took 172 ms, where the capture at the display's own resolution took 87 ms (16 against 27 ms at 1280 × 800). The scaling costs the same at any size asked for, 164–187 ms from 960 down to 640 px. So the shell now takes the snapshot at the display's resolution, and Overview's card decodes it at the card's own size (Qt adds the display's scale; JPEG scales while it decodes), in the picture's own proportions, so a snapshot taken on another display is not stretched. With 24 spaces at 4K and Overview open, the shell held 180.4 MB against main's 180.7. It shows the same crop: [before and after, at scale 1 and at 4K](screenshots/2026-11-22-native-snapshots). The cost is the files: about 0.9 MB per space at 4K with a screen of text, against 33 KB, in the session's runtime folder, which is memory. With 24 spaces on a 4K display that is about 20 MB; a space's file goes when the space does. Snapshots are also taken whenever a space settles, so this halves their CPU there too.

Overview's opening time (`open_took`) now also waits until every card on screen shows its snapshot, so decoding a larger file cannot hide behind it.

| At 3840 × 2160, scale 2 (8 spaces, medians of 5) | Main | This change | Neutralised (grim scaling again) |
|---|---|---|---|
| Overview's first frame, every card showing its snapshot | 347 and 414 ms ✗ | 179 and 218 ms | 443 ms ✗ |
| Startup to every space shown | 707 and 734 ms | 580 and 599 ms | |

Main's figures end at its first frame with every card, before the cards' pictures were decoded; this change's wait for them. The 218 ms run misses the 200 ms budget: what is left is the new window's first frame at 4K under virgl, about 150 ms.

**Startup waited for a timer.** The shell starts its controller and connects to it on a 250 ms timer, but the controller listens about 30 ms after it starts. It now prints `listening` when it does, and the shell connects on that line. The timer stays as a fallback, should the line not come or the connection drop, and now tries every second: the controller prints the line after every restart as well. The shell shows every space 110–150 ms sooner: 565 against 708 ms with five spaces, 690 against 744 with 24, and the 4K figures above. `tests/test_flow_controller.py` checks that the line comes once the socket takes connections, and not before; printed before, or not at all, the test fails. The controller counts how soon its first watcher (the shell) came, and the performance smoke checks that it was within 100 ms of the controller listening: 8 and 49 ms in two runs. With the shell's side of the line removed it was 964 ms, and the check fails. (With the timer still at 250 ms, a shell without the line came 143–225 ms after the controller listened, and could have come within the limit; that, too, is why the timer is now a second.)

**Not measured:** battery itself (the guest has none; wakeups stand in for it), and frame times on a real GPU. On a laptop: stop `flow-shell` and run `tests/niri_scale_probe.py` as `tests/niri_performance_smoke.py` is run, with `FLOW_SHELL` set.

## Background work: snapshots and automatic saving (2026-11-23)

A review of main after phase 3 found two jobs the shell repeated while nothing had changed, which no check measured. `tests/niri_background_smoke.py` measures both, and is run as `tests/niri_performance_smoke.py` is, with `flow-recovery` and `grim` on PATH. It opens five terminals as spaces, each with an app ID and a desktop entry of its own so automatic saving has nothing to pause for, and starts a development shell with automatic recovery on. Every `grim` and `flow-recovery` the shell starts goes through a recorder that logs it.

**Snapshots of a settled space.** Overview's card for a space shows the picture taken when that space last settled on screen, 0.7 s after it became active. The shell took one on every arrival, also of a space it had just taken that had not changed. Walking back and forth between spaces cost a capture each time: 16 ms of `grim` at 1280 × 800 and 87 ms at 3840 × 2160 (see above). A settled space is now taken again only when its picture is not current. A picture is current when it is under a minute old and was taken of the same look of the space: what the shell knows of it (its windows, their titles and which one is focused, a side-by-side Group's shares) and the display's size. The state of a Group's controls (whether it can widen, narrow, add or remove an app) is not part of the look. Opening Overview from the plain work view takes the space on screen afresh, unless the space is still settling; opened from Desk or a panel, or within 0.7 s of arriving, Overview shows the stored picture, as before.

What a card can miss now is what the look does not hold: a change inside a window that leaves its title alone, a Layer opened or moved over the space, or a change of theme. It can miss these for up to a minute past the visit its picture came from. Before, a card could miss everything since the space's last visit.

**Automatic saving.** With automatic session recovery on, the shell ran `flow-recovery auto-save` every 30 s whether or not anything had changed. A save costs about 59 ms of CPU in the guest (median of 10, with two windows). That is two Python processes, a few Niri requests and a synced write of the saved session. Every 30 s, it comes to 0.2% of a core, about what the idle shell itself spends (0.17% in the audit of 2026-11-22).

The shell now runs a save only while a change is unsaved: a change to the windows, spaces, pins or displays its controller sent. A save records the focused workspace's windows with their titles, columns and focus, the Groups and the display, and checks against the pins: all of it is in those. The one exception is a side-by-side Group's exact widths, which the state rounds to whole percents. A width change smaller than that waits for the next change of another kind (Flow's own resize steps are 10%). `save()` in `session/restore.py` says so, for whoever adds to what it records.

A change counts as saved only once a save has answered without an error. A save that dies without an answer leaves it unsaved, and the next tick saves it. A save also reads Niri itself, which can be between two states the controller never sent: focus overshooting to the next space and coming back before the controller's next refresh, 0.15 to 1 s later. A save that began within a second of a Niri event the shell saw, or of a change, therefore leaves the change unsaved too, and the next tick saves it again. The controller forwards the shell only some of Niri's events (focus and the window list), so a change Niri reports only in the others, undone before the next refresh while a save runs, is still saved wrong until the next change: a column moved and moved back, a title that changes and changes back, a window sent to another workspace and brought back.

The better place for automatic saving is the controller, which holds the exact state it sends and could save from it in-process: no Python start-ups, no list of what a save depends on, and no second read of Niri. That is a larger change, left for later.

| `tests/niri_background_smoke.py` (UTM guest) | Main | This change | Neutralised |
|---|---|---|---|
| A first walk through the five spaces, 2.5 s on each: snapshots (at least 4) | 6 | 5 | |
| A second walk, nothing on them changed: snapshots | 5 ✗ | 0 | 5 ✗ (pictures never current) |
| The helpers' CPU over the second walk | 0.73% | 0.0% | |
| A space whose window's title changed: snapshots when it settles | 1 | 1 | 0 ✗ (look not compared) |
| 65 s with nothing changed, after the last change was saved: automatic saves | 2 ✗ | 0 | 2 ✗ (saving every 30 s again) |
| A focus change: saved by the next automatic save (under 33 s) | 15.9 s | 15.6 s | never ✗ (no change noted) |
| A save within a second of a Niri event (focus overshooting and back around the tick): saves over it and the next tick | 2 | 2 | 1 ✗ (the event not counted) |
| A space whose picture is over a minute old: snapshots when it settles | 1 | 1 | 0 ✗ (age not compared) |
| A window joining another's column, focus unchanged, the first save dying: saved by the next (under 63 s) | 55.2 s | 56.9 s | never ✗ (spaces' changes not noted, or a change marked saved when its save began) |

Main fails two of the nine checks, and this change passes all of them. Each fix, neutralised with its symbols kept, fails its own check (two at a time where they touch different checks; marking a change saved when its save begins also defeats the Niri-event rule). The handlers for pins, other pins and displays are not covered: a save checks against the pins but records none, and a move to another display changes the spaces too. Automatic recovery is still off by default, and the performance smoke runs with it off, so its numbers are unchanged.
