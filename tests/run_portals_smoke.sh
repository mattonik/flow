#!/bin/sh
# F-012 on the installed system (tests/niri_portals_smoke.py): file chooser, screenshots, screen sharing, clipboard
# and drag and drop through the installed portals, in real apps, under the guest's screen lock. Run in the guest, in
# the Niri session's user, from the checkout's root; extra arguments go to the test (--only NAME, --shots DIR).
#
# The apps the guest does not install (Kate, KolourPaint, GNOME Text Editor, xterm, wl-clipboard) are built from the
# nixpkgs of the flake in FLOW_GUEST_FLAKE (default /etc/floe-test), with FLOW_NIX (default "sudo -n nix") and
# FLOW_NIX_ARGS (for the UTM guest: "--option ssl-cert-file /etc/floe-test/host-trust.pem"), into
# ~/flow-f012/apps, garbage-collector roots that stay until removed. PortalProbe needs the
# org.freedesktop.Platform//25.08 runtime installed for the user.
set -eu
flake=${FLOW_GUEST_FLAKE:-/etc/floe-test}
roots=${FLOW_F012_APPS:-$HOME/flow-f012/apps}
mkdir -p "$roots"
for attr in kdePackages.kate kdePackages.kolourpaint gnome-text-editor xterm wl-clipboard; do
    name=${attr##*.}
    [ -e "$roots/$name" ] && continue
    (cd /tmp && ${FLOW_NIX:-sudo -n nix} build --out-link "$roots/$name" --inputs-from "$flake" "nixpkgs#$attr" ${FLOW_NIX_ARGS:-})
done
eval "$(systemctl --user show-environment \
    | grep -E '^(NIRI_SOCKET|WAYLAND_DISPLAY|DISPLAY|XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE|NIXOS_OZONE_WL|PATH)=' | sed 's/^/export /')"
for name in kate kolourpaint gnome-text-editor xterm wl-clipboard; do PATH="$roots/$name/bin:$PATH"; done
export PATH
exec 9>/tmp/floe-screen.lock
flock -w "${FLOW_LOCK_WAIT:-1800}" 9 || { echo "screen lock busy" >&2; exit 1; }
exec python3 tests/niri_portals_smoke.py "$@"
