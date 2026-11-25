"""What the shell's and the Platform API's helpers say on stderr reaches a journal.

The shell and the API run as the flow-shell and flow-api user units, whose own stderr is the journal. A process
whose stderr they drop says its messages (a temporary file removed, why an open failed, a traceback) to nobody.
"""

import ast
from pathlib import Path
import re
import unittest

SESSION = Path(__file__).resolve().parents[1] / "session"
SHELL = (SESSION / "shell.qml").read_text()


def process_blocks(qml):
    """Each ``Process { ... }`` body, by brace depth, with its id."""
    blocks = {}
    for match in re.finditer(r"\bProcess\s*\{", qml):
        depth, index = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(qml[index], 0)
            index += 1
        body = qml[match.end():index - 1]
        ident = re.search(r"^\s*id:\s*(\w+)", body, re.M)
        blocks[ident.group(1) if ident else f"anonymous at {match.start()}"] = body
    return blocks


class HelperLogTest(unittest.TestCase):
    def test_every_shell_process_passes_its_stderr_on(self):
        blocks = process_blocks(SHELL)
        self.assertGreaterEqual(len(blocks), 10)
        for ident, body in sorted(blocks.items()):
            with self.subTest(process=ident):
                self.assertRegex(body, r'stderr:\s*SplitParser\s*\{\s*onRead:\s*line\s*=>\s*console\.warn\("[\w-]+: " \+ line\)')

    def test_the_api_keeps_its_flow_spaces_stderr(self):
        tree = ast.parse((SESSION / "flow_api.py").read_text())
        run_spaces = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "run_spaces")
        calls = [node for node in ast.walk(run_spaces) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "run"]
        self.assertEqual(len(calls), 1)
        self.assertNotIn("stderr", {keyword.arg for keyword in calls[0].keywords})


if __name__ == "__main__":
    unittest.main()
