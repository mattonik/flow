"""flow-spaces reads Niri over its socket: one connection, one request per line, refusals as errors."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
spec = importlib.util.spec_from_file_location("spaces", Path(__file__).parents[1] / "session/spaces.py")
spaces = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spaces)

WORKSPACES = [{"id": 1, "output": "Virtual-1", "is_focused": True, "is_active": True, "active_window_id": 7}]
WINDOWS = [{"id": 7, "app_id": "foot", "title": "shell", "workspace_id": 1}]
OUTPUTS = {"Virtual-1": {"logical": {"width": 1280, "height": 800}}}


class FakeNiri:
    """A Niri socket that answers each request line as Niri does, and counts connections."""

    def __init__(self, path, answers):
        self.answers, self.connections, self.requests = answers, 0, []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            self.connections += 1
            with connection, connection.makefile("rwb") as stream:
                for line in stream:
                    request = json.loads(line)
                    self.requests.append(request)
                    answer = self.answers.get(request)
                    reply = {"Ok": {request: answer}} if answer is not None else {"Err": f"no {request}"}
                    stream.write(json.dumps(reply).encode() + b"\n")
                    stream.flush()

    def close(self):
        self.server.close()


class NiriQueryTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.folder.name, "niri.sock")
        self.niri = FakeNiri(self.path, {"Workspaces": WORKSPACES, "Windows": WINDOWS, "Outputs": OUTPUTS})
        self.env = patch.dict(os.environ, {"NIRI_SOCKET": self.path})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.niri.close()
        self.folder.cleanup()

    def test_full_state_reads_everything_on_one_connection_and_asks_for_workspaces_once(self):
        with patch.object(spaces, "command", side_effect=AssertionError("niri msg must not run")):
            workspace, windows, outputs, workspaces = spaces.full_state()
        self.assertEqual((workspace, windows, outputs, workspaces), (WORKSPACES[0], WINDOWS, OUTPUTS, WORKSPACES))
        self.assertEqual(self.niri.connections, 1)
        self.assertEqual(sorted(self.niri.requests), ["Outputs", "Windows", "Workspaces"])

    def test_snapshot_and_display_state_read_the_socket(self):
        self.assertEqual(spaces.snapshot(), (WORKSPACES[0], WINDOWS))
        self.assertEqual(spaces.display_state(), (OUTPUTS, WORKSPACES))

    def test_a_refusal_is_an_error_not_an_empty_answer(self):
        self.niri.answers.pop("Windows")
        with self.assertRaisesRegex(ValueError, "Niri did not answer Windows"):
            spaces.snapshot()

    def test_no_focused_workspace_is_an_error(self):
        self.niri.answers["Workspaces"] = [{**WORKSPACES[0], "is_focused": False}]
        with self.assertRaisesRegex(ValueError, "No active workspace"):
            spaces.full_state()

    def test_without_a_socket_it_says_so(self):
        with patch.dict(os.environ, {"NIRI_SOCKET": ""}), self.assertRaisesRegex(ValueError, "NIRI_SOCKET"):
            spaces.snapshot()
        with patch.dict(os.environ, {"NIRI_SOCKET": self.path + ".gone"}), self.assertRaises(OSError):
            spaces.snapshot()


class OrientationTest(unittest.TestCase):
    """The shell runs automatic Group layout when a Group does not suit the orientation flow-spaces reports."""

    def observe(self, logical):
        with tempfile.TemporaryDirectory() as state, \
             patch.object(sys, "argv", ["flow-spaces", "observe"]), \
             patch.object(spaces, "full_state", return_value=(WORKSPACES[0], WINDOWS, {"Virtual-1": {"logical": logical}}, WORKSPACES)), \
             patch.object(spaces, "session_id", return_value="s"), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            spaces.run_locked(Path(state))
        return json.loads(out.getvalue())["orientation"]

    def test_labels_say_what_they_are_without_a_title(self):
        """The shell relabels windows from Niri's events with ``untitled`` and ``label_join``, and nothing else."""
        tiled = [{"id": 7, "app_id": "foot", "title": "", "workspace_id": 1, "is_floating": False,
                  "layout": {"pos_in_scrolling_layout": [1, 1]}},
                 {"id": 8, "app_id": "", "title": "notes", "workspace_id": 1, "is_floating": False,
                  "layout": {"pos_in_scrolling_layout": [1, 2]}}]
        with tempfile.TemporaryDirectory() as state, \
             patch.object(sys, "argv", ["flow-spaces", "observe"]), \
             patch.object(spaces, "full_state", return_value=(WORKSPACES[0], tiled, OUTPUTS, WORKSPACES)), \
             patch.object(spaces, "session_id", return_value="s"), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            spaces.run_locked(Path(state))
        result = json.loads(out.getvalue())
        self.assertEqual([(w["label"], w["untitled"]) for w in result["windows"]], [("foot", "foot"), ("notes", "Application")])
        [space] = result["spaces"]
        self.assertEqual([(m["label"], m["untitled"]) for m in space["members"]], [("foot", "foot"), ("notes", "Application")])
        self.assertEqual((space["label"], space["label_join"]), ("foot + notes", " + "))

    def test_orientation_of_the_focused_output(self):
        self.assertEqual(self.observe({"width": 1280, "height": 800}), "landscape")
        self.assertEqual(self.observe({"width": 800, "height": 1280}), "portrait")
        self.assertEqual(self.observe({}), "")


if __name__ == "__main__":
    unittest.main()
