"""Activities (ADR-0010): owned by a connection, limited, expiring, and labelled by the service."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
import flow_api_core as core


class ActivityTest(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.book = core.ActivityBook(clock=lambda: self.now[0])

    def test_an_activity_belongs_to_its_connection_and_ends_with_it(self):
        build = self.book.start(":1.4", "make", "Build", {"progress": 0.25, "detail": "Compiling"})
        self.assertEqual(self.book.public(), [{"id": build, "owner": "make", "title": "Build",
                                               "detail": "Compiling", "progress": 0.25}])
        for call in (lambda: self.book.update(":1.9", build, {"progress": 1}),
                     lambda: self.book.finish(":1.9", build)):
            with self.assertRaises(core.ApiError) as raised:
                call()
            self.assertEqual(raised.exception.kind, "NotFound")
        self.book.update(":1.4", build, {"progress": 0.5})
        self.assertEqual(self.book.public()[0]["progress"], 0.5)
        self.assertFalse(self.book.drop_connection(":1.9"))
        self.assertTrue(self.book.drop_connection(":1.4"))
        self.assertEqual(self.book.public(), [])

    def test_a_caller_shows_at_most_three_and_finished_ones_free_a_place(self):
        ids = [self.book.start(":1.4", "app", f"Job {n}", {}) for n in range(3)]
        with self.assertRaises(core.ApiError) as raised:
            self.book.start(":1.4", "app", "Job 4", {})
        self.assertEqual(raised.exception.kind, "RateLimited")
        self.book.start(":1.5", "other", "Their job", {})
        self.book.finish(":1.4", ids[0])
        self.book.start(":1.4", "app", "Job 4", {})
        self.assertEqual(len(self.book.public()), 4)

    def test_a_flatpak_app_cannot_exceed_its_limit_with_more_connections(self):
        for n, connection in enumerate((":1.4", ":1.5", ":1.6")):
            self.book.start(connection, "org.example.Spam", f"Job {n}", {}, caller="app:org.example.Spam")
        with self.assertRaises(core.ApiError) as raised:
            self.book.start(":1.7", "org.example.Spam", "Job 4", {}, caller="app:org.example.Spam")
        self.assertEqual(raised.exception.kind, "RateLimited")
        # Each connection still owns only what it started.
        self.assertTrue(self.book.drop_connection(":1.5"))
        self.assertEqual(len(self.book.public()), 2)

    def test_activities_last_until_finished_and_the_newest_comes_first(self):
        old = self.book.start(":1.4", "app", "Old", {})
        self.now[0] += 3600
        new = self.book.start(":1.5", "app", "New", {})
        self.assertEqual([a["id"] for a in self.book.public()], [new, old])
        self.now[0] += 1
        self.book.update(":1.4", old, {"progress": 0.5})
        self.assertEqual([a["id"] for a in self.book.public()], [old, new])
        for title in ("", "   ", "\u2800\u3164"):
            with self.subTest(title=title), self.assertRaises(core.ApiError) as raised:
                self.book.start(":1.6", "app", title, {})
            self.assertEqual(raised.exception.kind, "InvalidArgs")

    def test_text_is_one_printable_line_within_limits_and_bad_values_change_nothing(self):
        ident = self.book.start(":1.4", "app", "Build\n\x1b[31m done​ " + "x" * 90, {})
        title = self.book.public()[0]["title"]
        self.assertEqual(len(title), core.TITLE_MAX)
        self.assertTrue(title.startswith("Build [31m done") and title.endswith("…"))
        self.assertNotIn("\x1b", title)
        self.assertEqual(core.clean("e" + "\u0301" * 9, 80), "e\u0301\u0301")
        for changes in ({"progress": 2}, {"progress": True}, {"progress": "half"}, {"progress": float("nan")},
                        {"title": "  \n "},
                        {"detail": 7}, {"title": "Fine", "progress": 3}):
            with self.subTest(changes=changes), self.assertRaises(core.ApiError):
                self.book.update(":1.4", ident, changes)
        self.assertEqual(self.book.public()[0]["title"], title)
        with self.assertRaises(core.ApiError):
            self.book.start(":1.4", "app", "Bad", {"progress": 5})
        self.assertEqual(len(self.book.public()), 1)

    def test_the_owner_label_comes_from_the_caller_never_from_the_app(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "300" / "root").mkdir(parents=True)
            (proc / "300" / "stat").write_text("300 (make) S 1 1 1 0 -1\n")
            (proc / "300" / "comm").write_text("make\n")
            (proc / "301" / "root").mkdir(parents=True)
            (proc / "301" / "root" / ".flatpak-info").write_text("[Application]\nname=org.example.Files\n")
            # The flow CLI run by make: the activity says make, not flow.
            (proc / "302" / "root").mkdir(parents=True)
            (proc / "302" / "stat").write_text("302 (python3.14) S 300 1 1 0 -1\n")
            (proc / "302" / "comm").write_text("python3.14\n")
            tool, files, cli = core.caller(300, proc), core.caller(301, proc), core.caller(302, proc)
            self.assertEqual(core.owner_label(tool, proc), "make")
            self.assertEqual(core.owner_label(cli, proc), "make")
        # A Flatpak app is shown by the app ID Flatpak verified, never a name its developer wrote.
        self.assertEqual(core.owner_label(files), "org.example.Files")


if __name__ == "__main__":
    unittest.main()
