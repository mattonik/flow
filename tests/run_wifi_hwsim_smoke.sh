#!/bin/sh
# Runs tests/niri_wifi_hwsim_smoke.py against this checkout's shell and this checkout's flow-wifi-settings,
# inside a Niri session where Flow is installed (its flow-appmenu wrapper names a Python with pyatspi and
# PyGObject). Needs iw, hostapd, flow-pointer and passwordless sudo; stop the installed shell first
# (systemctl --user stop flow-shell). The wrapper comes from the module check CI builds
# (checks.aarch64-linux.module), built offline: on an installed Flow every input is in the store already, and
# the test guest reaches the binary cache only with sudo and an ssl-cert-file option. FLOW_WIFI_SETTINGS may
# name an already built flow-wifi-settings store path instead.
set -eu
checkout=$(cd "$(dirname "$0")/.." && pwd)
appmenu=$(command -v flow-appmenu) || { echo "flow-appmenu not found: install Flow" >&2; exit 1; }
for tool in niri quickshell nmcli iw hostapd flow-pointer grim ping dbus-run-session sudo; do
    command -v "$tool" >/dev/null || { echo "$tool not found" >&2; exit 1; }
done
eval "$(grep '^export GI_TYPELIB_PATH' "$appmenu")"
python=$(sed -n 's/^exec \([^ ]*python3\) .*/\1/p' "$appmenu")
[ -n "${GI_TYPELIB_PATH:-}" ] && [ -n "$python" ] \
    || { echo "$appmenu names no Python and GI_TYPELIB_PATH: its shape has changed" >&2; exit 1; }
if [ -z "${FLOW_WIFI_SETTINGS:-}" ]; then
    check=$(nix build --offline --no-link --print-out-paths "path:$checkout#checks.aarch64-linux.module") \
        || { echo "Could not build this checkout's module check; build it and set FLOW_WIFI_SETTINGS" >&2; exit 1; }
    FLOW_WIFI_SETTINGS=$(grep -e '-flow-wifi-settings$' "$check/wrappers") \
        || { echo "$check/wrappers lists no flow-wifi-settings" >&2; exit 1; }
fi
[ -x "$FLOW_WIFI_SETTINGS/bin/flow-wifi-settings" ] || { echo "$FLOW_WIFI_SETTINGS has no bin/flow-wifi-settings" >&2; exit 1; }
export PATH="$FLOW_WIFI_SETTINGS/bin:$PATH" GI_TYPELIB_PATH
dbus-run-session -- env FLOW_SHELL="${FLOW_SHELL:-$checkout/session/shell.qml}" \
    "$python" "$checkout/tests/niri_wifi_hwsim_smoke.py"
