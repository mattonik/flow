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

`tests/niri_performance_smoke.py` starts a development shell with its own state and the app menu on, on the focused display's empty workspace. That workspace holds two terminals and a third shaped as a Layer. Recorders on the shell's PATH log every `flow-spaces`, `flow-search` and `flow-appmenu` it starts. `flow-appmenu` is a stand-in that answers `null`, as the real one does while the focused workspace has no window. CPU comes from `/proc`: the shell's own, and its reaped children's. The shell times its own surfaces through an `open_took` IPC call, from the request to the content. For Desk, the content is a `flow-spaces` answer started after Desk opened. Run it in the guest with the installed shell stopped:

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
