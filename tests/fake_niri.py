"""A tiny model of Niri's scrolling columns, for checking Flow's action sequences."""


class FakeNiri:
    def __init__(self, columns, apps, active, output="Virtual-1", workspace=1):
        self.columns = [list(c) for c in columns]  # window IDs, top to bottom
        self.apps = dict(apps)
        self.active = active
        self.output = output
        self.workspace = workspace
        self.widths = {}
        self.floating = []
        self.tiled_active = active  # Niri remembers the active column while a floating window has focus
        self.opened_from = {}  # a new window remembers the column it was opened from
        self.log = []
        self.fail_on = None

    def column_of(self, ident):
        return next(i for i, column in enumerate(self.columns) if ident in column)

    def snapshot(self):
        windows = []
        for c, column in enumerate(self.columns, 1):
            for r, ident in enumerate(column, 1):
                width = self.widths.get(ident, 50.0) * 10
                windows.append({"id": ident, "app_id": self.apps[ident], "title": self.apps[ident],
                                "workspace_id": self.workspace, "is_floating": False,
                                "layout": {"pos_in_scrolling_layout": [c, r], "tile_size": [width, 700]}})
        for ident in self.floating:
            windows.append({"id": ident, "app_id": self.apps[ident], "title": self.apps[ident],
                            "workspace_id": self.workspace, "is_floating": True,
                            "layout": {"pos_in_scrolling_layout": None, "tile_size": [576, 480]}})
        return {"id": self.workspace, "active_window_id": self.active, "output": self.output,
                "is_focused": True, "is_active": True}, windows

    def after_active_column(self):
        tiled = [i for column in self.columns for i in column]
        return self.column_of(self.tiled_active) + 1 if self.tiled_active in tiled else len(self.columns)

    def open_window(self, ident, app):
        """A new window opens in its own column after the active column, even under a floating window."""
        self.apps[ident] = app
        self.columns.insert(self.after_active_column(), [ident])
        self.opened_from[ident] = self.tiled_active
        self.active = self.tiled_active = ident

    def __call__(self, *args):
        self.log.append(args)
        if self.fail_on and self.fail_on(args):
            raise OSError("Niri stopped")
        if args[0] != "action":
            return "{}"
        name, rest = args[1], args[2:]
        ident = int(rest[rest.index("--id") + 1]) if "--id" in rest else self.active
        if name == "focus-window":
            self.active = ident
            if ident not in self.floating:
                self.tiled_active = ident
                self.opened_from.clear()
        elif name == "move-window-to-floating" and ident not in self.floating:
            c = self.column_of(ident)
            self.columns[c].remove(ident)
            if not self.columns[c]:
                self.columns.pop(c)
            self.floating.append(ident)
            if self.tiled_active == ident:
                # Niri returns to the column a new window was opened from, else the one taking its place.
                origin = self.opened_from.get(ident)
                if origin in [i for column in self.columns for i in column]:
                    self.tiled_active = origin
                else:
                    self.tiled_active = self.columns[min(c, len(self.columns) - 1)][0] if self.columns else None
        elif name == "move-window-to-tiling" and ident in self.floating:
            self.floating.remove(ident)
            self.columns.insert(self.after_active_column(), [ident])
            if self.active == ident:
                self.tiled_active = ident
        elif name == "close-window":
            if ident in self.floating:
                self.floating.remove(ident)
                if self.active == ident:
                    self.active = self.tiled_active
            else:
                c = self.column_of(ident)
                self.columns[c].remove(ident)
                if not self.columns[c]:
                    self.columns.pop(c)
                if self.active == ident:
                    self.active = self.tiled_active = self.columns[0][0] if self.columns else None
        elif name.startswith("consume-or-expel-window-"):
            left = name.endswith("left")
            c = self.column_of(ident)
            if len(self.columns[c]) > 1:
                self.columns[c].remove(ident)
                self.columns.insert(c if left else c + 1, [ident])
            else:
                target = c - 1 if left else c + 1
                if 0 <= target < len(self.columns):
                    self.columns.pop(c)
                    self.columns[target if left else c].append(ident)
        elif name == "consume-window-into-column":
            c = self.column_of(self.active)
            if c + 1 < len(self.columns):
                moved = self.columns[c + 1].pop(0)
                self.columns[c].append(moved)
                if not self.columns[c + 1]:
                    self.columns.pop(c + 1)
        elif name == "move-column-to-index":
            c = self.column_of(self.active)
            column = self.columns.pop(c)
            self.columns.insert(int(rest[0]) - 1, column)
        elif name in ("move-column-left", "move-column-right"):
            c = self.column_of(self.active)
            t = c + (-1 if name.endswith("left") else 1)
            if 0 <= t < len(self.columns):
                self.columns[c], self.columns[t] = self.columns[t], self.columns[c]
        elif name in ("move-window-up", "move-window-down"):
            column = self.columns[self.column_of(self.active)]
            r = column.index(self.active)
            t = r + (-1 if name.endswith("up") else 1)
            if 0 <= t < len(column):
                column[r], column[t] = column[t], column[r]
        elif name == "set-window-width" and ident not in self.floating:
            self.widths[ident] = float(rest[-1].rstrip("%"))
        return ""

    def layout(self):
        return [[self.apps[i] for i in column] for column in self.columns]


