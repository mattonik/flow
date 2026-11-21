#!/bin/sh
# Runs tests/niri_focus_walk_smoke.py against this checkout's shell, inside a Niri session where Flow is
# installed (its flow-appmenu wrapper names a Python with pyatspi and PyGObject). flow-spaces and
# flow-search run this checkout's session/, from wrappers in a temporary directory. Stop the installed
# shell first (systemctl --user stop flow-shell); the smoke refuses to run beside another shell.
# FLOW_DBUS_CONFIG may name a dbus-daemon configuration for the private bus, e.g. to add a service
# directory on a machine whose generation predates the module's accessibility bus.
set -eu
checkout=$(cd "$(dirname "$0")/.." && pwd)
appmenu=$(command -v flow-appmenu) || { echo "flow-appmenu not found: install Flow" >&2; exit 1; }
for tool in wtype grim foot niri quickshell dbus-run-session; do
    command -v "$tool" >/dev/null || { echo "$tool not found" >&2; exit 1; }
done
eval "$(grep '^export GI_TYPELIB_PATH' "$appmenu")"
python=$(sed -n 's/^exec \([^ ]*python3\) .*/\1/p' "$appmenu")
[ -n "${GI_TYPELIB_PATH:-}" ] && [ -n "$python" ] \
    || { echo "$appmenu names no Python and GI_TYPELIB_PATH: its shape has changed" >&2; exit 1; }
# gio, which the installed wrappers put on PATH for launching apps.
glib=$(grep -o '/nix/store/[^/:"]*-glib-[^/:"]*/bin' "$(command -v flow-spaces)" | head -n 1 || true)
bin=$(mktemp -d "${TMPDIR:-/tmp}/flow-focus-walk-bin.XXXXXX")
trap 'rm -rf "$bin"' EXIT
for name in spaces search; do
    cat > "$bin/flow-$name" <<EOF
#!/bin/sh
export PYTHONPATH="$checkout/session" GI_TYPELIB_PATH="$GI_TYPELIB_PATH" PATH="${glib:+$glib:}\$PATH"
exec "$python" "$checkout/session/$name.py" "\$@"
EOF
    chmod +x "$bin/flow-$name"
done
export PATH="$bin:$PATH" GI_TYPELIB_PATH
dbus-run-session ${FLOW_DBUS_CONFIG:+--config-file="$FLOW_DBUS_CONFIG"} -- \
    env FLOW_SHELL="${FLOW_SHELL:-$checkout/session/shell.qml}" "$python" "$checkout/tests/niri_focus_walk_smoke.py"
