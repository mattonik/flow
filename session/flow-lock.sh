#!/bin/sh
# The lock screen in Floe's theme, following the Desk's appearance switch (Qt's Settings writes
# [General] lightMode=true|false in floe-appearance.ini).
#
# FLOW_SWAYLOCK_BIN is swaylock itself; FLOW_SWAYLOCK_THEMES holds swaylock-dark.conf and
# swaylock-light.conf, generated from the design tokens. The colours live in those files, never on
# the command line: swayidle runs its commands through sh, where a word starting with "#" is a comment.
# A user's own swaylock configuration wins: with one, swaylock reads it as usual.
config_home=${XDG_CONFIG_HOME:-$HOME/.config}
if [ -e "$HOME/.swaylock/config" ] || [ -e "$config_home/swaylock/config" ]; then
    exec "$FLOW_SWAYLOCK_BIN" "$@"
fi
theme=dark
if grep -qx 'lightMode=true' "$config_home/floe-appearance.ini" 2>/dev/null; then
    theme=light
fi
exec "$FLOW_SWAYLOCK_BIN" --config "$FLOW_SWAYLOCK_THEMES/swaylock-$theme.conf" "$@"
