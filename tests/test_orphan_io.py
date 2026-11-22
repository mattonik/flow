"""Flow's helpers keep working after the shell that started them, and the pipes it read, are gone."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

from test_niri_query import FakeNiri

SESSION = Path(__file__).resolve().parents[1] / "session"
# Niri, standing in on its socket: one focused, empty workspace on one output.
NIRI_ANSWERS = {
    "Workspaces": [{"id": 1, "idx": 1, "output": "Virtual-1", "is_active": True, "is_focused": True, "active_window_id": None}],
    "Windows": [],
    "Outputs": {"Virtual-1": {"name": "Virtual-1", "make": "Flow", "model": "Test", "serial": None,
                              "logical": {"width": 1280, "height": 800, "transform": "Normal"}}},
}


def orphaned(command, env=None):
    """Run ``command`` with its stdout and stderr read by nobody, as after the shell that started it died.

    It starts with stdin open and goes on once stdin closes, by when the pipes' read ends are already gone.
    """
    process = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    process.stdout.close()
    process.stderr.close()
    process.stdin.close()
    return process.wait(timeout=30)


class OrphanIoTest(unittest.TestCase):
    def test_writes_with_the_reader_gone_do_not_stop_the_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = Path(tmp) / "done"
            script = textwrap.dedent(f"""
                import sys
                sys.path.insert(0, {str(SESSION)!r})
                import orphan_io
                kept = orphan_io.guard()
                sys.stdin.read()
                print("changing the Group layout", file=sys.stderr)  # part-way through an action
                print("partial result")
                open({str(done)!r}, "w").write("done")
                orphan_io.finish(kept)
            """)
            self.assertEqual(orphaned([sys.executable, "-c", script]), 0)
            self.assertEqual(done.read_text(), "done")

    def test_recovery_finishes_with_its_reader_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, XDG_STATE_HOME=tmp, PYTHONPATH=str(SESSION))
            command = [sys.executable, "-c", f"import sys; sys.stdin.read(); sys.argv = ['restore.py', 'status']; "
                       f"exec(compile(open({str(SESSION / 'restore.py')!r}).read(), 'restore.py', 'exec'))"]
            self.assertEqual(orphaned(command, env), 0)

    @unittest.skipUnless(Path("/proc/sys/kernel/random/boot_id").exists(), "needs Linux's boot ID")
    def test_spaces_finishes_with_its_reader_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            niri = FakeNiri(str(tmp / "niri.sock"), NIRI_ANSWERS)
            self.addCleanup(niri.close)
            env = dict(os.environ, XDG_STATE_HOME=str(tmp / "state"), PYTHONPATH=str(SESSION),
                       NIRI_SOCKET=str(tmp / "niri.sock"))
            answer = subprocess.run([sys.executable, str(SESSION / "spaces.py"), "refresh"], env=env, text=True,
                                    stdout=subprocess.PIPE, timeout=30)
            self.assertEqual((answer.returncode, json.loads(answer.stdout).get("error")), (0, None))
            command = [sys.executable, "-c", f"import sys; sys.stdin.read(); sys.argv = ['spaces.py', 'refresh']; "
                       f"exec(compile(open({str(SESSION / 'spaces.py')!r}).read(), 'spaces.py', 'exec'))"]
            self.assertEqual(orphaned(command, env), 0)


if __name__ == "__main__":
    unittest.main()
