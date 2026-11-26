#!/bin/sh
# Point ~/.config/flow/niri-appearance.kdl, which niri.kdl includes, at Niri's colours for the Desk's
# current appearance, and ~/.config/flow/niri-motion.kdl at Niri's animations for Reduce motion. Run by
# the installed shell at startup and when the appearance or Reduce motion switch changes.
#
# The file is a symlink into the installed shell's directory (FLOW_SHELL_DIR, /etc/flow), so it
# follows upgrades and rollbacks: a generation without the target leaves a dangling link, which
# Niri's optional include treats as missing. The settings are read when the links are set, under a lock,
# so two quick toggles can never finish in the wrong order.
dir=${FLOW_SHELL_DIR:-/etc/flow}
link="$HOME/.config/flow/niri-appearance.kdl"
mkdir -p "${link%/*}" || exit 1
if command -v flock >/dev/null 2>&1; then
    exec 9>"${XDG_RUNTIME_DIR:-${link%/*}}/flow-niri-appearance.lock" && flock 9
fi
settings="${XDG_CONFIG_HOME:-$HOME/.config}/floe-appearance.ini"
mode=dark
if grep -qx 'lightMode=true' "$settings" 2>/dev/null; then
    mode=light
fi
# Reduce motion: Niri's own animations (its overview's zoom, a column scrolling into view, a window opening) are
# off too, through a second include, ~/.config/flow/niri-motion.kdl.
motion=moving
if grep -qx 'reduceMotion=true' "$settings" 2>/dev/null; then
    motion=still
fi
point() {
    [ "$(readlink "$1")" = "$2" ] && return 0
    ln -sfn "$2" "$1.new" && mv -f "$1.new" "$1"
}
point "$link" "$dir/niri-$mode.kdl" && point "${link%/*}/niri-motion.kdl" "$dir/niri-$motion.kdl"
