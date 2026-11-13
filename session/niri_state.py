"""Pure projection of Niri's event-stream state; transport stays outside."""

from copy import deepcopy


class NiriState:
    def __init__(self):
        self.reset()

    def reset(self):
        """Forget the old stream; wait for fresh replacement snapshots."""
        self.workspaces = {}
        self.windows = {}
        self.focused_window_id = None
        self._have_workspaces = False
        self._have_windows = False

    @property
    def ready(self):
        return self._have_workspaces and self._have_windows

    def apply(self, event):
        """Apply one decoded Niri event. Return False for unknown/malformed events."""
        if not isinstance(event, dict) or len(event) != 1:
            return False
        name, data = next(iter(event.items()))
        if not isinstance(data, dict):
            return False

        if name == "WorkspacesChanged":
            rows = data.get("workspaces")
            if not isinstance(rows, list) or not all(
                    isinstance(w, dict) and _valid_id(w.get("id")) for w in rows):
                return False
            self.workspaces = {w["id"]: deepcopy(w) for w in rows}
            self._have_workspaces = True
        elif name == "WindowsChanged":
            rows = data.get("windows")
            if not isinstance(rows, list) or not all(
                    isinstance(w, dict) and _valid_id(w.get("id")) for w in rows):
                return False
            self.windows = {w["id"]: deepcopy(w) for w in rows}
            self._have_windows = True
            self._sync_focus()
        elif name == "WindowOpenedOrChanged":
            window = data.get("window")
            if not isinstance(window, dict) or not _valid_id(window.get("id")):
                return False
            window = deepcopy(window)
            self.windows[window["id"]] = window
            if window.get("is_focused"):
                self._set_focus(window["id"])
        elif name == "WindowClosed":
            ident = data.get("id")
            if not _valid_id(ident):
                return False
            self.windows.pop(ident, None)
            if self.focused_window_id == ident:
                self.focused_window_id = None
        elif name == "WindowFocusChanged":
            if "id" not in data or (data["id"] is not None and not _valid_id(data["id"])):
                return False
            self._set_focus(data["id"])
        elif name == "WorkspaceActivated":
            ident = data.get("id")
            if not _valid_id(ident) or not isinstance(data.get("focused"), bool):
                return False
            target = self.workspaces.get(ident)
            if target is not None:
                output = target.get("output")
                for workspace in self.workspaces.values():
                    if workspace.get("output") == output:
                        workspace["is_active"] = workspace["id"] == ident
                    if data["focused"]:
                        workspace["is_focused"] = workspace["id"] == ident
        elif name == "WorkspaceActiveWindowChanged":
            ident = data.get("workspace_id")
            active_id = data.get("active_window_id")
            if not _valid_id(ident) or "active_window_id" not in data or (
                    active_id is not None and not _valid_id(active_id)):
                return False
            workspace = self.workspaces.get(ident)
            if workspace is not None:
                workspace["active_window_id"] = data["active_window_id"]
        elif name == "WindowLayoutsChanged":
            changes = data.get("changes")
            if not isinstance(changes, list) or not all(
                    isinstance(change, list) and len(change) == 2 and _valid_id(change[0])
                    and isinstance(change[1], dict) for change in changes):
                return False
            for ident, layout in changes:
                if ident in self.windows:
                    self.windows[ident]["layout"] = deepcopy(layout)
        else:
            return False
        return True

    def _set_focus(self, ident):
        self.focused_window_id = ident
        for window in self.windows.values():
            window["is_focused"] = window["id"] == ident

    def _sync_focus(self):
        focused = next((w["id"] for w in self.windows.values() if w.get("is_focused")), None)
        self._set_focus(focused)


def _valid_id(value):
    # Niri serializes u64 IDs as JSON integers; reject bool and container IDs.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