class MultiNiri:
    """Niri with several outputs, each showing one workspace of columns (window IDs, top to bottom).

    Moving a column to another monitor inserts it after that monitor's active column and focuses it,
    as Niri does; the source monitor activates the column that took its place.
    """

    def __init__(self, layout, apps, focused, outputs):
        self.outputs = outputs
        self.workspace_ids = {name: index for index, name in enumerate(layout, 1)}
        self.columns = {name: [list(c) for c in columns] for name, columns in layout.items()}
        self.active = {name: (columns[0][0] if columns else None) for name, columns in self.columns.items()}
        self.apps = dict(apps)
        self.focused_output = self.output_of(focused)
        self.active[self.focused_output] = focused
        self.widths = {}
        self.log = []
        self.fail_on = None

    def output_of(self, ident):
        return next(name for name, columns in self.columns.items() if any(ident in c for c in columns))

    def column_of(self, name, ident):
        return next(i for i, column in enumerate(self.columns[name]) if ident in column)

    def workspaces(self):
        return [{"id": self.workspace_ids[name], "output": name, "is_active": True,
                 "is_focused": name == self.focused_output, "active_window_id": self.active[name]}
                for name in self.columns]

    def snapshot(self):
        windows = []
        for name, columns in self.columns.items():
            for c, column in enumerate(columns, 1):
                for r, ident in enumerate(column, 1):
                    windows.append({"id": ident, "app_id": self.apps[ident], "title": self.apps[ident],
                                    "workspace_id": self.workspace_ids[name], "is_floating": False,
                                    "layout": {"pos_in_scrolling_layout": [c, r],
                                               "tile_size": [self.widths.get(ident, 50.0) * 10, 700]}})
        return next(w for w in self.workspaces() if w["is_focused"]), windows

    def display_state(self):
        return self.outputs, self.workspaces()

    def take_column(self, name, index):
        column = self.columns[name].pop(index)
        remaining = self.columns[name]
        self.active[name] = remaining[max(0, index - 1)][0] if remaining else None
        return column

    def insert_after_active(self, name, column):
        active = self.active[name]
        index = self.column_of(name, active) + 1 if active is not None else 0
        self.columns[name].insert(index, column)
        self.active[name] = column[0]
        self.focused_output = name

    def __call__(self, *args):
        self.log.append(args)
        if self.fail_on and self.fail_on(args):
            raise OSError("Niri stopped")
        if args[0] != "action":
            return "{}"
        name, rest = args[1], args[2:]
        ident = int(rest[rest.index("--id") + 1]) if "--id" in rest else None
        if name == "focus-window":
            self.focused_output = self.output_of(ident)
            self.active[self.focused_output] = ident
        elif name == "focus-monitor":
            self.focused_output = rest[0]
        elif name == "move-column-to-monitor":
            source = self.focused_output
            column = self.take_column(source, self.column_of(source, self.active[source]))
            self.insert_after_active(rest[0], column)
        elif name == "move-window-to-monitor":
            source = self.output_of(ident)
            index = self.column_of(source, ident)
            self.columns[source][index].remove(ident)
            if not self.columns[source][index]:
                self.columns[source].pop(index)
            self.insert_after_active(rest[-1], [ident])
        elif name == "move-column-to-index":
            out = self.focused_output
            column = self.columns[out].pop(self.column_of(out, self.active[out]))
            self.columns[out].insert(int(rest[0]) - 1, column)
        elif name == "set-window-width":
            self.widths[ident] = float(rest[-1].rstrip("%"))
        return ""

    def layout(self):
        return {name: [[self.apps[i] for i in column] for column in columns]
                for name, columns in self.columns.items()}
