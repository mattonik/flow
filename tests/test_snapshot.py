"""The portable workspace snapshot (ADR-0012): its format, privacy, versions, the preview and the import."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
sys.path.insert(0, str(Path(__file__).parent))
import snapshot  # noqa: E402
import groups as group_store  # noqa: E402
from fake_niri import FakeNiri  # noqa: E402

spec = importlib.util.spec_from_file_location("restore", Path(__file__).parents[1] / "session/restore.py")
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)

DISPLAY = "edid:Acme|Panel|SN1"
OUTPUTS = {"Virtual-1": {"name": "Virtual-1", "make": "Acme", "model": "Panel", "serial": "SN1",
                         "logical": {"x": 0, "y": 0, "width": 1000, "height": 700, "scale": 1.0}}}
SECRET = "Payroll 2026.ods"


def snap(*spaces):
    return {"format": snapshot.FORMAT, "version": 1, "spaces": list(spaces)}


# What Flow's own code tries on the network while a test runs: an audit hook sees every socket connect and
# name lookup in this process, whatever library makes it.
NETWORK = []
RECORDING = [False]


def _audit(event, args):
    if not RECORDING[0]:
        return
    if event == "socket.connect" and args[0].family != socket.AF_UNIX:
        NETWORK.append((event, args[1]))
    elif event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr", "urllib.Request"):
        NETWORK.append((event, args[0]))


sys.addaudithook(_audit)


@contextlib.contextmanager
def no_network(case):
    NETWORK.clear()
    RECORDING[0] = True
    try:
        yield
    finally:
        RECORDING[0] = False
    case.assertEqual(NETWORK, [], "Flow's snapshot code used the network")


class Format(unittest.TestCase):
    def test_a_minimal_snapshot_is_valid(self):
        data = snap({"apps": ["foot.desktop"]})
        self.assertIs(snapshot.validate(data), data)
        self.assertEqual(snapshot.parse(snapshot.dump(data)), data)

    def test_a_newer_version_is_refused_by_name(self):
        with self.assertRaisesRegex(ValueError, r"newer Flow \(version 2\); update Flow"):
            snapshot.validate({**snap({"apps": ["foot.desktop"]}), "version": 2})

    def test_missing_or_odd_versions_and_formats_are_refused(self):
        for version in (None, 0, True, "1", 1.0):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "no valid version"):
                snapshot.validate({**snap({"apps": ["foot.desktop"]}), "version": version})
        with self.assertRaisesRegex(ValueError, "Not a Flow workspace snapshot"):
            snapshot.validate({"version": 1, "windows": []})  # session.json is not a snapshot
        with self.assertRaisesRegex(ValueError, "not JSON"):
            snapshot.parse(b"\xff\xfe")
        with self.assertRaisesRegex(ValueError, "at most 64 KiB"):
            snapshot.parse(b" " * (snapshot.MAX_BYTES + 1))

    def test_private_or_local_keys_are_refused_at_every_level(self):
        """A key the version does not define is refused, so no title or window ID passes through unnoticed."""
        cases = [({**snap({"apps": ["foot.desktop"]}), "session": "boot:socket"}, "session"),
                 ({**snap({"apps": ["foot.desktop"]}), "display": DISPLAY}, "display"),
                 (snap({"apps": ["foot.desktop"], "title": SECRET}), "title"),
                 (snap({"apps": ["foot.desktop"], "window_id": 7}), "window_id"),
                 (snap({"apps": ["a.desktop", "b.desktop"],
                        "group": {"name": "W", "layout": "stack", "id": "abc"}}), "id")]
        for data, key in cases:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, f"does not know: {key}"):
                snapshot.validate(data)

    def test_apps_are_desktop_file_ids_each_once(self):
        for app in ("foot", "../foot.desktop", "a/b.desktop", ".desktop", "x" * 250 + ".desktop", 7):
            with self.subTest(app=app), self.assertRaisesRegex(ValueError, "desktop-file IDs"):
                snapshot.validate(snap({"apps": [app]}))
        with self.assertRaisesRegex(ValueError, "already in the snapshot"):
            snapshot.validate(snap({"apps": ["foot.desktop"]}, {"apps": ["foot.desktop"]}))
        with self.assertRaisesRegex(ValueError, "several apps but no Group"):
            snapshot.validate(snap({"apps": ["a.desktop", "b.desktop"]}))

    def test_groups_and_pins_are_checked(self):
        two = ["a.desktop", "b.desktop"]
        bad = [({"apps": two, "group": {"name": " ", "layout": "stack"}}, "name of 1 to 80"),
               ({"apps": two, "group": {"name": "x" * 81, "layout": "stack"}}, "name of 1 to 80"),
               ({"apps": ["a.desktop"], "group": {"name": "W", "layout": "stack"}}, "2 to 4 apps"),
               ({"apps": [f"{c}.desktop" for c in "abcde"], "group": {"name": "W", "layout": "stack"}}, "2 to 4"),
               ({"apps": two, "group": {"name": "W", "layout": "tabs"}}, "stack or side-by-side"),
               ({"apps": two, "group": {"name": "W", "layout": "stack", "proportions": [0.5, 0.5]}}, "only side"),
               ({"apps": two, "group": {"name": "W", "layout": "side-by-side", "proportions": [0.7, 0.7]}}, "summing"),
               ({"apps": two, "group": {"name": "W", "layout": "side-by-side", "proportions": [True, 0]}}, "summing"),
               ({"apps": two, "group": None}, "empty Group"),
               ({"apps": ["a.desktop"], "pin": 0}, "pin is a rank"),
               ({"apps": ["a.desktop"], "pin": True}, "pin is a rank")]
        for space, message in bad:
            with self.subTest(space=space), self.assertRaisesRegex(ValueError, message):
                snapshot.validate(snap(space))
        with self.assertRaisesRegex(ValueError, "pin is a rank"):
            snapshot.validate(snap({"apps": ["a.desktop"], "pin": 1}, {"apps": ["b.desktop"], "pin": 1}))
        snapshot.validate(snap({"apps": two, "group": {"name": "W", "layout": "side-by-side",
                                                       "proportions": [0.7, 0.3]}, "pin": 3}))

    def test_proportions_keep_flows_minimum_share(self):
        self.assertEqual(snapshot.MIN_SHARE, restore.spaces.MIN_SHARE)
        with self.assertRaisesRegex(ValueError, "at least 0.15"):
            snapshot.validate(snap({"apps": ["a.desktop", "b.desktop"],
                                    "group": {"name": "W", "layout": "side-by-side", "proportions": [0.05, 0.95]}}))

    def test_deep_nesting_is_refused_as_not_a_snapshot(self):
        # Python 3.12's parser recurses (RecursionError, refused as not JSON); later ones parse it, and it is no snapshot.
        with self.assertRaisesRegex(ValueError, "^Not a Flow workspace snapshot"):
            snapshot.parse(b"[" * 30000 + b"]" * 30000)


class GroupNames(unittest.TestCase):
    def test_renaming_marks_the_name_as_the_users(self):
        groups = [{"id": "g", "label": f"{SECRET} + Inbox", "members": ["a", "b"]}]
        group_store.rename(groups, "g", "Writing")
        self.assertEqual(groups[0], {"id": "g", "label": "Writing", "members": ["a", "b"], "named": True})
        group_store.validate({"version": 1, "groups": groups})
        with self.assertRaisesRegex(ValueError, "Invalid saved Groups"):
            group_store.validate({"version": 1, "groups": [{**groups[0], "named": "yes"}]})

    def test_a_read_only_load_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(group_store.load(Path(tmp), migrate=False), [])
            self.assertEqual(list(Path(tmp).iterdir()), [])


class TwoWorkspaces:
    """Niri with one output and two workspaces: 1 holds the user's work, 2 is the empty one Niri keeps last.

    Each workspace is a FakeNiri; ``query`` and ``command`` stand in for ``spaces.query`` and
    ``spaces.command``, and ``launch`` for ``gio launch``: the app's window opens on the focused workspace."""

    def __init__(self, columns, apps, active):
        self.spaces = {1: FakeNiri(columns, apps, active, workspace=1), 2: FakeNiri([], {}, None, workspace=2)}
        self.focused = 1
        self.next_id = 100
        self.launched = []
        self.actions = []

    def windows(self):
        return [w for ws in self.spaces.values() for w in ws.snapshot()[1]]

    def workspaces(self):
        return [{**ws.snapshot()[0], "idx": ident, "is_focused": ident == self.focused,
                 "is_active": ident == self.focused} for ident, ws in self.spaces.items()]

    def query(self, *names):
        answer = {"Workspaces": self.workspaces, "Windows": self.windows, "Outputs": lambda: copy.deepcopy(OUTPUTS)}
        return [answer[name]() for name in names]

    def command(self, *args):
        self.actions.append(args)
        if args[:2] == ("action", "focus-workspace"):
            self.focused = int(args[2])
            return ""
        if args[:2] == ("action", "focus-window"):
            ident = int(args[3])
            self.focused = next(k for k, ws in self.spaces.items() if ident in ws.apps)
        return self.spaces[self.focused](*args)

    def launch(self, args, **_):
        if args[:2] != ["gio", "launch"]:
            raise AssertionError(f"unexpected command {args}")
        app = Path(args[2]).name[:-len(".desktop")]
        self.launched.append(app)
        self.next_id += 1
        self.spaces[self.focused].open_window(self.next_id, app)

    def layout(self, ident):
        return self.spaces[ident].layout()


