"""A tiny model of Niri's scrolling columns, for checking Flow's action sequences."""


class FakeNiri:
    def __init__(self, columns, apps, active, output="Virtual-1", workspace=1):
        self.columns = [list(c) for c in columns]  # window IDs, top to bottom
        self.apps = dict(apps)
        self.active = active
        self.output = output
        self.workspace = workspace
        self.widths = {}
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
        return {"id": self.workspace, "active_window_id": self.active, "output": self.output,
                "is_focused": True, "is_active": True}, windows

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
        elif name == "set-window-width":
            self.widths[ident] = float(rest[-1].rstrip("%"))
        return ""

    def layout(self):
        return [[self.apps[i] for i in column] for column in self.columns]
