#!/bin/sh
# Runs tests/niri_appmenu_smoke.py against this checkout's shell and app-menu reader, inside a Niri session where
# Flow is installed (its flow-appmenu wrapper names a Python with pyatspi and PyGObject). flow-appmenu,
# flow-spaces and flow-search run this checkout's session/, from wrappers in a temporary directory.
#
# Everything runs on a private session bus (dbus-run-session) with its own runtime directory, so its
# accessibility bus is the run's own, and with GSettings in a key file there: the accessibility switch the reader
# turns on (at-spi persists it through GSettings) never reaches the session's switch or the user's dconf.
#
# Stop the installed shell first (systemctl --user stop flow-shell): the pointer would reach its surfaces too.
# Holds the screen lock for the run. On the test guest:
#   FLOW_NIX="sudo -n nix" \
#   FLOW_NIX_ARGS="--option ssl-cert-file /etc/floe-test/host-trust.pem --option flake-registry ''" \
#     tests/run_appmenu_smoke.sh
set -eu
checkout=$(cd "$(dirname "$0")/.." && pwd)
appmenu=$(command -v flow-appmenu) || { echo "flow-appmenu not found: install Flow" >&2; exit 1; }
for tool in foot niri quickshell grim dbus-run-session busctl flow-pointer; do
    command -v "$tool" >/dev/null || { echo "$tool not found" >&2; exit 1; }
done
eval "$(grep '^export GI_TYPELIB_PATH' "$appmenu")"
python=$(sed -n 's/^exec \([^ ]*python3\) .*/\1/p' "$appmenu")
[ -n "${GI_TYPELIB_PATH:-}" ] && [ -n "$python" ] \
    || { echo "$appmenu names no Python and GI_TYPELIB_PATH: its shape has changed" >&2; exit 1; }
glib=$(grep -o '/nix/store/[^/:"]*-glib-[^/:"]*/bin' "$(command -v flow-spaces)" | head -n 1 || true)
work=$(mktemp -d "${TMPDIR:-/tmp}/flow-appmenu-smoke.XXXXXX")
# The document portal, which the private bus starts for apps, mounts itself in the runtime directory.
trap 'fusermount3 -u "$work/runtime/doc" 2>/dev/null; for i in 1 2 3 4 5; do rm -rf "$work" 2>/dev/null && break; sleep 1; done' EXIT
mkdir -m 700 "$work/runtime" "$work/bin" "$work/bus-config"
for name in spaces search appmenu; do
    cat > "$work/bin/flow-$name" <<EOF
#!/bin/sh
export PYTHONPATH="$checkout/session" GI_TYPELIB_PATH="$GI_TYPELIB_PATH" PATH="${glib:+$glib:}\$PATH"
exec "$python" -P "$checkout/session/$name.py" "\$@"
EOF
    chmod +x "$work/bin/flow-$name"
done
# The session's own environment, so apps start as they do from Flow (an ssh login has none of it).
eval "$(systemctl --user show-environment \
    | grep -E '^(XDG_SESSION_TYPE|XDG_CURRENT_DESKTOP|DISPLAY|NIXOS_OZONE_WL|WAYLAND_DISPLAY|NIRI_SOCKET)=' | sed 's/^/export /')"
case "${WAYLAND_DISPLAY:-wayland-1}" in
    /*) ;;
    *) WAYLAND_DISPLAY="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/${WAYLAND_DISPLAY:-wayland-1}" ;;
esac
# A session started before at-spi2-core was enabled exports these, and they switch GTK's accessibility off.
unset NO_AT_BRIDGE GTK_A11Y AT_SPI_BUS_ADDRESS DBUS_STARTER_ADDRESS DBUS_STARTER_BUS_TYPE
export WAYLAND_DISPLAY GI_TYPELIB_PATH PATH="$work/bin:$PATH" XDG_RUNTIME_DIR="$work/runtime" \
    XDG_CONFIG_HOME="$work/bus-config" GSETTINGS_BACKEND=keyfile FLOW_APPMENU_SMOKE_BUS=1
user_name="${USER:-$(id -un)}"
eval "$(grep '^export XDG_DATA_DIRS' "$appmenu")"  # where the installed reader finds desktop files
exec 9>"${FLOW_SCREEN_LOCK:-/tmp/floe-screen.lock}"
flock -w 1800 9 || { echo "screen lock busy" >&2; exit 1; }
dbus-run-session -- env FLOW_SHELL="${FLOW_SHELL:-$checkout/session/shell.qml}" \
    "$python" "$checkout/tests/niri_appmenu_smoke.py" "$@"
