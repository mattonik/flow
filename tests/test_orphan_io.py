"""Flow's helpers keep working after the shell that started them, and the pipes it read, are gone."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

SESSION = Path(__file__).resolve().parents[1] / "session"
FAKE_NIRI = """#!/bin/sh
# Niri, standing in: one focused, empty workspace on one output.
case "$*" in
  "msg -j workspaces") echo '[{"id": 1, "idx": 1, "output": "Virtual-1", "is_active": true, "is_focused": true, "active_window_id": null}]' ;;
  "msg -j windows") echo '[]' ;;
  "msg -j outputs") echo '{"Virtual-1": {"name": "Virtual-1", "make": "Flow", "model": "Test", "serial": null, "logical": {"width": 1280, "height": 800, "transform": "Normal"}}}' ;;
  *) exit 1 ;;
esac
"""


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
            (tmp / "bin").mkdir()
            (tmp / "bin" / "niri").write_text(FAKE_NIRI)
            (tmp / "bin" / "niri").chmod(0o755)
            (tmp / "niri.sock").touch()
            env = dict(os.environ, XDG_STATE_HOME=str(tmp / "state"), PYTHONPATH=str(SESSION),
                       PATH=f"{tmp / 'bin'}:{os.environ['PATH']}", NIRI_SOCKET=str(tmp / "niri.sock"))
            answer = subprocess.run([sys.executable, str(SESSION / "spaces.py"), "refresh"], env=env, text=True,
                                    stdout=subprocess.PIPE, timeout=30)
            self.assertEqual((answer.returncode, json.loads(answer.stdout).get("error")), (0, None))
            command = [sys.executable, "-c", f"import sys; sys.stdin.read(); sys.argv = ['spaces.py', 'refresh']; "
                       f"exec(compile(open({str(SESSION / 'spaces.py')!r}).read(), 'spaces.py', 'exec'))"]
            self.assertEqual(orphaned(command, env), 0)


if __name__ == "__main__":
    unittest.main()
