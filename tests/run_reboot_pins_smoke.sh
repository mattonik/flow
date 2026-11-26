#!/bin/sh
# F-003 on the installed system (tests/niri_reboot_pins_smoke.py), run from the host: prepare, reboot the guest,
# check, swap the pins and log out and back in, check, put the real state back.
#
#   FLOW_GUEST="ssh -i KEY -p 41222 floe@127.0.0.1" tests/run_reboot_pins_smoke.sh [SCREENSHOTS_DIR]
#
# FLOW_GUEST reaches the guest as the user of its Niri session. Thunderbird is built in the guest from the
# nixpkgs of the flake in FLOW_GUEST_FLAKE (default /etc/floe-test), with FLOW_NIX_OPTIONS (for the UTM guest:
# "--option ssl-cert-file /etc/floe-test/host-trust.pem"), into ~/flow-f003/thunderbird, a garbage-collector root
# owned by root that stays until removed. The screenshots are copied to SCREENSHOTS_DIR when given.
#
# The guest's screen lock (/tmp/floe-screen.lock) is held from prepare through the reboot, and from the first ssh
# after it to the end. Only the boot itself leaves it free. Exit 2 means the guest did not come back after the
# reboot: nothing was put back, and the run folder ~/flow-f003/run holds the backup. A run that was put back has
# its folder removed, whether or not its checks passed.
set -u
here=$(cd "$(dirname "$0")" && pwd)
: "${FLOW_GUEST:?Set FLOW_GUEST to the command that reaches the guest}"
flake=${FLOW_GUEST_FLAKE:-/etc/floe-test}
guest() { $FLOW_GUEST -o ConnectTimeout=10 "$@"; }
smoke='python3 ~/flow-f003/niri_reboot_pins_smoke.py'
run='~/flow-f003/run'
# Runs a guest command line holding the screen lock; the smoke's phases inherit it.
locked() { guest "exec 9>/tmp/floe-screen.lock; flock -w 1800 9 || exit 9; $1"; }
boot() { guest cat /proc/sys/kernel/random/boot_id 2>/dev/null; }

guest 'mkdir -p ~/flow-f003' || exit 1
guest 'cat > ~/flow-f003/niri_reboot_pins_smoke.py' < "$here/niri_reboot_pins_smoke.py" || exit 1
guest "cd /tmp && sudo -n nix build --out-link \$HOME/flow-f003/thunderbird --inputs-from $flake nixpkgs#thunderbird ${FLOW_NIX_OPTIONS:-}" || exit 1

before=$(boot)
# Prepare, then the reboot, in one hold of the lock. 1 is the smoke's failure (the reboot was not reached, or was
# refused); ssh's own 255, or 0, means the guest went down under it.
locked "$smoke prepare $run --mail ~/flow-f003/thunderbird && $smoke reboot $run"
case $? in
    1|9) rc=1 ;;
    *) rc=0 ;;
esac
if [ $rc = 0 ]; then
    came_back=
    for _ in $(seq 60); do
        sleep 5
        now=$(boot) && [ -n "$now" ] && [ "$now" != "$before" ] && { came_back=1; break; }
    done
    if [ -z "$came_back" ]; then
        echo "The guest did not come back within 5 minutes of the reboot; nothing was put back" >&2
        exit 2
    fi
    locked "$smoke check $run --after reboot && $smoke logout $run --swap-pins && $smoke check $run --after relogin; r=\$?; $smoke put-back $run || r=1; exit \$r"
    rc=$?
else
    locked "$smoke put-back $run"
fi
if [ -n "${1:-}" ]; then
    mkdir -p "$1" && guest 'cd ~/flow-f003/run && tar -c *.png' | tar -x -C "$1"
fi
guest "grep -q '\"phase\": \"put back\"' $run/record.json && rm -rf $run"
exit $rc
