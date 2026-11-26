import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
import pin_store
import restore
import spaces

LAPTOP = {"make": "BOE", "model": "Panel", "serial": None, "logical": {"width": 1280, "height": 800}}
MONITOR = {"make": "Dell", "model": "U2723QE", "serial": "SN1", "logical": {"width": 1920, "height": 1080}}
LAPTOP_KEY = "port:eDP-1|BOE|Panel"
MONITOR_KEY = "edid:Dell|U2723QE|SN1"


def window(ident, app, column, workspace):
    return {"id": ident, "app_id": app, "title": app, "workspace_id": workspace, "is_floating": False,
            "layout": {"pos_in_scrolling_layout": [column, 1]}}


class IdentityTest(unittest.TestCase):
    def test_serial_identifies_a_monitor_across_connectors_and_ports_otherwise(self):
        found, ambiguous = pin_store.identities({"eDP-1": LAPTOP, "DP-1": MONITOR, "DP-9": {**MONITOR, "logical": None}})
        self.assertEqual(set(found), {"eDP-1", "DP-1"})
        self.assertEqual((found["eDP-1"]["key"], found["DP-1"]["key"]), (LAPTOP_KEY, MONITOR_KEY))
        self.assertEqual(found["DP-1"]["label"], "Dell U2723QE")
        moved, _ = pin_store.identities({"HDMI-A-1": MONITOR})
        self.assertEqual(moved["HDMI-A-1"]["key"], MONITOR_KEY)
        self.assertEqual(ambiguous, set())

    def test_duplicate_serials_are_not_trusted(self):
        found, ambiguous = pin_store.identities({"DP-1": MONITOR, "DP-2": MONITOR})
        self.assertEqual(ambiguous, {MONITOR_KEY})
        self.assertEqual({r["key"] for r in found.values()}, {"port:DP-1|Dell|U2723QE", "port:DP-2|Dell|U2723QE"})
        self.assertEqual(pin_store.status(MONITOR_KEY, found, ambiguous), "ambiguous")

    def test_a_twin_monitor_keeps_its_port_identity_when_the_other_is_unplugged(self):
        displays = {}
        both, _ = pin_store.identities({"DP-1": MONITOR, "DP-2": MONITOR})
        pin_store.migrate([], displays, 2, both, lambda pin: None)
        alone, ambiguous = pin_store.identities({"DP-1": MONITOR}, pin_store.shared_serials(displays))
        self.assertEqual(alone["DP-1"]["key"], "port:DP-1|Dell|U2723QE")
        self.assertEqual(ambiguous, {MONITOR_KEY})
        # A different monitor of the same model with its own serial is still identified by it.
        other, _ = pin_store.identities({"DP-3": {**MONITOR, "serial": "SN2"}}, pin_store.shared_serials(displays))
        self.assertEqual(other["DP-3"]["key"], "edid:Dell|U2723QE|SN2")


