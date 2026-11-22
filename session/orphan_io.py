"""Output for Flow's helpers that may outlive the shell that started them.

A shell restart does not end a ``flow-spaces`` or ``flow-recovery`` action, but it closes the pipes the
action prints to. Python raises on a write to a closed pipe, so one write part-way through would stop the
action half-done. ``guard()`` makes every write safe: stdout is kept and written once by ``finish()``, and a
write to stderr that fails is dropped.
"""

import io
import os
import sys


class _Forgiving(io.TextIOBase):
    """A text stream whose writes never raise: what cannot be written is dropped."""

    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        try:
            self.stream.write(text)
            self.stream.flush()
        except OSError:
            pass
        return len(text)


def guard():
    """Keep stdout until ``finish()``, and make stderr forgiving. Returns the kept output."""
    kept = io.StringIO()
    sys.stdout = kept
    sys.stderr = _Forgiving(sys.__stderr__)
    return kept


def finish(kept):
    """Write the kept output; if its reader has gone, drop it, so exiting does not fail on it either."""
    try:
        sys.__stdout__.write(kept.getvalue())
        sys.__stdout__.flush()
    except OSError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.__stdout__.fileno())
