# ADR-0002: Prototype a pure Niri event projection

**Status:** Prototype only, 13 November 2026

## Context

Flow's current shell listens to `niri msg -j event-stream` but separately refreshes its spaces model through existing commands. The master spec assigns compositor state to Niri and semantic spaces, Groups, pinning, and restoration intent to the Flow Controller. Niri documents an initial event-stream state followed by updates; replacement events can replace window/workspace collections. Pond's `NiriMsg.qml` demonstrates a related event-derived projection, but also carries Pond-specific UI, launcher, and action behavior.

## Decision

Add a small Python `NiriState` projection that consumes already-decoded event objects. It handles initial/replacement workspace and window snapshots, window open/change/close, focus, workspace activation, active-window updates, `WindowLayoutsChanged`, and explicit reset for reconnection. In Niri 26.04, `WindowLayoutsChanged.changes` is serialized as arrays of `[window_id, layout]` pairs; the projection accepts that documented shape. Niri u64 IDs are accepted as nonnegative JSON integers only. The projection owns no transport, Niri process, commands, persistence, semantic Flow IDs, or public API. Unknown and malformed events are ignored without mutating state.

## Consequences

- Tests can exercise state transitions without a compositor or shell.
- An eventual controller can compare this projection with the existing snapshot path before integration.
- `ready` means both initial replacement snapshots have arrived; after reset no prior compositor state is presented as current.
- Niri event fields and ordering can change with the compositor version. Niri notes that some cross-event updates are not atomic, so consumers must tolerate temporary references to removed workspaces.
- Layout updates for windows not yet present are ignored; the subsequent full or per-window state event supplies their current record.
- This does not prove IPC transport reliability, reconnect orchestration, live VM behavior, Flow API compatibility, or semantic space behavior. Preserve the current shell/controller behavior until a separate integration decision is made.

## References

- [Niri IPC and event stream](https://github.com/niri-wm/niri/blob/main/docs/wiki/IPC.md)
- [Niri event reference](https://niri-wm.github.io/niri/niri_ipc/enum.Event.html)
- [Pond NiriMsg.qml at reviewed revision](https://github.com/bflycomputer/pond-interface/blob/9277356200ac02c5367587bc7b68a079cb2574e7/shell/NiriMsg.qml)

## Initial guest check

On the ARM64 NixOS guest with Niri 26.04, the initial event stream was fed to this projection and compared with separate Niri window and workspace snapshots. Window IDs, workspace IDs and focused-window ID matched. Live event transitions, reconnect handling, output changes and integration with Flow remain unverified; this comparison is not atomic.