class MigrationTest(unittest.TestCase):
    def connected(self, outputs):
        return pin_store.identities(outputs)[0]

    def test_single_display_legacy_pins_keep_their_order_on_that_display(self):
        pins = [{"id": "1", "app_id": "a"}, {"id": "2", "app_id": "b", "display": "eDP-1"}, {"id": "3", "app_id": "c"}]
        displays = pin_store.migrate(pins, {}, 1, self.connected({"eDP-1": LAPTOP}), lambda pin: None)
        self.assertEqual([p["display"] for p in pins], [LAPTOP_KEY] * 3)
        self.assertEqual(set(displays), {LAPTOP_KEY})

    def test_legacy_connector_waits_for_its_output_and_unassigned_pins_are_not_guessed(self):
        pins = [{"id": "1", "app_id": "a", "display": "HDMI-A-1"}, {"id": "2", "app_id": "b"},
                {"id": "3", "app_id": "c"}]
        connected = self.connected({"eDP-1": LAPTOP, "DP-1": MONITOR})
        displays = pin_store.migrate(pins, {}, 1, connected, lambda pin: "DP-1" if pin["id"] == "3" else None)
        self.assertEqual([p["display"] for p in pins], ["legacy:HDMI-A-1", None, MONITOR_KEY])
        self.assertEqual(pin_store.status(None, connected, set()), "unassigned")
        self.assertEqual(pin_store.status("legacy:HDMI-A-1", connected, set()), "disconnected")
        later = self.connected({"eDP-1": LAPTOP, "HDMI-A-1": {**MONITOR, "serial": "SN2"}})
        pin_store.migrate(pins, displays, 2, later, lambda pin: None)
        self.assertEqual(pins[0]["display"], "edid:Dell|U2723QE|SN2")
        self.assertNotIn("legacy:HDMI-A-1", displays)
        self.assertIsNone(pins[1]["display"])
        pin_store.migrate(pins, displays, 2, self.connected({"eDP-1": LAPTOP}), lambda pin: None)
        self.assertEqual(pins[1]["display"], LAPTOP_KEY)

    def test_monitor_on_a_new_connector_keeps_pins_and_records_the_new_connector(self):
        pins = [{"id": "1", "app_id": "a", "display": MONITOR_KEY}]
        displays = {MONITOR_KEY: {"connector": "DP-1", "label": "Dell U2723QE"}}
        pin_store.migrate(pins, displays, 2, self.connected({"DP-3": MONITOR}), lambda pin: None)
        self.assertEqual((pins[0]["display"], displays[MONITOR_KEY]["connector"]), (MONITOR_KEY, "DP-3"))

    def test_order_and_moves_are_per_display(self):
        pins = [{"id": "m1", "display": "m"}, {"id": "l1", "display": "l"}, {"id": "m2", "display": "m"},
                {"id": "l2", "display": "l"}]
        pin_store.swap(pins, "m1", "right", "m")
        self.assertEqual([p["id"] for p in pins], ["m2", "l1", "m1", "l2"])
        pin_store.swap(pins, "l1", "left", "l")
        self.assertEqual([p["id"] for p in pins], ["m2", "l1", "m1", "l2"])
        pin_store.move_here(pins, "m2", "l")
        self.assertEqual([(p["id"], p["display"]) for p in pins],
                         [("l1", "l"), ("m1", "m"), ("l2", "l"), ("m2", "l")])
        with self.assertRaisesRegex(ValueError, "already pinned"):
            pin_store.move_here(pins, "m2", "l")

    def test_read_rejects_unknown_formats_and_write_keeps_a_rollback_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spaces.json"
            for bad in ({"version": 3}, {"version": 2, "displays": {}, "pins": [{"id": "1", "display": "gone"}]},
                        [{"id": "1"}, {"id": "1"}]):
                path.write_text(json.dumps(bad))
                with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "Invalid saved spaces"):
                    pin_store.read(path)
            legacy = [{"id": "1", "app_id": "a"}]
            path.write_text(json.dumps(legacy))
            pins, displays, version = pin_store.read(path)
            pin_store.write(path, pins, displays, version)
            self.assertEqual(json.loads((path.parent / "spaces.v1.json").read_text()), legacy)
            self.assertEqual(pin_store.read(path)[2], 2)


