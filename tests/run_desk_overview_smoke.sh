#!/usr/bin/env bash
# Run tests/niri_desk_overview_smoke.py in the guest, as the user, from a copy of the checkout: under the screen lock,
# with the installed shell stopped and started again afterwards, whatever happened. The real Flow state, settings,
# appearance, recovery markers and Niri include links are checksummed before and after, and must be the same.
# Arguments go to the smoke (for example --part desk). The second display is switched on and off by the smoke itself.
set -u
cd "$(dirname "$0")/.."
# FLOW_SCREEN_LOCK_HELD=1: the caller already holds the lock in another process.
if [ -z "${FLOW_SCREEN_LOCK_HELD:-}" ]; then
    exec 9>/tmp/floe-screen.lock
    flock -w 1800 9 || { echo "The screen lock is busy" >&2; exit 9; }
fi
eval "$(systemctl --user show-environment | grep -E '^(WAYLAND_DISPLAY|NIRI_SOCKET|DBUS_SESSION_BUS_ADDRESS|XDG_RUNTIME_DIR|DISPLAY)=' | sed 's/^/export /')"
sums() {
    find ~/.local/state/flow -type f -print0 2>/dev/null | sort -z | xargs -0 -r sha256sum
    sha256sum ~/.config/flow-settings.ini ~/.config/floe-appearance.ini 2>/dev/null
    for link in ~/.config/flow/niri-*.kdl; do [ -L "$link" ] && echo "$link -> $(readlink "$link")"; done
    for marker in "$XDG_RUNTIME_DIR"/flow-recovery-*; do [ -f "$marker" ] && echo "$marker $(sha256sum < "$marker")"; done
}
# The installed shell keeps Niri's include links in line with the real settings; it does so again when it starts
# afterwards. Doing it first makes the links comparable (an install that added a link has not run the shell yet).
command -v flow-niri-appearance >/dev/null && flow-niri-appearance
before=$(sums)
was_active=$(systemctl --user is-active flow-shell)
systemctl --user stop flow-shell
python3 tests/niri_desk_overview_smoke.py "$@" 9>&-
status=$?
[ "$was_active" = active ] && systemctl --user start flow-shell
sleep 5
after=$(sums)
if [ "$before" != "$after" ]; then
    echo "The real Flow state, settings, appearance, markers or Niri links changed:" >&2
    diff <(echo "$before") <(echo "$after") >&2
    status=1
else
    echo "The real Flow state, settings, appearance, recovery markers and Niri links are unchanged; flow-shell $(systemctl --user is-active flow-shell)"
fi
exit $status
