"""Flow's state writers, killed part-way through, leave the file they write whole (F-018).

Each writer is paused by session/pause_point.py where a kill hurts most: its new content written and synced to
a temporary file, the rename not yet done. A process stopped there by SIGTERM (as the controller is when its shell
dies: setpriv --pdeathsig TERM, and a handler that exits) or SIGKILL must leave the old file byte for byte, and
after SIGTERM no temporary file either. A writer that wrote in place would leave it cut short at the same point.
"""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SESSION = Path(__file__).resolve().parents[1] / "session"
sys.path.insert(0, str(SESSION))
import pause_point

# What each writer replaces, and the call that replaces it, run in a child that exits on SIGTERM as the
# controller does (session/flow_controller.py, main()).
OLD = {"groups.json": {"version": 1, "groups": []},
       "spaces.json": {"version": 2, "displays": {}, "pins": []},
       "session.json": {"version": 1, "session": None, "windows": [], "groups": []}}
CALLS = {
    "groups.write": ("groups.json", "import groups; groups.write(path, [{'id': 'g', 'label': 'Kept', 'members': ['a', 'b']}])"),
    "spaces.write": ("spaces.json", "import pin_store; pin_store.write(path, [{'id': 'p', 'app_id': 'a', 'label': 'A'}], {}, 2)"),
    "session.write": ("session.json", "import restore; restore.write_snapshot(path, {'version': 1, 'session': None, "
                                      "'windows': [{'app_id': 'a', 'column': 1}] * 200, 'groups': []})"),
}
CHILD = """import signal, sys
from pathlib import Path
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
path = Path(sys.argv[1])
{call}
"""


class GateTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.state = self.home / "isolated"
        self.state.mkdir()
        self.flag = self.state / "paused"
        patcher = patch.object(pause_point, "PAUSE", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def reached(self, env, point="groups.write"):
        with patch.dict(os.environ, env, clear=True):
            return pause_point.reached(point)

    def test_pauses_only_at_its_point_with_isolated_state_and_the_flag_inside(self):
        spec = f"groups.write={self.flag}"
        self.assertTrue(self.reached({"HOME": str(self.home), "XDG_STATE_HOME": str(self.state), "FLOW_TEST_PAUSE": spec}))
        self.assertEqual(self.flag.read_text(), str(os.getpid()))
        self.assertFalse(self.reached({"HOME": str(self.home), "XDG_STATE_HOME": str(self.state), "FLOW_TEST_PAUSE": spec},
                                      "spaces.write"))

    def test_never_pauses_the_default_state_or_a_flag_elsewhere(self):
        default = self.home / ".local/state"
        default.mkdir(parents=True)
        cases = [
            {"HOME": str(self.home), "FLOW_TEST_PAUSE": f"groups.write={self.flag}"},  # no XDG_STATE_HOME
            {"HOME": str(self.home), "XDG_STATE_HOME": str(default), "FLOW_TEST_PAUSE": f"groups.write={default / 'f'}"},
            {"HOME": str(self.home), "XDG_STATE_HOME": str(self.state), "FLOW_TEST_PAUSE": f"groups.write={self.home / 'f'}"},
            {"HOME": str(self.home), "XDG_STATE_HOME": str(self.state), "FLOW_TEST_PAUSE": "groups.write=paused"},
            {"HOME": str(self.home), "XDG_STATE_HOME": str(self.state), "FLOW_TEST_PAUSE": "groups.write"},
        ]
        for env in cases:
            with self.subTest(env=env):
                self.assertFalse(self.reached(env))
        self.assertEqual(sorted(p.name for p in self.home.rglob("*") if p.is_file()), [])


class InterruptedWriteTest(unittest.TestCase):
    def interrupt(self, point, sig):
        """Run the writer for ``point`` until it pauses, send ``sig``; the folder's files afterwards."""
        state = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, state, True)
        name, call = CALLS[point]
        target = state / "flow" / name
        target.parent.mkdir()
        old = json.dumps(OLD[name]).encode()
        target.write_bytes(old)
        flag = state / "paused"
        env = dict(os.environ, PYTHONPATH=str(SESSION), XDG_STATE_HOME=str(state), FLOW_TEST_PAUSE=f"{point}={flag}")
        child = subprocess.Popen([sys.executable, "-c", CHILD.format(call=call), str(target)], env=env)
        try:
            end = time.monotonic() + 20
            while not flag.exists():
                self.assertIsNone(child.poll(), "the writer ended before its pause")
                self.assertLess(time.monotonic(), end, "the writer never paused")
                time.sleep(0.01)
            child.send_signal(sig)
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
        self.assertEqual(target.read_bytes(), old, f"{name} changed when its writer was stopped by {sig.name}")
        json.loads(target.read_bytes())
        return sorted(p.name for p in target.parent.iterdir())

    def test_sigterm_mid_write_keeps_each_file_whole_and_leaves_no_temporary_file(self):
        for point, (name, _) in CALLS.items():
            with self.subTest(point=point):
                self.assertEqual(self.interrupt(point, signal.SIGTERM), [name])

    def test_sigkill_mid_write_keeps_each_file_whole(self):
        for point in CALLS:
            with self.subTest(point=point):
                self.interrupt(point, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