class RoutingTest(unittest.TestCase):
    """Two displays: workspace 1 on the laptop, workspace 2 on the external monitor."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        (self.state / "groups.initialized").touch()
        (self.state / "groups.json").write_text('{"version": 1, "groups": []}')
        self.outputs = {"eDP-1": LAPTOP, "DP-1": MONITOR}
        self.workspaces = [{"id": 1, "output": "eDP-1", "is_active": True, "is_focused": True, "active_window_id": 1},
                           {"id": 2, "output": "DP-1", "is_active": True, "is_focused": False, "active_window_id": 3}]
        self.windows = [window(1, "mail", 2, 1), window(2, "notes", 1, 1), window(3, "terminal", 1, 2)]
        self.actions = []

    def focused(self):
        ws = next(w for w in self.workspaces if w["is_focused"])
        return ws, self.windows

    def niri(self, *args):
        self.actions.append(args)
        if args[:2] == ("action", "focus-monitor"):
            for ws in self.workspaces:
                ws["is_focused"] = ws["output"] == args[2]
        elif args[:2] == ("action", "focus-window"):
            target = next(w for w in self.windows if w["id"] == int(args[-1]))
            for ws in self.workspaces:
                ws["is_focused"] = ws["id"] == target["workspace_id"]
                if ws["is_focused"]:
                    ws["active_window_id"] = target["id"]
        elif args[:2] == ("action", "move-column-to-index"):
            pass
        return ""

    def invoke(self, *args, outputs=None):
        with patch.object(sys, "argv", ["flow-spaces", *args]), \
             patch.object(spaces, "snapshot", side_effect=self.focused), \
             patch.object(spaces, "display_state", side_effect=lambda: (outputs or self.outputs, self.workspaces)), \
             patch.object(spaces, "full_state", side_effect=lambda: (*spaces.snapshot(), *spaces.display_state())), \
             patch.object(spaces, "session_id", return_value="session"), \
             patch.object(spaces, "command", side_effect=self.niri), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            spaces.run_locked(self.state)
        return json.loads(output.getvalue())

    def pins(self, pins):
        displays = {LAPTOP_KEY: {"connector": "eDP-1", "label": "BOE Panel"},
                    MONITOR_KEY: {"connector": "DP-1", "label": "Dell U2723QE"}}
        (self.state / "spaces.json").write_text(json.dumps({"version": 2, "displays": displays, "pins": pins}))

    def test_each_display_lists_and_orders_only_its_own_pins(self):
        self.pins([{"id": "t", "label": "Terminal", "app_id": "terminal", "display": MONITOR_KEY},
                   {"id": "m", "label": "Mail", "app_id": "mail", "display": LAPTOP_KEY}])
        result = self.invoke("refresh")
        self.assertEqual([p["id"] for p in result["pins"]], ["m"])
        self.assertEqual([(p["id"], p["display_state"], p["display_label"]) for p in result["other_pins"]],
                         [("t", "connected", "Dell U2723QE")])
        self.assertEqual((result["display"]["key"], result["display_count"]), (LAPTOP_KEY, 2))
        # Mail is pinned first on the laptop, so it moves to column 1 there; the monitor is untouched.
        self.assertIn(("action", "move-column-to-index", 1), self.actions)
        self.assertNotIn(("action", "focus-window", "--id", 3), self.actions)

    def test_the_refresh_names_the_focused_displays_connector_for_the_shell(self):
        # The shell puts the work view's Island and indicators, Desk and Overview on that screen (F-007).
        self.assertEqual(self.invoke("refresh")["display"]["output"], "eDP-1")
        for ws in self.workspaces:
            ws["is_focused"] = ws["output"] == "DP-1"
        self.assertEqual(self.invoke("refresh")["display"]["output"], "DP-1")

    def test_new_pin_belongs_to_the_focused_display(self):
        self.pins([])
        self.workspaces[0]["is_focused"], self.workspaces[1]["is_focused"] = False, True
        result = self.invoke("pin")
        self.assertEqual(result["pins"][0]["display"], MONITOR_KEY)
        self.assertEqual(result["other_pins"], [])

    def test_a_named_window_is_pinned_without_moving_focus_and_only_from_this_workspace(self):
        self.pins([])
        result = self.invoke("pin", "2")
        self.assertEqual([p["window_ids"] for p in result["pins"]], [[2]])
        self.assertEqual(next(w for w in self.workspaces if w["is_focused"])["active_window_id"], 1)
        for arg, message in (("x", "by its number"), ("3", "Select one non-floating")):
            with self.subTest(arg=arg), self.assertRaisesRegex(ValueError, message):
                self.invoke("pin", arg)

    def test_focusing_another_displays_pin_moves_display_focus_first(self):
        self.pins([{"id": "t", "label": "Terminal", "app_id": "terminal", "display": MONITOR_KEY}])
        result = self.invoke("focus", "t")
        self.assertEqual(self.actions[0], ("action", "focus-monitor", "DP-1"))
        self.assertIn(("action", "focus-window", "--id", 3), self.actions)
        self.assertEqual(result["display"]["key"], MONITOR_KEY)

    def test_disconnected_display_pins_focus_in_place_and_are_never_launched(self):
        self.pins([{"id": "t", "label": "Terminal", "app_id": "terminal", "display": MONITOR_KEY},
                   {"id": "x", "label": "Gone", "app_id": "gone", "display": MONITOR_KEY}])
        # Niri moved the monitor's workspace to the laptop when it was unplugged.
        self.workspaces[1]["output"] = "eDP-1"
        self.workspaces[1]["is_active"] = False
        laptop_only = {"eDP-1": LAPTOP}
        result = self.invoke("refresh", outputs=laptop_only)
        self.assertEqual([p["display_state"] for p in result["other_pins"]], ["disconnected", "disconnected"])
        self.assertEqual(result["pins"], [])
        self.invoke("focus", "t", outputs=laptop_only)
        self.assertIn(("action", "focus-window", "--id", 3), self.actions)
        with patch.object(spaces.subprocess, "run") as launch, \
             self.assertRaisesRegex(ValueError, "not connected"):
            self.invoke("focus", "x", outputs=laptop_only)
        launch.assert_not_called()
        saved = pin_store.read(self.state / "spaces.json")[0]
        self.assertEqual([p["display"] for p in saved], [MONITOR_KEY, MONITOR_KEY])

    def test_reconnected_display_gets_its_pins_back_and_move_here_is_explicit(self):
        self.pins([{"id": "t", "label": "Terminal", "app_id": "terminal", "display": MONITOR_KEY}])
        self.invoke("refresh", outputs={"eDP-1": LAPTOP})
        result = self.invoke("refresh")
        self.assertEqual(result["other_pins"][0]["display_state"], "connected")
        result = self.invoke("move-pin-here", "t")
        self.assertEqual([p["id"] for p in result["pins"]], ["t"])
        self.assertEqual(pin_store.read(self.state / "spaces.json")[0][0]["display"], LAPTOP_KEY)

    def test_the_remaining_twin_monitor_keeps_its_pins_after_the_other_is_unplugged(self):
        twin = "port:DP-1|Dell|U2723QE"
        (self.state / "spaces.json").write_text(json.dumps({"version": 2, "displays": {
            twin: {"connector": "DP-1", "label": "Dell U2723QE"}}, "pins": [
            {"id": "t", "label": "Terminal", "app_id": "terminal", "display": twin}]}))
        self.invoke("refresh", outputs={"eDP-1": LAPTOP, "DP-1": MONITOR, "DP-2": MONITOR})
        result = self.invoke("refresh", outputs={"eDP-1": LAPTOP, "DP-1": MONITOR})
        self.assertEqual([(p["id"], p["display_state"]) for p in result["other_pins"]], [("t", "connected")])
        self.assertNotIn(MONITOR_KEY, pin_store.read(self.state / "spaces.json")[1])

    def test_recovery_keys_a_lone_twin_monitor_as_pins_do(self):
        (self.state / "spaces.json").write_text(json.dumps({"version": 2, "pins": [], "displays": {
            "port:DP-1|Dell|U2723QE": {"connector": "DP-1", "label": "Dell U2723QE", "shared_serial": MONITOR_KEY}}}))
        with patch.object(restore.spaces, "query", return_value=[{"DP-1": MONITOR}]):
            key, connected = restore.focused_display({"output": "DP-1"}, self.state)
        self.assertEqual((key, connected), ("port:DP-1|Dell|U2723QE", {"port:DP-1|Dell|U2723QE"}))

    def test_observing_neither_writes_state_nor_moves_windows(self):
        legacy = [{"id": "m", "label": "Mail", "app_id": "mail"}]
        (self.state / "spaces.json").write_text(json.dumps(legacy))
        before = (self.state / "spaces.json").read_bytes()
        result = self.invoke("observe")
        self.assertEqual((self.state / "spaces.json").read_bytes(), before)
        self.assertFalse([a for a in self.actions if a[0] == "action"])
        self.assertEqual(result["pins"][0]["window_ids"], [1])
        self.assertEqual({w["id"] for w in result["all_windows"]}, {1, 2, 3})

    def test_reordering_refuses_a_pin_from_another_display(self):
        self.pins([{"id": "t", "label": "Terminal", "app_id": "terminal", "display": MONITOR_KEY},
                   {"id": "m", "label": "Mail", "app_id": "mail", "display": LAPTOP_KEY}])
        with self.assertRaisesRegex(ValueError, "Move this pin"):
            self.invoke("right", "t")

    def test_legacy_list_is_migrated_once_and_kept_for_rollback(self):
        legacy = [{"id": "m", "label": "Mail", "app_id": "mail"},
                  {"id": "t", "label": "Terminal", "app_id": "terminal", "display": "DP-1"},
                  {"id": "z", "label": "Closed", "app_id": "closed"}]
        (self.state / "spaces.json").write_text(json.dumps(legacy))
        self.invoke("refresh")
        pins, displays, version = pin_store.read(self.state / "spaces.json")
        self.assertEqual(version, 2)
        self.assertEqual([p["display"] for p in pins], [LAPTOP_KEY, MONITOR_KEY, None])
        self.assertEqual(json.loads((self.state / "spaces.v1.json").read_text()), legacy)
        before = (self.state / "spaces.json").read_bytes()
        self.invoke("refresh")
        self.assertEqual((self.state / "spaces.json").read_bytes(), before)

    def test_recovery_refuses_a_session_saved_on_another_connected_display(self):
        (self.state / "session.json").write_text(json.dumps(
            {"version": 1, "display": MONITOR_KEY, "windows": [], "groups": []}))
        with patch.dict(restore.os.environ, {"XDG_STATE_HOME": str(self.state.parent)}), \
             patch.object(restore, "state_path", return_value=self.state / "session.json"), \
             patch.object(sys, "argv", ["flow-recovery", "restore"]), \
             patch.object(restore.spaces, "snapshot", side_effect=self.focused), \
             patch.object(restore.spaces, "session_id", return_value="session"), \
             patch.object(restore.spaces, "query", return_value=[self.outputs]) as query, \
             patch.object(restore.spaces, "command", side_effect=AssertionError("no niri msg")), \
             self.assertRaisesRegex(ValueError, "another connected display"):
            restore.main()
        self.assertEqual(query.call_args_list[0].args, ("Outputs",))  # over Niri's socket, no process
        self.assertEqual(len(query.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()
