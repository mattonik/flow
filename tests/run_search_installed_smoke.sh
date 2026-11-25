#!/bin/sh
# Search on the installed system (F-008): tests/niri_search_installed_smoke.py against the installed shell and
# flow-search, from this checkout, under the guest's screen lock. Extra arguments go to the test (--only NAME).
# Run in the guest, in the session's environment, from the checkout's root.
set -eu
eval "$(systemctl --user show-environment | grep -E '^(NIRI_SOCKET|WAYLAND_DISPLAY|XDG_RUNTIME_DIR|PATH)=' | sed 's/^/export /')"
exec 9>/tmp/floe-screen.lock
flock -w 1800 9
exec python3 tests/niri_search_installed_smoke.py "$@"
