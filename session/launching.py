"""How Flow starts the apps it opens.

The installed shell runs as the flow-shell user unit and sets FLOW_APP_SCOPES=1. Apps it opens,
directly or through Flow's helpers, then each get their own transient systemd scope, so they are
not part of the shell's unit: restarting or stopping the shell never touches them, and a limit on
the shell never applies to them. Elsewhere (a development shell, tests) apps start directly.
"""
import os

SCOPE = ["systemd-run", "--user", "--scope", "--collect", "--quiet", "--"]


def app_command(args):
    """``args`` run in an app scope of their own when the installed shell asks for it."""
    return [*SCOPE, *args] if os.environ.get("FLOW_APP_SCOPES") == "1" else list(args)
