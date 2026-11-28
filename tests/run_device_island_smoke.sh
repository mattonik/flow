#!/bin/sh
# F-012's resting chrome by device (tests/niri_device_island_smoke.py): the edge strip, Super+B, the clock pill,
# the work indicator, the home line, the banner and a simulated notch band, under the guest's screen lock. Run in the guest, in
# the Niri session's user, from the checkout's root; extra arguments go to the test (--only NAME, --shots DIR).
#
# The apps the guest does not install (Kate, KolourPaint, xterm, wl-clipboard, GIMP) are built from the
# nixpkgs of the flake in FLOW_GUEST_FLAKE (default /etc/floe-test), with FLOW_NIX (default "sudo -n nix") and
# FLOW_NIX_ARGS (for the UTM guest: "--option ssl-cert-file /etc/floe-test/host-trust.pem"), into
# ~/flow-f012/apps, garbage-collector roots that stay until removed.
set -eu
flake=${FLOW_GUEST_FLAKE:-/etc/floe-test}
roots=${FLOW_F012_APPS:-$HOME/flow-f012/apps}
mkdir -p "$roots"
for attr in kdePackages.kate kdePackages.kolourpaint xterm wl-clipboard gimp; do
    name=${attr##*.}
    [ -e "$roots/$name" ] && continue
    (cd /tmp && ${FLOW_NIX:-sudo -n nix} build --out-link "$roots/$name" --inputs-from "$flake" "nixpkgs#$attr" ${FLOW_NIX_ARGS:-})
done
eval "$(systemctl --user show-environment \
    | grep -E '^(NIRI_SOCKET|WAYLAND_DISPLAY|DISPLAY|XDG_CURRENT_DESKTOP|XDG_SESSION_TYPE|NIXOS_OZONE_WL|PATH)=' | sed 's/^/export /')"
for name in kate kolourpaint xterm wl-clipboard gimp; do PATH="$roots/$name/bin:$PATH"; done
export PATH
# FLOW_SCREEN_LOCK_HELD: the caller holds the lock already (a second open of the file would wait for it for ever).
if [ -z "${FLOW_SCREEN_LOCK_HELD:-}" ]; then
    exec 9>/tmp/floe-screen.lock
    flock -w "${FLOW_LOCK_WAIT:-1800}" 9 || { echo "screen lock busy" >&2; exit 1; }
fi
exec python3 tests/niri_device_island_smoke.py "$@"
