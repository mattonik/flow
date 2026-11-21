#!/bin/sh
# Point ~/.config/flow/niri-appearance.kdl, which niri.kdl includes, at Niri's colours for the Desk's
# current appearance. Run by the installed shell at startup and when the appearance switch changes.
#
# The file is a symlink into the installed shell's directory (FLOW_SHELL_DIR, /etc/flow), so it
# follows upgrades and rollbacks: a generation without the target leaves a dangling link, which
# Niri's optional include treats as missing. The mode is read when the link is set, under a lock,
# so two quick toggles can never finish in the wrong order.
dir=${FLOW_SHELL_DIR:-/etc/flow}
link="$HOME/.config/flow/niri-appearance.kdl"
mkdir -p "${link%/*}" || exit 1
if command -v flock >/dev/null 2>&1; then
    exec 9>"${XDG_RUNTIME_DIR:-${link%/*}}/flow-niri-appearance.lock" && flock 9
fi
mode=dark
if grep -qx 'lightMode=true' "${XDG_CONFIG_HOME:-$HOME/.config}/floe-appearance.ini" 2>/dev/null; then
    mode=light
fi
[ "$(readlink "$link")" = "$dir/niri-$mode.kdl" ] && exit 0
ln -sfn "$dir/niri-$mode.kdl" "$link.new" && mv -f "$link.new" "$link"
