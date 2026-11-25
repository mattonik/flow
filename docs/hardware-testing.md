# Hardware tests in the UTM guest

What of Flow's hardware support the UTM test guest (QEMU ARM64 with HVF on an Apple Silicon Mac) can
test, and how. What it cannot test is listed in
[*What needs bare metal*](IMPLEMENTATION_STATUS.md#what-needs-bare-metal-2026-11-22).

The guest's machine configuration (`/etc/floe-test`, not Flow's module) has what these tests need since
generation 31: `iw` and `hostapd` for the simulated Wi-Fi test, `lsusb` (usbutils), the redistributable
firmware that USB Wi-Fi and Bluetooth dongles load, BlueZ, and DisplayLink's `evdi` kernel module.
DisplayLink's driver itself waits for you to download it (see [DisplayLink](#displaylink-dock)).

## Simulated Wi-Fi

`tests/run_wifi_hwsim_smoke.sh` tests Flow's Wi-Fi path against a simulated WPA2 access point, with no
Wi-Fi hardware: the kernel's `mac80211_hwsim` gives the guest two radios, one becomes an access point
(hostapd, in a network namespace of its own) and NetworkManager uses the other. What it checks and how it
stays away from the guest's real network is in the test's docstring
(`tests/niri_wifi_hwsim_smoke.py`). Run it in the guest from a copy of this checkout, holding the screen
lock that the guest's tests share, with the installed shell stopped:

```bash
export XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-1 NIRI_SOCKET=$(ls /run/user/1000/niri*.sock | head -1)
```

```bash
(flock -w 1800 9 || exit 9; was=$(systemctl --user is-active flow-shell); [ "$was" = active ] && systemctl --user stop flow-shell; tests/run_wifi_hwsim_smoke.sh; rc=$?; [ "$was" = active ] && systemctl --user start flow-shell; exit $rc) 9>/tmp/floe-screen.lock
```

It starts the shell again only if it was running. A run stopped with Ctrl-C or `kill` removes its
radios, access point and profiles; one killed with `kill -9` leaves them and its development shell, and
the next run refuses until they are removed:

```bash
python3 tests/niri_wifi_hwsim_smoke.py --cleanup
```

The runner builds this checkout's `flow-wifi-settings` from the module check
(`checks.aarch64-linux.module`), so a change to the wrapper is what gets tested. It builds offline, since
an installed Flow has every input in the store and the guest reaches the binary cache only with `sudo` and
the host's TLS certificate. If a change needs something new from the cache, build the check that way and
name the wrapper:

```bash
sudo -n nix build --no-link --print-out-paths --option ssl-cert-file /etc/floe-test/host-trust.pem "path:$PWD#checks.aarch64-linux.module"
```

```bash
FLOW_WIFI_SETTINGS=$(grep -e '-flow-wifi-settings$' /nix/store/…-flow-module-check/wrappers) tests/run_wifi_hwsim_smoke.sh
```

The test refuses to run while NetworkManager has another Wi-Fi device. A new wireless interface makes
NixOS restart `wpa_supplicant` (a udev rule in `/etc/udev/rules.d/99-local.rules`), which would drop a
passed-through dongle's connection. Unplug the dongle from the guest first.

## USB passthrough

UTM passes host USB devices to a QEMU guest by USB redirection (usbredir). The guest has USB 3 xHCI
controllers; `lsusb -t` shows two root hubs at 480M and two at 5000M.

1. With the VM shut down, open its settings, *Input* → *USB Sharing*: tick *Share USB devices from host*
   and set *USB Support* to *USB 3.0 (XHCI)*. Leave *Maximum Shared USB Devices* at 3 or more.
2. Start the VM, plug the device into the Mac, and choose it from the USB button in the VM window's
   toolbar. macOS loses the device while the guest has it. A device macOS is using (a headset playing
   sound) may refuse to move until macOS lets it go.
