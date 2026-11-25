#!/bin/sh
# F-003 on the installed system (tests/niri_reboot_pins_smoke.py), run from the host: prepare, reboot the guest,
# check, log out and back in, check, put the real state back. Each phase holds the guest's screen lock.
#
#   FLOW_GUEST="ssh -i KEY -p 41222 floe@127.0.0.1" tests/run_reboot_pins_smoke.sh [SCREENSHOTS_DIR]
#
# FLOW_GUEST reaches the guest as the user of its Niri session. Thunderbird is built in the guest from the
# nixpkgs of the flake in FLOW_GUEST_FLAKE (default /etc/floe-test), with FLOW_NIX_OPTIONS (for the UTM guest:
# "--option ssl-cert-file /etc/floe-test/host-trust.pem"), into ~/flow-f003/thunderbird. The screenshots are
# copied to SCREENSHOTS_DIR when given. Exit 2 means the guest did not come back after the reboot: nothing was
# put back, and the run folder ~/flow-f003/run holds the backup.
set -u
here=$(cd "$(dirname "$0")" && pwd)
: "${FLOW_GUEST:?Set FLOW_GUEST to the command that reaches the guest}"
flake=${FLOW_GUEST_FLAKE:-/etc/floe-test}
guest() { $FLOW_GUEST -o ConnectTimeout=10 "$@"; }
smoke() { guest "python3 ~/flow-f003/niri_reboot_pins_smoke.py --screen-lock /tmp/floe-screen.lock $*"; }
boot() { guest cat /proc/sys/kernel/random/boot_id 2>/dev/null; }

guest 'mkdir -p ~/flow-f003' || exit 1
guest 'cat > ~/flow-f003/niri_reboot_pins_smoke.py' < "$here/niri_reboot_pins_smoke.py" || exit 1
guest "cd /tmp && sudo -n nix build --out-link \$HOME/flow-f003/thunderbird --inputs-from $flake nixpkgs#thunderbird ${FLOW_NIX_OPTIONS:-}" || exit 1

rc=0
if smoke prepare '~/flow-f003/run' --mail '~/flow-f003/thunderbird'; then
    before=$(boot)
    smoke reboot '~/flow-f003/run'  # ssh drops as the guest goes down
    came_back=
    for _ in $(seq 60); do
        sleep 5
        now=$(boot) && [ -n "$now" ] && [ "$now" != "$before" ] && { came_back=1; break; }
    done
    if [ -z "$came_back" ]; then
        echo "The guest did not come back within 5 minutes of the reboot; nothing was put back" >&2
        exit 2
    fi
    if smoke check '~/flow-f003/run' --after reboot && smoke logout '~/flow-f003/run'; then
        smoke check '~/flow-f003/run' --after relogin || rc=1
    else
        rc=1
    fi
else
    rc=1
fi
smoke put-back '~/flow-f003/run' || rc=1
if [ -n "${1:-}" ]; then
    mkdir -p "$1" && guest 'cd ~/flow-f003/run && tar -c *.png' | tar -x -C "$1"
fi
[ $rc = 0 ] && guest 'rm -rf ~/flow-f003/run'
exit $rc
