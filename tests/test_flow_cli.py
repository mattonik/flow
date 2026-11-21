"""The flow CLI's activity commands never break the work they report on (ADR-0010).

PyGObject is not needed: a minimal stand-in for ``gi`` is installed before the CLI is imported, and
the D-Bus call is replaced by one that drives the real ActivityBook.
"""
import importlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "session"))
import flow_api_core as core


class FakeError(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class Variant:
    def __init__(self, signature, value):
        self.signature, self.value = signature, value

    def unpack(self):
        value = self.value
        if isinstance(value, dict):
            return {k: v.unpack() if isinstance(v, Variant) else v for k, v in value.items()}
        if isinstance(value, tuple):
            return tuple(v.unpack() if isinstance(v, Variant) else (
                {k: x.unpack() for k, x in v.items()} if isinstance(v, dict) else v) for v in value)
        return value


def load_cli():
    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    glib = types.SimpleNamespace(Variant=Variant, Error=FakeError, VariantType=lambda s: s, MainLoop=None)
    gio = types.SimpleNamespace(DBusError=types.SimpleNamespace(strip_remote_error=lambda e: None,
                                                                get_remote_error=lambda e: None),
                                BusType=types.SimpleNamespace(SESSION=0), DBusCallFlags=types.SimpleNamespace(NONE=0))
    repository.GLib, repository.Gio = glib, gio
    gi.repository = repository
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        sys.modules.pop("flow_cli", None)
        return importlib.import_module("flow_cli")


class Service:
    """The service side of the activity calls, with failures that can be switched on."""

    def __init__(self):
        self.book = core.ActivityBook()
        self.calls = []
        self.fail = None

    def __call__(self, bus, interface, method, args=None, reply="()"):
        values = args.unpack() if args is not None else ()
        self.calls.append((method, values))
        if self.fail and self.fail(method):
            raise FakeError(f"GDBus.Error:{core.NAME}.Error.RateLimited: Too many requests")
        if method == "Start":
            return (self.book.start(":1.9", "make", values[0], values[1]),)
        if method == "Update":
            self.book.update(":1.9", values[0], values[1])
        elif method == "Finish":
            self.book.finish(":1.9", values[0])
        return ()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.service = Service()
        patcher = patch.object(self.cli, "call", self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def feed(self, data):
        read, write = os.pipe()
        os.write(write, data)
        os.close(write)
        stream = os.fdopen(read, "rb")
        self.addCleanup(stream.close)
        return stream

    def updates(self):
        return [values[1] for method, values in self.service.calls if method == "Update"]

    def test_progress_sends_the_last_value_even_without_a_newline_and_reads_carriage_returns(self):
        status = self.cli.progress(None, "Copy", self.feed(b"10\r20\r nan\rCopying a.jpg\n100"), interval=0)
        self.assertEqual(status, 0)
        self.assertEqual(self.updates()[-1], {"progress": 1.0})
        self.assertIn("Copying a.jpg", [u.get("detail") for u in self.updates()])
        self.assertEqual([self.cli.progress_value(text) for text in ("50%", " 7.5 ", "nan", "inf", "-inf", "\u0663", "", "x")],
                         [0.5, 0.075, None, None, None, None, None, None])
        self.assertEqual(self.service.book.public(), [])

    def test_a_refused_update_never_stops_reading_or_fails_the_pipeline(self):
        self.service.fail = lambda method: method in ("Update", "Finish")
        produce = "import sys; sys.stdout.buffer.write(b''.join(b'%d\\n' % n for n in range(100000)))"  # more than a pipe holds
        writer = subprocess.Popen([sys.executable, "-c", produce], stdout=subprocess.PIPE)
        self.addCleanup(writer.stdout.close)
        with patch("sys.stderr"):
            status = self.cli.progress(None, "Copy", writer.stdout, interval=0)
        self.assertEqual((status, writer.wait(timeout=10)), (0, 0))  # the producer saw no broken pipe
        self.assertEqual(sum(method == "Update" for method, _ in self.service.calls), 1)  # then it stopped trying

    def test_run_always_returns_the_commands_own_status(self):
        self.assertEqual(self.cli.run(None, "Build", [sys.executable, "-c", "raise SystemExit(3)"]), 3)
        self.service.fail = lambda method: method == "Finish"
        with patch("sys.stderr"):
            self.assertEqual(self.cli.run(None, "Build", [sys.executable, "-c", "pass"]), 0)
        self.service.fail = lambda method: True
        with patch("sys.stderr"):
            self.assertEqual(self.cli.run(None, "Build", [sys.executable, "-c", "pass"]), 0)
        self.service.fail = None
        killed = self.cli.run(None, "Build", [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"])
        self.assertEqual(killed, 128 + signal.SIGTERM)

    def test_run_passes_a_termination_signal_on_to_the_command(self):
        read, write = os.pipe()
        helper = os.fork()
        if helper == 0:  # once the command says it is ready, send flow (this process) SIGTERM
            os.close(write)
            os.read(read, 6)
            os.kill(os.getppid(), signal.SIGTERM)
            os._exit(0)
        os.close(read)
        command = [sys.executable, "-c", "import os, signal, sys, time\n"
                                         "signal.signal(signal.SIGTERM, lambda *_: sys.exit(7))\n"
                                         f"os.write({write}, b'ready\\n')\n"
                                         "time.sleep(30)\n"]
        original = subprocess.Popen
        with patch.object(self.cli.subprocess, "Popen", lambda args: original(args, pass_fds=(write,))):
            status = self.cli.run(None, "Build", command)
        os.close(write)
        os.waitpid(helper, 0)
        self.assertEqual(status, 7)  # the command got the SIGTERM and chose its own way out
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)  # flow put the handler back

if __name__ == "__main__":
    unittest.main()