3. In the guest, check that it arrived, then run the Flow checks below. To give the device back, untick it
   in the same menu.

GNOME's Wi-Fi panel, Flow's Network row and Flow's volume slider act on whatever NetworkManager and PipeWire offer, so
each device below is a test of the hand-off to those, not of a Flow driver.

### USB Wi-Fi dongle

Pick one with a mainline Linux driver and no out-of-tree module: MediaTek **mt7921u** (Wi-Fi 6, for
example the Netgear A8000 or Alfa AWUS036AXML) or **mt7612u** (Wi-Fi 5, for example the Alfa AWUS036ACM
or Netgear A6210). Both load firmware from linux-firmware.

Arrived:

```bash
lsusb
```

```bash
sudo dmesg | grep -iE 'mt79|mt76|firmware' | tail
```

```bash
iw dev && nmcli device
```

A `wifi` device that NetworkManager lists as *disconnected* (not *unavailable*) is ready.

Flow checks:
- Quick settings → *Network* opens GNOME's Wi-Fi panel, which lists the networks around the Mac.
- Connecting to a protected network from that panel asks for its password; with a wrong one, nm-applet
  (the session's secret agent) asks again; with the right one, the panel shows it as connected.
- Disconnecting from the panel, and turning Wi-Fi off and on, update the panel.
- The *Network* row shows overall connectivity, which the guest's wired connection decides while it is
  up. To see the row follow Wi-Fi, disconnect *Wired connection 1* from the UTM window, not over SSH: the
  SSH port forward runs over that connection.
- Don't run the simulated Wi-Fi test while the dongle is in the guest (see above).

### USB Bluetooth dongle

Any dongle with a mainline driver (`btusb`), for example one with an Intel, Realtek or Broadcom chip.
macOS uses the Mac's own Bluetooth, so a separate dongle is what reaches the guest.

Arrived:

```bash
lsusb && sudo dmesg | grep -i bluetooth | tail
```

```bash
bluetoothctl show
```

Flow checks: Flow has no Bluetooth control of its own yet; Quick settings has no Bluetooth row. What can
be checked is the path a user would take today:
- GNOME's Bluetooth panel (`XDG_CURRENT_DESKTOP=GNOME gnome-control-center bluetooth`, as
  `flow-wifi-settings` starts the Wi-Fi panel) finds and pairs a device.
- A paired Bluetooth headset becomes a PipeWire sink (`wpctl status`), and Quick settings' *Sound*
  slider and *Mute* then act on it (`wpctl get-volume @DEFAULT_AUDIO_SINK@`).

### USB headset

Any USB Audio Class headset; no driver beyond `snd-usb-audio`.

Arrived:

```bash
lsusb && cat /proc/asound/cards
```

```bash
wpctl status
```

Flow checks:
- With the headset as the default sink (`wpctl set-default <id>`), Quick settings' *Sound* slider changes
  the headset's volume and *Mute* mutes it (`wpctl get-volume @DEFAULT_AUDIO_SINK@`).
- Unplugging it from the guest moves sound back to the guest's own output, and the slider follows the new
  default sink.
- Media keys on the headset, if it has them, reach the player Quick settings shows.

### DisplayLink dock

A dock or adapter with a DisplayLink chip (DL-3xxx, DL-41xx, DL-5xxx, DL-6xxx or DL-7xxx; `lsusb` shows
vendor `17e9`). DisplayLink sends the picture over USB, which is why it can reach a VM at all. Expect it to
be slow: the picture goes through UTM's USB redirection on top of DisplayLink's own compression, so a
moving image may stutter or the dock may not start. A dock that works there shows that Flow handles a
display arriving and leaving, not how smooth it is.

USB-C alt-mode docks (DisplayPort over the USB-C cable) and Thunderbolt docks can never reach the VM: the
picture is not USB traffic, and UTM passes only USB devices. Their displays belong to macOS.