class Machine(unittest.TestCase):
    """A machine with Flow state on disk, installed apps and a Niri, for export, preview and import."""

    installed = ("existing", "inbox", "editor", "terminal", "notes", "calc")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.state = root / "state" / "flow"
        self.state.mkdir(parents=True)
        self.data = root / "data"
        (self.data / "applications").mkdir(parents=True)
        for app in self.installed:
            (self.data / "applications" / f"{app}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nName={app.title()}\nExec=true\n")
        self.niri = TwoWorkspaces([[1], [2]], {1: "existing", 2: "inbox"}, 1)
        self.pins = [{"id": "p1", "label": "Existing", "app_id": "existing", "window_id": 1,
                      "session": "session-1", "display": DISPLAY}]
        self.groups = [{"id": "g-old", "label": "Old", "members": ["mail", "chat"], "named": True}]
        self.write_state()
        for target, value in ((restore.spaces, "query"), (restore.spaces, "command")):
            patcher = patch.object(target, value, getattr(self.niri, value))
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (patch.object(restore.spaces, "session_id", return_value="session-1"),
                        patch.object(restore.subprocess, "run", side_effect=self.niri.launch),
                        patch.dict(os.environ, {"XDG_STATE_HOME": str(root / "state"),
                                                "XDG_DATA_HOME": str(self.data), "XDG_DATA_DIRS": str(self.data),
                                                "XDG_RUNTIME_DIR": str(root)})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dirs = (str(self.data),)
        restore.spaces.app_name.cache_clear()  # app names are cached per process; these are this machine's
        self.addCleanup(restore.spaces.app_name.cache_clear)

    def write_state(self):
        record = {"key": DISPLAY, "connector": "Virtual-1", "make": "Acme", "model": "Panel", "serial": "SN1",
                  "label": "Acme Panel", "number": 0}
        (self.state / "spaces.json").write_text(json.dumps({"version": 2, "displays": {DISPLAY: record},
                                                            "pins": self.pins}))
        (self.state / "groups.json").write_text(json.dumps({"version": 1, "groups": self.groups}))
        (self.state / "groups.initialized").write_text("")
        (self.state / "session.json").write_text(json.dumps({"version": 1, "session": "session-1",
                                                             "windows": [], "groups": []}))

    def files(self):
        return {p.name: p.read_bytes() for p in sorted(self.state.iterdir()) if not p.name.endswith(".lock")}

    def run_main(self, *args):
        out = io.StringIO()
        with patch.object(sys, "argv", ["flow-recovery", *args]), contextlib.redirect_stdout(out):
            restore.main()
        return json.loads(out.getvalue())

    def snapshot_file(self, data):
        path = self.state.parent.parent / "snapshot.json"
        path.write_bytes(snapshot.dump(data))
        return str(path)


class Export(Machine):
    installed = ("existing", "inbox", "editor", "terminal", "notes", "calc", "mail", "chat")

    def test_export_keeps_intent_and_no_titles_ids_sessions_or_displays(self):
        # Columns: a pinned app, a renamed stacked Group, an unnamed side-by-side Group (named after titles at
        # creation), two windows of one app, a window with no app ID, and a Layer.
        ws = FakeNiri([[11], [12, 13], [14], [15], [16], [17], [18]],
                      {11: "calc", 12: "mail", 13: "chat", 14: "editor", 15: "terminal", 16: "notes", 17: "notes",
                       18: "", 19: "inbox"}, 12, workspace=1)
        ws.floating = [19]
        ws.widths.update({14: 70.0, 15: 30.0})
        self.niri.spaces[1] = ws
        titles = {11: "Calculator", 12: SECRET, 13: "Chat with Dana", 14: "~/secret/plan.md", 15: "ssh prod-db",
                  16: "Diary", 17: "Diary 2", 18: "Untitled", 19: "Password: hunter2"}
        real = ws.snapshot
        ws.snapshot = lambda: (real()[0], [{**w, "title": titles[w["id"]]} for w in real()[1]])
        self.groups = [{"id": "g-stack", "label": "Writing", "members": ["mail", "chat"], "named": True},
                       {"id": "g-side", "label": "~/secret/plan.md + ssh prod-db", "members": ["editor", "terminal"],
                        "layout": "side-by-side", "side_ratio": 0.7}]
        self.pins = [{"id": "p-g", "label": "Writing", "app_id": "mail", "members": ["mail", "chat"],
                      "window_ids": [12, 13], "window_id": 12, "group_id": "g-stack", "session": "session-1",
                      "display": DISPLAY},
                     {"id": "p-c", "label": "Calculator", "app_id": "calc", "window_id": 11, "session": "session-1",
                      "display": DISPLAY},
                     {"id": "p-x", "label": "Elsewhere", "app_id": "existing", "window_id": 1, "session": "session-1",
                      "display": None}]  # unassigned: not this display's
        self.write_state()
        before = self.files()
        target = self.state.parent.parent / "out.json"
        with no_network(self):
            result = self.run_main("export", str(target))
        raw = target.read_bytes()
        self.assertEqual(oct(target.stat().st_mode & 0o777), "0o600")
        self.assertEqual(snapshot.parse(raw), {"format": snapshot.FORMAT, "version": 1, "spaces": [
            {"apps": ["calc.desktop"], "pin": 2},
            {"apps": ["mail.desktop", "chat.desktop"], "group": {"name": "Writing", "layout": "stack"}, "pin": 1},
            {"apps": ["editor.desktop", "terminal.desktop"],
             "group": {"name": "Editor + Terminal", "layout": "side-by-side", "proportions": [0.7, 0.3]}},
            {"apps": ["notes.desktop"]}]})
        text = raw.decode()
        for private in [*titles.values(), "session-1", DISPLAY, "Acme", "Virtual-1", "g-stack", "g-side", "p-g"]:
            self.assertNotIn(private, text)
        for ident in titles:
            self.assertNotIn(str(ident), text.replace("0.7", "").replace("0.3", ""))
        self.assertEqual(result["left_out"], [
            "1 Layer (floating windows): Layers are not exported",
            "A Group's own name came from window titles; exported under its apps' names",
            "notes: more than one window; exported once", "A window without an application ID"])
        self.assertEqual(self.files(), before)
        self.assertEqual(self.niri.actions, [])

    def test_export_tells_a_bound_group_from_another_window_of_its_app(self):
        self.niri.spaces[1] = FakeNiri([[21, 22]], {21: "mail", 22: "chat"}, 21, workspace=1)
        self.niri.spaces[2] = FakeNiri([[23]], {23: "mail"}, 23, workspace=2)
        self.groups = [{"id": "g", "label": "Talk", "members": ["mail", "chat"], "named": True,
                        "bound": {"session": "session-1", "windows": {"mail": 21, "chat": 22}}}]
        self.pins = []
        self.write_state()
        target = self.state.parent.parent / "out.json"
        self.run_main("export", str(target))
        self.assertEqual(json.loads(target.read_text())["spaces"],
                         [{"apps": ["mail.desktop", "chat.desktop"], "group": {"name": "Talk", "layout": "stack"}}])

    def test_an_oversized_file_is_refused(self):
        path = self.state.parent.parent / "big.json"
        path.write_bytes(b" " * (snapshot.MAX_BYTES + 10))
        with self.assertRaisesRegex(ValueError, "at most 64 KiB"):
            self.run_main("preview", str(path))

    def test_export_never_replaces_a_file(self):
        target = self.state.parent.parent / "out.json"
        target.write_text("mine")
        with self.assertRaises(FileExistsError):
            self.run_main("export", str(target))
        self.assertEqual(target.read_text(), "mine")


class Plan(unittest.TestCase):
    display = {"key": DISPLAY, "label": "Acme Panel", "pins": 1, "connected": 2}

    def plan(self, data, windows=(), groups=(), pins=(), missing=("gone.desktop",)):
        return snapshot.plan(snapshot.validate(data), list(windows), list(groups), list(pins),
                             lambda d: d not in missing, self.display)

    def test_the_luma_case_reports_the_missing_app_and_opens_the_group(self):
        report = self.plan(snap({"apps": ["gone.desktop"], "pin": 2},
                                {"apps": ["editor.desktop", "terminal.desktop"],
                                 "group": {"name": "Writing", "layout": "stack"}, "pin": 1}))
        self.assertIs(report["acted"], False)
        self.assertEqual(report["workspace"], {"new": True, "display": "Acme Panel", "displays_connected": 2})
        self.assertEqual(report["open"], [{"apps": ["editor.desktop", "terminal.desktop"],
                                           "group": {"name": "Writing", "layout": "stack"}, "pin": 1}])
        self.assertEqual(report["left_out"], [{"app": "gone.desktop", "reason": "not installed"}])
        self.assertEqual(report["pins"], [{"space": "Writing", "position": 2, "display": "Acme Panel"},
                                          {"space": "gone.desktop", "position": None,
                                           "reason": "not pinned: the space does not open whole"}])
        self.assertRegex(report["confirm"], "^[0-9a-f]{12}$")

    def test_apps_open_pinned_or_grouped_here_are_left_alone(self):
        report = self.plan(snap({"apps": ["open.desktop"]}, {"apps": ["pinned.desktop"]},
                                {"apps": ["grouped.desktop"]}, {"apps": ["new.desktop"]}),
                           windows=[{"id": 5, "app_id": "open", "is_floating": True}],
                           groups=[{"id": "g", "label": "G", "members": ["grouped", "other"]}],
                           pins=[{"id": "p", "app_id": "x", "members": ["pinned", "y"]}])
        self.assertEqual(report["open"], [{"apps": ["new.desktop"], "pin": None}])
        self.assertEqual([(e["app"], e["reason"].split(";")[0]) for e in report["left_out"]],
                         [("open.desktop", "already open here"), ("pinned.desktop", "already pinned here"),
                          ("grouped.desktop", "already in a Group here")])

    def test_a_group_missing_a_member_is_not_created_and_its_other_apps_open_unpinned(self):
        report = self.plan(snap({"apps": ["a.desktop", "gone.desktop", "b.desktop"],
                                 "group": {"name": "Trio", "layout": "stack"}, "pin": 1}))
        self.assertEqual(report["open"], [{"apps": ["a.desktop"], "pin": None}, {"apps": ["b.desktop"], "pin": None}])
        self.assertEqual(report["left_out"], [
            {"group": "Trio", "reason": "not created: gone.desktop left out; its other apps open on their own"},
            {"app": "gone.desktop", "reason": "not installed"}])
        self.assertEqual(report["pins"][0]["position"], None)

    def test_side_by_side_beside_a_stack_opens_as_a_stack(self):
        report = self.plan(snap({"apps": ["a.desktop", "b.desktop"], "group": {"name": "S", "layout": "stack"}},
                                {"apps": ["c.desktop", "d.desktop"],
                                 "group": {"name": "Wide", "layout": "side-by-side", "proportions": [0.6, 0.4]}}))
        self.assertEqual(report["open"][1]["group"], {"name": "Wide", "layout": "stack"})
        self.assertEqual(report["left_out"][0]["group"], "Wide")
        alone = self.plan(snap({"apps": ["c.desktop", "d.desktop"], "group": {"name": "Wide", "layout": "side-by-side"}}))
        self.assertEqual(alone["open"][0]["group"]["proportions"], [0.5, 0.5])
        self.assertEqual(alone["left_out"], [])

    def test_the_confirmation_changes_with_what_the_import_would_do(self):
        data = snap({"apps": ["a.desktop"]}, {"apps": ["b.desktop"]})
        first = self.plan(data)["confirm"]
        self.assertEqual(self.plan(data)["confirm"], first)
        self.assertNotEqual(self.plan(data, windows=[{"id": 1, "app_id": "a"}])["confirm"], first)
        self.assertNotEqual(self.plan(data, missing=("b.desktop",))["confirm"], first)
        self.assertNotEqual(self.plan(snap({"apps": ["b.desktop"]}, {"apps": ["a.desktop"]}))["confirm"], first)

    def test_the_confirmation_names_the_display_not_only_its_label(self):
        data = snap({"apps": ["a.desktop"]})
        first = self.plan(data)["confirm"]
        self.display = {**self.display, "key": "edid:Acme|Panel|SN2"}  # a twin with the same label
        self.assertNotEqual(self.plan(data)["confirm"], first)

    def test_no_pin_is_promised_beside_a_side_by_side_group(self):
        report = self.plan(snap({"apps": ["calc.desktop"], "pin": 1},
                                {"apps": ["a.desktop", "b.desktop"], "pin": 2,
                                 "group": {"name": "Wide", "layout": "side-by-side", "proportions": [0.6, 0.4]}}))
        self.assertEqual([s["pin"] for s in report["open"]], [None, None])
        self.assertEqual([(p["space"], p["position"]) for p in report["pins"]], [("calc.desktop", None), ("Wide", None)])
        self.assertIn("side-by-side", report["pins"][0]["reason"])

    def test_restore_input_columns_and_new_groups(self):
        report = self.plan(snap({"apps": ["a.desktop"]},
                                {"apps": ["b.desktop", "c.desktop"], "group": {"name": " Pair ", "layout": "stack"}},
                                {"apps": ["d.desktop", "e.desktop"],
                                 "group": {"name": "Wide", "layout": "side-by-side", "proportions": [0.6, 0.4]}}))
        report["open"][2]["group"] = {"name": "Wide", "layout": "side-by-side", "proportions": [0.6, 0.4]}
        data, created = snapshot.restore_input(report)
        self.assertEqual([(e["app_id"], e["column"]) for e in data["windows"]],
                         [("a", 1), ("b", 2), ("c", 2), ("d", 3), ("e", 4)])
        self.assertEqual([{k: v for k, v in g.items() if k != "id"} for g in created],
                         [{"label": "Pair", "members": ["b", "c"], "named": True},
                          {"label": "Wide", "members": ["d", "e"], "named": True, "layout": "side-by-side",
                           "side_ratio": 0.6}])
        self.assertEqual(data["groups"], created)
        self.assertNotEqual(created[0]["id"], created[1]["id"])


class PreviewAndImport(Machine):
    luma = snap({"apps": ["gone.desktop"]},
                {"apps": ["editor.desktop", "terminal.desktop"], "group": {"name": "Writing", "layout": "stack"},
                 "pin": 1})

    def existing(self):
        return [(w["id"], w["app_id"], w["workspace_id"], w["layout"]["pos_in_scrolling_layout"])
                for w in self.niri.windows() if w["workspace_id"] == 1]

    def test_preview_changes_nothing_and_runs_nothing(self):
        before, windows = self.files(), self.existing()
        with no_network(self):
            report = self.run_main("preview", self.snapshot_file(self.luma))
        self.assertIs(report["acted"], False)
        self.assertEqual(report["left_out"], [{"app": "gone.desktop", "reason": "not installed"}])
        self.assertEqual(report["pins"][0], {"space": "Writing", "position": 2, "display": "Acme Panel"})
        self.assertEqual((self.files(), self.existing()), (before, windows))
        self.assertEqual((self.niri.actions, self.niri.launched), ([], []))

    def test_preview_of_a_fresh_state_creates_no_state_file(self):
        for path in self.state.iterdir():
            path.unlink()
        self.run_main("preview", self.snapshot_file(self.luma))
        self.assertEqual(self.files(), {})

    def test_import_without_the_previews_confirmation_does_nothing(self):
        before = self.files()
        path = self.snapshot_file(self.luma)
        confirm = self.run_main("preview", path)["confirm"]
        self.niri.spaces[1].open_window(50, "terminal")  # the machine changes after the preview
        for wrong in ("", "0" * 12, confirm):
            with self.subTest(confirm=wrong), self.assertRaisesRegex(ValueError, "Nothing was imported; preview"):
                self.run_main("import", path, wrong)
        self.assertEqual(self.files(), before)
        self.assertEqual((self.niri.actions, self.niri.launched, self.niri.focused), ([], [], 1))

    def test_import_opens_the_group_on_a_new_workspace_and_leaves_existing_work_alone(self):
        before, windows = self.files(), self.existing()
        path = self.snapshot_file(self.luma)
        with no_network(self):
            confirm = self.run_main("preview", path)["confirm"]
            result = self.run_main("import", path, confirm)
        self.assertIs(result["acted"], True)
        self.assertEqual(result["workspace"], 2)
        self.assertEqual(self.niri.focused, 2)
        self.assertEqual(self.niri.launched, ["editor", "terminal"])
        self.assertEqual(self.niri.layout(2), [["editor", "terminal"]])
        self.assertEqual(self.existing(), windows)
        self.assertEqual(result["left_out"], [{"app": "gone.desktop", "reason": "not installed"}])
        self.assertIn("Imported 1 of 1 spaces onto a new workspace. Left out: gone.desktop: not installed",
                      result["message"])
        groups = json.loads((self.state / "groups.json").read_text())["groups"]
        self.assertEqual(groups[0], self.groups[0])
        self.assertEqual([{k: v for k, v in g.items() if k not in ("id", "bound", "last_focused_app")} for g in groups[1:]],
                         [{"label": "Writing", "members": ["editor", "terminal"], "named": True}])
        pins = json.loads((self.state / "spaces.json").read_text())["pins"]
        self.assertEqual(pins[0], self.pins[0])
        self.assertEqual((pins[1]["group_id"], pins[1]["members"], pins[1]["display"]),
                         (groups[1]["id"], ["editor", "terminal"], DISPLAY))
        self.assertEqual(result["pins"], [{"space": "Writing", "pinned": True}])
        self.assertEqual(self.files()["session.json"], before["session.json"])

    def test_import_into_an_empty_focused_workspace_stays_there(self):
        self.niri.focused = 2
        path = self.snapshot_file(snap({"apps": ["calc.desktop"], "pin": 1}, {"apps": ["notes.desktop"]}))
        result = self.run_main("import", path, self.run_main("preview", path)["confirm"])
        self.assertNotIn(("action", "focus-workspace", 2), self.niri.actions)
        self.assertEqual(self.niri.layout(2), [["calc"], ["notes"]])
        self.assertEqual(result["pins"], [{"space": "calc.desktop", "pinned": True}])
        pins = json.loads((self.state / "spaces.json").read_text())["pins"]
        self.assertEqual([p["app_id"] for p in pins], ["existing", "calc"])

    def test_a_side_by_side_group_gets_its_proportions(self):
        path = self.snapshot_file(snap({"apps": ["editor.desktop", "terminal.desktop"],
                                        "group": {"name": "Wide", "layout": "side-by-side",
                                                  "proportions": [0.7, 0.3]}}))
        self.run_main("import", path, self.run_main("preview", path)["confirm"])
        self.assertEqual(self.niri.layout(2), [["editor"], ["terminal"]])
        self.assertEqual(sorted(self.niri.spaces[2].widths.values()), [30.0, 70.0])
        group = json.loads((self.state / "groups.json").read_text())["groups"][-1]
        self.assertEqual((group["layout"], group["side_ratio"]), ("side-by-side", 0.7))

    def test_an_app_that_never_opens_is_named_in_the_answer(self):
        launch = self.niri.launch

        def stubborn(args, **kwargs):
            if "terminal" not in args[2]:
                launch(args, **kwargs)
        path = self.snapshot_file(snap({"apps": ["calc.desktop"]}, {"apps": ["terminal.desktop"], "pin": 1}))
        confirm = self.run_main("preview", path)["confirm"]
        # restore() waits 15 s for a window that never comes; a short wait keeps the test quick.
        original = restore.restore

        def quick(*args, **kwargs):
            return original(*args, **{**kwargs, "wait": lambda _: None})
        with patch.object(restore.subprocess, "run", side_effect=stubborn), patch.object(restore, "restore", quick):
            result = self.run_main("import", path, confirm)
        self.assertEqual(self.niri.layout(2), [["calc"]])
        self.assertIn({"app": "terminal.desktop", "reason": "did not open: terminal.desktop"}, result["left_out"])
        self.assertEqual(result["pins"], [{"space": "terminal.desktop", "pinned": False, "reason": "did not open"}])
        self.assertIn("Imported 1 of 2 spaces", result["message"])

    def test_only_apps_a_user_could_open_count_as_installed(self):
        apps = self.data / "applications"
        (apps / "hidden.desktop").write_text("[Desktop Entry]\nType=Application\nHidden=true\nExec=true\n")
        (apps / "helper.desktop").write_text("[Desktop Entry]\nType=Application\nNoDisplay=true\nExec=true\n")
        (apps / "link.desktop").write_text("[Desktop Entry]\nType=Link\nURL=https://example.invalid\n")
        report = self.run_main("preview", self.snapshot_file(snap(
            {"apps": ["hidden.desktop"]}, {"apps": ["helper.desktop"]}, {"apps": ["link.desktop"]},
            {"apps": ["calc.desktop"]})))
        self.assertEqual(report["open"], [{"apps": ["calc.desktop"], "pin": None}])
        self.assertEqual([e["reason"] for e in report["left_out"]], ["not installed"] * 3)

    def test_nothing_that_can_open_means_nothing_is_done(self):
        before = self.files()
        path = self.snapshot_file(snap({"apps": ["gone.desktop"]}))
        with self.assertRaisesRegex(ValueError, "Nothing in the snapshot can open here; nothing was imported"):
            self.run_main("import", path, self.run_main("preview", path)["confirm"])
        self.assertEqual((self.files(), self.niri.actions, self.niri.focused), (before, [], 1))

    def test_a_workspace_that_does_not_come_into_focus_stops_the_import(self):
        command = self.niri.command
        self.niri.command = lambda *args: None if args[:2] == ("action", "focus-workspace") else command(*args)
        path = self.snapshot_file(snap({"apps": ["calc.desktop"]}))
        confirm = self.run_main("preview", path)["confirm"]
        with patch.object(restore.spaces, "command", self.niri.command), \
                self.assertRaisesRegex(ValueError, "did not come into focus empty; nothing was imported"):
            self.run_main("import", path, confirm)
        self.assertEqual((self.niri.launched, self.niri.layout(1)), ([], [["existing"], ["inbox"]]))

    def test_a_pinned_side_by_side_import_pins_nothing_and_says_so(self):
        path = self.snapshot_file(snap({"apps": ["calc.desktop"], "pin": 1},
                                       {"apps": ["editor.desktop", "terminal.desktop"], "pin": 2,
                                        "group": {"name": "Wide", "layout": "side-by-side",
                                                  "proportions": [0.7, 0.3]}}))
        preview = self.run_main("preview", path)
        self.assertEqual([p["position"] for p in preview["pins"]], [None, None])
        result = self.run_main("import", path, preview["confirm"])
        self.assertEqual(self.niri.layout(2), [["calc"], ["editor"], ["terminal"]])
        self.assertEqual(result["pins"], [])
        self.assertEqual(json.loads((self.state / "spaces.json").read_text())["pins"], self.pins)
        self.assertEqual(result["message"], "Imported 2 of 2 spaces onto a new workspace.")

    def test_a_group_niri_would_not_build_is_left_out_and_unregistered(self):
        command = self.niri.command
        self.niri.command = lambda *args: None if args[1:2] == ("consume-window-into-column",) else command(*args)
        path = self.snapshot_file(self.luma)
        confirm = self.run_main("preview", path)["confirm"]
        with patch.object(restore.spaces, "command", self.niri.command):
            result = self.run_main("import", path, confirm)
        self.assertEqual(self.niri.layout(2), [["editor"], ["terminal"]])
        self.assertEqual(result["opened"], [])
        self.assertIn({"group": "Writing", "reason": "not created: Flow could not arrange its apps as the Group; "
                                                     "they stay open as spaces of their own"}, result["left_out"])
        self.assertIn("Imported 0 of 1 spaces", result["message"])
        self.assertIn("Not pinned: Writing: did not open", result["message"])
        self.assertEqual(json.loads((self.state / "groups.json").read_text())["groups"], self.groups)
        # Automatic saving goes on: no separated Group of the import's is left for it to refuse.
        restore.save_locked(self.state / "session.json", checked=True)

    def test_a_failing_niri_action_still_says_which_groups_stay(self):
        command = self.niri.command

        def failing(*args):
            if args[1:2] == ("consume-window-into-column",):
                raise restore.subprocess.CalledProcessError(1, ["niri", "msg", *args])
            return command(*args)
        path = self.snapshot_file(self.luma)
        confirm = self.run_main("preview", path)["confirm"]
        with patch.object(restore.spaces, "command", failing), \
                self.assertRaisesRegex(ValueError, "The import stopped: .*; Groups it registered stay: Writing"):
            self.run_main("import", path, confirm)

    def test_refreshes_during_the_import_do_not_stop_it(self):
        """The controller refreshes whenever Niri changes: once the Group is one column, a refresh binds it."""
        command = self.niri.command

        def refreshed(*args):
            answer = command(*args)
            if args[1:2] == ("consume-window-into-column",):
                with (self.state / "session.lock").open("a") as lock:
                    restore.fcntl.flock(lock, restore.fcntl.LOCK_EX)
                    restore.spaces.run_locked(self.state, "refresh", emit=False)
                group = json.loads((self.state / "groups.json").read_text())["groups"][-1]
                self.assertIn("bound", group)  # the refresh did change the new Group's record
            return answer
        path = self.snapshot_file(self.luma)
        confirm = self.run_main("preview", path)["confirm"]
        with patch.object(restore.spaces, "command", refreshed):
            result = self.run_main("import", path, confirm)
        self.assertEqual(self.niri.layout(2), [["editor", "terminal"]])
        self.assertEqual(result["pins"], [{"space": "Writing", "pinned": True}])

    def test_a_change_to_existing_pins_during_the_import_stops_it(self):
        path = self.snapshot_file(snap({"apps": ["calc.desktop"]}, {"apps": ["notes.desktop"]}))
        confirm = self.run_main("preview", path)["confirm"]
        launch = self.niri.launch

        def meddle(args, **kwargs):
            launch(args, **kwargs)
            self.pins[0]["label"] = "Renamed meanwhile"
            self.write_state()
        with patch.object(restore.subprocess, "run", side_effect=meddle), \
                self.assertRaisesRegex(ValueError, "The import stopped: Groups or pinned spaces changed"):
            self.run_main("import", path, confirm)
        self.assertEqual(self.niri.launched, ["calc"])


if __name__ == "__main__":
    unittest.main()
