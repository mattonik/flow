"""The Niri smokes' isolated state is removed only once nothing started with it runs."""

import os
from pathlib import Path
import subprocess
import time
import unittest

from isolated_state import IsolatedState


@unittest.skipUnless(Path("/proc/self/environ").exists(), "needs Linux /proc")
class IsolatedStateTest(unittest.TestCase):
    def setUp(self):
        self.state = IsolatedState("flow-isolated-state-test-")
        self.env = dict(os.environ, XDG_STATE_HOME=self.state.path)
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait()
        subprocess.run(["rm", "-rf", self.state.path])

    def start(self, script, env=None):
        process = subprocess.Popen(["sh", "-c", script, "sh", self.state.path], env=env or self.env)
        self.processes.append(process)
        return process

    def test_waits_for_a_helper_that_writes_the_folder_after_the_shell_stopped(self):
        # Like a flow-spaces helper that outlives the shell: it writes its state a moment later, well
        # before the check a second after removal would look.
        self.start('sleep 0.6; mkdir -p "$1/flow"; echo {} > "$1/flow/spaces.json"')
        self.assertEqual(self.state.remove(), [])
        self.assertFalse(Path(self.state.path).exists())

    def test_reports_the_folder_written_again_after_removal(self):
        # A writer the scans cannot see (the path reaches it some other way than its environment).
        self.start('sleep 0.5; mkdir -p "$1/flow"', env=dict(os.environ))
        self.assertEqual(self.state.remove(), [f"folder {self.state.path}"])

    def test_reports_a_process_started_with_the_state_after_removal(self):
        # Started without the state, it takes it on only after the folder is removed.
        late = self.start('sleep 0.5; exec env XDG_STATE_HOME="$1" sleep 5', env=dict(os.environ))
        self.assertEqual(self.state.remove(), [f"process {late.pid}"])

    def test_kills_and_reports_a_process_that_never_exits(self):
        stuck = self.start("sleep 30")
        time.sleep(0.2)
        leftover = self.state.remove(timeout=1)
        self.assertEqual(len(leftover), 1)
        self.assertIn(f"process {stuck.pid}", leftover[0])
        self.assertIn("killed after 1 s", leftover[0])
        self.assertEqual(stuck.wait(timeout=5), -9)
        self.assertFalse(Path(self.state.path).exists())

    def test_leaving_the_block_fails_on_what_outlived_it(self):
        with self.assertRaisesRegex(RuntimeError, "outlived the test: folder"):
            with self.state as tmp:
                self.start('sleep 0.5; mkdir -p "$1/flow"', env=dict(os.environ))
        self.assertEqual(tmp, self.state.path)

    def test_leaving_the_block_on_an_error_removes_the_folder_and_keeps_the_error(self):
        with self.assertRaisesRegex(ValueError, "the smoke's own failure"):
            with self.state as tmp:
                self.start('sleep 0.5; mkdir -p "$1/flow"')
                raise ValueError("the smoke's own failure")
        self.assertFalse(Path(tmp).exists())

    def test_an_error_in_the_block_is_not_replaced_by_what_outlived_it(self):
        # The smoke's own failure is the message to see, even when something also outlived it.
        with self.assertRaisesRegex(ValueError, "the smoke's own failure"):
            with self.state:
                self.start('sleep 0.5; mkdir -p "$1/flow"', env=dict(os.environ))
                raise ValueError("the smoke's own failure")


if __name__ == "__main__":
    unittest.main()