**Driver.** nixpkgs builds DisplayLink's driver (`displaylink` 6.3.0-48, which has an aarch64 build) only
from the file you download yourself, because its licence does not allow redistribution:
1. On the Mac, open <https://www.synaptics.com/products/displaylink-usb-graphics-software-ubuntu-63>, read
   and accept DisplayLink's licence, and download *DisplayLink USB Graphics Software for Ubuntu 6.3*. The
   file is `DisplayLink USB Graphics Software for Ubuntu6.3-EXE.zip`.
2. Copy it into the guest and add it to the Nix store under the name nixpkgs expects:

   ```bash
   scp -i /Users/mpuskac/Experiments/.floe-test/ssh-key -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -P 41222 ~/Downloads/"DisplayLink USB Graphics Software for Ubuntu6.3-EXE.zip" floe@127.0.0.1:/tmp/displaylink-630.zip
   ```

   The two `-o` options are the ones the test sessions use. They keep the guest out of
   `~/.ssh/known_hosts`, where a key from an earlier install on the same port makes `scp` refuse with
   *REMOTE HOST IDENTIFICATION HAS CHANGED*. The port is UTM's forward to the VM on this Mac, not a remote
   host.

   Then, from the Mac, add it to the guest's store (`nix-prefetch-url` runs in the guest; the Mac has no Nix):

   ```bash
   ssh -i /Users/mpuskac/Experiments/.floe-test/ssh-key -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p 41222 floe@127.0.0.1 nix-prefetch-url --type sha256 file:///tmp/displaylink-630.zip
   ```

   The hash it prints must be `1670rhm39pqkmfy8kawwx3c79w7y6rd1pkhm698hq1i7fmn8asbj`, which is the
   `sha256-cmmFbHUnBgxRMhXOG1o2/vB02Oicq4m8qxPfNCrM4Jg=` that nixpkgs pins. If it differs, Synaptics has
   changed the file; stop there.
3. In the guest's `/etc/floe-test/displaylink.nix`, set `driverDownloaded = true;`, then rebuild:

   ```bash
   cd /tmp && sudo -n nixos-rebuild switch --flake /etc/floe-test#floe-test --option ssl-cert-file /etc/floe-test/host-trust.pem
   ```

That turns on nixpkgs' DisplayLink module, which installs its udev rules and the `dlm` service that runs
`DisplayLinkManager`. Nothing starts `dlm` but the udev rule, when a DisplayLink device arrives: if the
dock was already in the guest during the rebuild, give it back to the Mac and pass it through again (untick
and tick it in UTM's USB menu). The `evdi` module is installed already and loads at boot; until the guest's next
reboot, `modprobe` looks only in the booted generation's modules, so load it with
`sudo modprobe -d /run/current-system/kernel-modules evdi`.

Arrived:

```bash
lsusb | grep -i 17e9 && systemctl status dlm --no-pager
```

```bash
journalctl -u dlm -b --no-pager | tail -20 && ls /sys/class/drm
```

```bash
niri msg outputs
```

A new DRM card (from `evdi`) and a new output in `niri msg outputs` mean the dock's display reached Niri.
It may well not: `evdi` is a second DRM card with no GPU of its own, and in this guest Niri could not drive
outputs on a second, non-GL card (a second UTM display device; see *Two-display journey* in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)). If the card appears but no output does, look for
Niri's errors with `journalctl --user -u niri -b`. That would be a finding about Niri in this guest, not
about the dock.

Flow checks, with the dock's display as a second output:
- The display gets the next number (*External 1*), and its pins are its own (see *Display numbers and
  shortcuts* and *Two-display journey* in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)).
- *Move to* moves a space onto it and back; Super+Alt+digit reaches its pins.
- Unplugging the dock keeps its pins as a disconnected display's, and plugging it in again brings them back.
- The two-display smoke tests (`niri_display_pins_smoke.py`, `niri_move_display_smoke.py`) pass with the
  dock's output as the second display instead of the guest's `Virtual-2`.
