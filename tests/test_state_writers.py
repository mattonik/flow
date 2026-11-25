"""Flow's state writers make each new file durable: its contents synced before the rename, and the folder synced
after it, so a power cut cannot bring back the file from before a write that had finished."""

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "session"))
import groups
import pin_store
import restore

WRITES = {
    "groups.json": lambda path: groups.write(path, [{"id": "g", "label": "Kept", "members": ["a", "b"]}]),
    "spaces.json": lambda path: pin_store.write(path, [{"id": "p", "app_id": "a", "label": "A"}], {}, 2),
    "session.json": lambda path: restore.write_snapshot(path, {"version": 1, "session": None, "windows": [],
                                                              "groups": []}),
}


class DurableWriteTest(unittest.TestCase):
    def events(self, name):
        """What the writer of ``name`` synced and renamed, in order: ("file" | "folder" | "rename", path)."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / name
        target.write_text(json.dumps({}))
        seen, fsync, replace = [], os.fsync, os.replace

        def synced(fd):
            kind = "folder" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
            seen.append((kind, os.path.realpath(os.readlink(f"/proc/self/fd/{fd}")) if os.path.exists("/proc/self/fd")
                         else None))
            return fsync(fd)

        def renamed(source, destination):
            seen.append(("rename", os.path.realpath(destination)))
            return replace(source, destination)

        with patch("os.fsync", synced), patch("os.replace", renamed):
            WRITES[name](target)
        return seen, os.path.realpath(target), os.path.realpath(target.parent)

    def test_each_writer_syncs_its_file_then_renames_then_syncs_the_folder(self):
        for name in WRITES:
            with self.subTest(name=name):
                seen, target, folder = self.events(name)
                kinds = [kind for kind, _ in seen]
                self.assertIn("rename", kinds)
                rename = kinds.index("rename")
                self.assertEqual(seen[rename][1], target)
                self.assertIn("file", kinds[:rename])
                self.assertIn("folder", kinds[rename + 1:])
                if seen[0][1] is not None:  # Linux names each synced descriptor's file
                    self.assertIn(("folder", folder), seen[rename + 1:])


if __name__ == "__main__":
    unittest.main()
