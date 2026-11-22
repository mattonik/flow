#!/bin/sh
# Runs tests/appmenu_coverage.py (the app-menu coverage table) inside a Niri session where Flow is installed:
# its flow-appmenu wrapper names a Python with pyatspi and PyGObject. Holds the screen lock for the whole run,
# since the run opens app windows. Arguments go to the script; see its docstring.
# On the test guest:
#   FLOW_NIX="sudo -n nix" \
#   FLOW_NIX_ARGS="--option ssl-cert-file /etc/floe-test/host-trust.pem --option flake-registry ''" \
#     tests/run_appmenu_coverage.sh --json /tmp/appmenu-coverage.json
# (flake-registry '' keeps nixpkgs at the system's pinned revision rather than the global registry's.)
set -eu
checkout=$(cd "$(dirname "$0")/.." && pwd)
appmenu=$(command -v flow-appmenu) || { echo "flow-appmenu not found: install Flow" >&2; exit 1; }
for tool in niri busctl nix; do
    command -v "$tool" >/dev/null || { echo "$tool not found" >&2; exit 1; }
done
eval "$(grep '^export GI_TYPELIB_PATH' "$appmenu")"
python=$(sed -n 's/^exec \([^ ]*python3\) .*/\1/p' "$appmenu")
[ -n "${GI_TYPELIB_PATH:-}" ] && [ -n "$python" ] \
    || { echo "$appmenu names no Python and GI_TYPELIB_PATH: its shape has changed" >&2; exit 1; }
export GI_TYPELIB_PATH
# The session's own environment, so apps start as they do from Flow (an ssh login has no XDG_SESSION_TYPE,
# DISPLAY or NIXOS_OZONE_WL, and Qt 5 then falls back to X11).
eval "$(systemctl --user show-environment \
    | grep -E '^(XDG_SESSION_TYPE|XDG_CURRENT_DESKTOP|DISPLAY|NIXOS_OZONE_WL|WAYLAND_DISPLAY)=' | sed 's/^/export /')"
# Not NO_AT_BRIDGE or GTK_A11Y: a session started before the Flow module enabled at-spi2-core still exports them,
# and they switch GTK's accessibility off. The table describes a session started with at-spi2-core enabled; the
# mousepad-login-env row sets them on purpose.
unset NO_AT_BRIDGE GTK_A11Y
# A bus named directly would bypass the run's private buses.
unset AT_SPI_BUS_ADDRESS DBUS_STARTER_ADDRESS DBUS_STARTER_BUS_TYPE
exec 9>"${FLOW_SCREEN_LOCK:-/tmp/floe-screen.lock}"
flock -w 1800 9 || { echo "screen lock busy" >&2; exit 1; }
"$python" "$checkout/tests/appmenu_coverage.py" "$@"
