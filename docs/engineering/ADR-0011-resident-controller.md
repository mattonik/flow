# ADR-0011: A resident controller for Flow's state

**Status:** Accepted, 22 November 2026

## Context

The shell learned Flow's state by running `flow-spaces refresh`, a Python process, after Niri's events: one process per burst of events, at least 20 ms each even after the startup work of 2026-11-22 (most of it the interpreter and standard-library imports). It also ran its own `niri msg -j event-stream`. The Platform API service ran a second event stream, and a `flow-spaces observe` process for every listing and every authorization. The master specification gives semantic spaces, Groups and pins to a Flow Controller, and ADR-0004 prototyped the projection of Niri's events such a controller would hold.

## Decision

`session/flow_controller.py` (`flow-controller`) is one process per session. It uses only the standard library: a selector loop over two sockets.

- **Niri.** It connects to `$NIRI_SOCKET` and asks for the event stream there (no `niri msg` process). It feeds the events to ADR-0004's `NiriState`, which now also follows focus timestamps, only to tell a title-only change from any other. A refresh still asks Niri for windows, workspaces and outputs on one connection (about 2 ms), because Niri's events are not atomic across workspaces and windows, and outputs have no events.
- **Refreshing.** It runs Flow's refresh in-process: `spaces.run_locked(state, "refresh", emit=False, reshape=False)`, the code and the locks of `flow-spaces refresh`. Phase 1's rules decide when, now in Python and unit-tested: 150 ms after the last structural event, at most 1 s after the first, at least 0.5 s after the previous refresh; a title-only change at once, then at most once a second. There is a safety refresh every 30 s (the first one at start), every 3 s while the stream is down (it reconnects after 5 s), and an immediate one when a client asks.
- **Never waiting.** Every holder of `spaces.lock` takes `session.lock` first. The controller takes `session.lock` without waiting, and while an action holds it, tries again every 0.1 s. A Layer to reshape after its display turned would run Niri actions and wait for them, so the in-process refresh only notes it (`reshape_due`), and the controller starts one `flow-spaces refresh` process to do it. Desktop-entry names are read afresh for each refresh. An unexpected exception in a refresh or an event is logged and becomes an error state; the loop goes on.
- **Clients.** It serves a socket in `$XDG_RUNTIME_DIR` named after a hash of `$XDG_STATE_HOME`, created with umask 077, one JSON object per line. So a development shell with state of its own gets a controller of its own beside the installed one. A watcher gets the state whenever it changes, and the Niri events the shell reacts to itself: focus, active window, closed window, the full window list and Niri's overview. A client can also ask for a refresh (answered even if nothing changed, with its request id), for a fresh `observe` state, or for counters. One controller serves a runtime directory and state: a second one waits on its lock until the first has gone, then takes over.

- **Automatic session saving (23 November 2026).** While a client asks for it (`{"auto_save": true}`: the shell, while the setting is on and recovery is not paused), the controller saves the session as `flow-recovery auto-save` did, in-process: `restore.automatic("auto-save", ..., wait=False)`. It saves 2 s after the first refresh that Niri's events or a client asked for since its last save, and never sooner than 30 s after the previous save. The save reads Niri afresh. A state it catches on its way to another is followed by events, hence a refresh, hence another save. A snapshot the same as the saved one is not written again. A refusal (windows recovery could not reopen) is kept from the clients once, as it may not last, and pauses only at the next save. Held locks (recovery's marker lock, or `session.lock`) are tried again a second later, never waited on. The clients that asked get `{"auto_saved": result}`, and the shell pauses on an error as before. The shell does not ask while its own Save, Restore or rename runs.

**The shell starts the controller** and supervises it, as it did its event stream, under `setpriv --pdeathsig TERM`. It restarts a controller that stopped after 2 s, then twice as long each time (at most a minute) until one has served for 30 s, and logs its errors. So every shell, the installed one or a development shell with its own runtime and state directories, has a controller with its environment. The shell drops its own event stream, its refresh timers and the title relabelling of 2026-11-22. It asks the controller for a refresh when Desk opens, when an action finishes (a pin changes no window, so no Niri event tells the controller), when recovery changes something, and when a display changes size. **Actions still run as `flow-spaces <action>` processes:** they wait on Niri and on apps, which the resident loop must not. A NixOS switch that changes Flow's Python restarts the shell, and with it the controller, so the controller never runs code older than the actions beside it.

**The Platform API** reads state from the controller (`{"get": "observe"}`) and emits `Changed` for each state the controller pushes. Before, it emitted on every burst of Niri events. When no controller exists (no shell runs), it falls back to `flow-spaces observe` and its own event stream, and looks for a controller again every 5 s. Its own stream stops when a controller is found and is not restarted while one serves. A controller that does not answer within 5 s is waiting on an action, so the call fails as busy instead of starting a `flow-spaces` that would wait on the same lock.

## Consequences

- A refresh after events costs about 5 ms in the controller instead of a 20 ms process. While a shell runs, the session has one Niri event stream instead of two. The guest numbers are in [Performance](../performance.md).
- The controller's loop is single-threaded. Everything it does between two `select` calls must stay short: a refresh in-process, socket reads and writes. A client that stops reading is dropped once 16 MB wait for it, and one that sends a 1 MB line is dropped.
- State is still written by whoever holds the locks: the controller's refresh, `flow-spaces` actions and `flow-recovery`. Nothing new owns the files.
- Automatic saving costs no process: the shell's 30 s timer and its list of what a save depends on are gone. `flow-recovery auto-save` stays for the command line, and runs in-process too.
- `flow-spaces refresh` stays, for actions, reshaping, and the API without a shell. Its timing is still checked by the performance smoke.
- The API needs the shell for its fast path. A controller as a separate user service would serve the API without a shell, but would need a separate controller for every development shell and smoke; that can follow if the API is ever used without a shell.

## References

- ADR-0004: [Niri event projection](ADR-0004-niri-event-projection.md)
- ADR-0009: [Platform API v1](ADR-0009-platform-api-v1.md)
- `tests/test_flow_controller.py`, `tests/niri_performance_smoke.py`, `tests/niri_background_smoke.py`
