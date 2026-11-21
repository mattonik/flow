#!/bin/sh
# fuzzel in Floe's theme, following the user's appearance setting (the Desk switch, which Qt's
# Settings writes as [General] lightMode=true|false in floe-appearance.ini).
#
# FLOW_FUZZEL_BIN is fuzzel itself; FLOW_FUZZEL_THEMES holds fuzzel-dark.ini and fuzzel-light.ini,
# generated from the design tokens. A user's own ~/.config/fuzzel/fuzzel.ini wins: with one, fuzzel
# starts with its usual configuration search.
config_home=${XDG_CONFIG_HOME:-$HOME/.config}
if [ -e "$config_home/fuzzel/fuzzel.ini" ]; then
    exec "$FLOW_FUZZEL_BIN" "$@"
fi
theme=dark
if grep -qx 'lightMode=true' "$config_home/floe-appearance.ini" 2>/dev/null; then
    theme=light
fi
exec "$FLOW_FUZZEL_BIN" --config="$FLOW_FUZZEL_THEMES/fuzzel-$theme.ini" "$@"
