#!/usr/bin/env python3
r"""Flow's Wi-Fi path against a simulated WPA2 access point (mac80211_hwsim), in the test guest.

Flow's own part of Wi-Fi is small: Quick settings' *Network* row shows NetworkManager's connectivity
(``nmcli -t -f CONNECTIVITY general``) and opens ``flow-wifi-settings``, which starts GNOME's Wi-Fi panel.
Choosing a network, passwords and connecting are GNOME's panel, nm-applet (the session's secret agent) and
NetworkManager. So the test checks the shell where the shell does something, and NetworkManager where
Flow hands off:

- the Network row of a development shell shows the label for the connectivity NetworkManager reports;
- a click on it (the pointer, tests/pointer.py) closes Quick settings and opens GNOME's Settings at its
  Wi-Fi panel, which lists the test network under the simulated radio as a WPA2 network (read over AT-SPI);
- through NetworkManager: the network is found by a scan; a profile with the right passphrase connects,
  the access point reports the station and a ping reaches it over the radio, and GNOME's panel shows the
  network as connected; disconnecting ends it on both sides; a profile with a wrong passphrase is refused
  (the access point reports a passphrase mismatch, the radio never connects).

The guest's real network is left alone. The test loads ``mac80211_hwsim`` with two radios (it refuses
when the module is already loaded, or when NetworkManager has any other Wi-Fi device, since a new
wireless interface makes NixOS restart wpa_supplicant), moves one radio into a network namespace of its
own and runs hostapd there, so NetworkManager never sees the access point. Its profiles do not connect
on their own, are bound to the simulated radio, use a /24 the guest does not use and never take the
default route. Everything is removed afterwards, also when the test fails: the profiles, hostapd, the
namespace and the module; the real profiles, devices and default route are compared before and after.

Run it with ``tests/run_wifi_hwsim_smoke.sh`` inside a Niri session where Flow is installed, with ``iw``,
``hostapd``, ``flow-pointer`` and passwordless ``sudo``, and the installed shell stopped (``systemctl
--user stop flow-shell``: its panels sit where the development shell's do). The shell, GNOME's panel and
the AT-SPI bus run on a private session bus; the shell has its own runtime, state and configuration.
``FLOW_WIFI_SHOTS`` may name a directory for screenshots of GNOME's panel.
"""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from pointer import Pointer

# A /24 for the simulated network; the test refuses to run when the guest already uses any of it.
SUBNET = "10.213.77"
AP_ADDRESS, CLIENT_ADDRESS = f"{SUBNET}.1", f"{SUBNET}.2"
# The Network row's words for each of NetworkManager's connectivity states (session/shell.qml).
CONNECTIVITY = {"full": "Connected", "limited": "Limited", "portal": "Sign-in required", "none": "Offline",
                "unknown": "Unknown"}


def nm_fields(line):
    """The fields of one line of ``nmcli -t`` output: nmcli escapes a colon or backslash inside a field
    with a backslash."""
    fields, field, chars = [], "", iter(line)
    for char in chars:
        if char == "\\":
            field += next(chars, "")
        elif char == ":":
            fields.append(field)
            field = ""
        else:
            field += char
    return fields + [field]


def hwsim_radios(sysfs=Path("/sys")):
    """Simulated radios, as {phy: [network interfaces]}, in phy order: mac80211_hwsim's phys sit under
    /sys/devices/virtual/mac80211_hwsim."""
    radios = {}
    root = sysfs / "class/ieee80211"
    for phy in sorted(root.iterdir() if root.is_dir() else [], key=lambda p: int(re.sub(r"\D", "", p.name) or 0)):
        device = (phy / "device").resolve()
        if "mac80211_hwsim" in device.parts:
            net = phy / "device/net"
            radios[phy.name] = sorted(p.name for p in net.iterdir()) if net.is_dir() else []
    return radios


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def wait_for(test, label, timeout=15, pause=0.25):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(pause)
    raise RuntimeError(f"Timed out waiting for {label() if callable(label) else label}")


def run(*command, check=True, timeout=30):
    done = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if check and done.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} exited {done.returncode}: {done.stderr.strip() or done.stdout.strip()}")
    return done


def sudo(*command, check=True, timeout=30):
    return run("sudo", "-n", *command, check=check, timeout=timeout)


def nmcli(*args, check=True, timeout=30):
    return run("nmcli", *args, check=check, timeout=timeout)


def niri(*args):
    return subprocess.check_output(["niri", "msg", *map(str, args)], text=True, timeout=10)


def read(kind):
    return json.loads(niri("-j", kind))


def real_network():
    """What the test must not change: every profile, every device's state and connection, and the default
    routes. Taken before the radios exist and after they are gone."""
    profiles = sorted(tuple(nm_fields(line)[:3]) for line in nmcli("-t", "-f", "UUID,NAME,TYPE", "connection", "show").stdout.splitlines())
    devices = sorted(tuple(nm_fields(line)) for line in nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device").stdout.splitlines())
    routes = run("ip", "-j", "route", "show", "default").stdout
    return {"profiles": profiles, "devices": devices, "default_routes": json.loads(routes or "[]")}


class Lab:
    """The simulated network: two hwsim radios, an access point in its own namespace, test profiles.
    ``close`` removes whatever was made, in reverse, and reports what it could not remove."""

    def __init__(self, tmp, token, iw, hostapd):
        self.tmp, self.token, self.iw, self.hostapd_bin = tmp, token, iw, hostapd
        self.namespace = f"flow-wifi-{token}"
        self.ssid = f"flow-test-{token}"
        self.passphrase = secrets.token_urlsafe(12)
        self.log = tmp / "hostapd.log"
        self.loaded = self.namespaced = False
        self.hostapd = None
        self.profiles = []
        self.client = self.ap = None

    def start(self):
        sudo("modprobe", "mac80211_hwsim", "radios=2")
        self.loaded = True
        radios = wait_for(lambda: (r := hwsim_radios()) and len(r) == 2 and all(r.values()) and r,
                          "two simulated radios with interfaces")
        (client_phy, client_ifs), (ap_phy, ap_ifs) = radios.items()
        self.client, self.ap = client_ifs[0], ap_ifs[0]
        sudo("ip", "netns", "add", self.namespace)
        self.namespaced = True
        sudo(self.iw, "phy", ap_phy, "set", "netns", "name", self.namespace)
        inside = ("ip", "netns", "exec", self.namespace)
        wait_for(lambda: sudo(*inside, "ip", "link", "show", self.ap, check=False).returncode == 0,
                 "the access point's radio in its namespace")
        sudo(*inside, "ip", "link", "set", self.ap, "up")
        sudo(*inside, "ip", "addr", "add", f"{AP_ADDRESS}/24", "dev", self.ap)
        conf = self.tmp / "hostapd.conf"
        conf.write_text("\n".join([
            f"interface={self.ap}", "driver=nl80211", f"ssid={self.ssid}", "hw_mode=g", "channel=6",
            "wpa=2", "wpa_key_mgmt=WPA-PSK", "rsn_pairwise=CCMP", f"wpa_passphrase={self.passphrase}", ""]))
        # nixpkgs' hostapd has no log file option (-f is ignored): its events go to stdout, line by line
        # through stdbuf, as a pipe would otherwise hold them back.
        with open(self.log, "w") as log:
            self.hostapd = subprocess.Popen(["sudo", "-n", *inside, shutil.which("stdbuf"), "-oL", self.hostapd_bin, "-t",
                                             str(conf)], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        wait_for(lambda: "AP-ENABLED" in self.events() or self.hostapd.poll() is not None, "hostapd to start")
        if self.hostapd.poll() is not None:
            raise RuntimeError(f"hostapd exited {self.hostapd.returncode}: {self.events()[-800:]}")

    def events(self):
        try:
            return self.log.read_text(errors="replace")
        except OSError:
            return ""

    def profile(self, label, passphrase):
        ident = str(uuid.uuid4())
        nmcli("connection", "add", "type", "wifi", "ifname", self.client, "con-name", f"flow-wifi-test-{self.token}-{label}",
              "connection.uuid", ident, "ssid", self.ssid, "autoconnect", "no",
              "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", passphrase,
              "ipv4.method", "manual", "ipv4.addresses", f"{CLIENT_ADDRESS}/24", "ipv4.never-default", "yes",
              "ipv6.method", "disabled")
        self.profiles.append(ident)
        return ident

    def close(self):
        problems = []

        def attempt(label, action):
            try:
                action()
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                problems.append(f"{label}: {exc}")

        for ident in reversed(self.profiles):
            attempt(f"delete profile {ident}", lambda: nmcli("connection", "delete", "uuid", ident, check=False))
        if self.hostapd and self.hostapd.poll() is None:
            self.hostapd.terminate()
            try:
                self.hostapd.wait(timeout=10)
            except subprocess.TimeoutExpired:
                problems.append("hostapd did not stop on SIGTERM")
        if self.namespaced:
            # Anything still inside (a hostapd sudo failed to stop) would keep the namespace, and the radio, alive.
            def empty_namespace():
                pids = sudo("ip", "netns", "pids", self.namespace, check=False).stdout.split()
                if pids:
                    sudo("kill", *pids, check=False)
                sudo("ip", "netns", "delete", self.namespace)
            attempt("delete the namespace", empty_namespace)
        if self.loaded:
            def unload():
                # The radio leaves the deleted namespace a moment later; the module is busy until then.
                wait_for(lambda: sudo("modprobe", "-r", "mac80211_hwsim", check=False).returncode == 0,
                         "mac80211_hwsim to unload", timeout=20, pause=0.5)
            attempt("unload mac80211_hwsim", unload)
        leftover = [p for p in nmcli("-t", "-f", "NAME", "connection", "show", check=False).stdout.splitlines()
                    if self.token in p]
        if leftover:
            problems.append(f"test profiles remain: {leftover}")
        return problems


def check(shell, bus, lab, steps, shots):
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Gio, GLib
    import pyatspi

    real_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")

    def device_state():
        return nmcli("-g", "GENERAL.STATE", "device", "show", lab.client, check=False).stdout.strip()

    def scan():
        out = nmcli("-t", "-f", "SSID,SECURITY,SIGNAL", "device", "wifi", "list", "ifname", lab.client, "--rescan", "yes",
                    check=False).stdout
        return next((nm_fields(line) for line in out.splitlines() if nm_fields(line)[0] == lab.ssid), None)

    default_routes = json.loads(run("ip", "-j", "route", "show", "default").stdout or "[]")
    # NetworkManager: the radio, and the network in a scan.
    wait_for(lambda: device_state().startswith("30"), lambda: f"NetworkManager to offer {lab.client} ({device_state()!r})",
             timeout=40, pause=1)
    found = wait_for(scan, f"{lab.ssid} in a scan of {lab.client}", timeout=60, pause=2)
    if "WPA2" not in found[1]:
        raise RuntimeError(f"The scan does not show {lab.ssid} as WPA2: {found}")
    steps.append(f"NetworkManager found {lab.ssid} ({found[1]}, signal {found[2]}) on the simulated radio {lab.client}")

    with tempfile.TemporaryDirectory(prefix="flow-wifi-shell-") as tmp:
        runtime = Path(tmp) / "runtime"
        runtime.mkdir(mode=0o700)
        env = dict(os.environ, XDG_STATE_HOME=tmp, XDG_CONFIG_HOME=str(Path(tmp) / "config"), XDG_RUNTIME_DIR=str(runtime),
                   WAYLAND_DISPLAY=wayland if wayland.startswith("/") else str(real_runtime / wayland))

        def ipc(*call):
            try:
                return subprocess.run(["quickshell", "ipc", "-p", shell, "call", "desk", *call], env=env, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10).stdout.strip()
            except subprocess.SubprocessError:
                return None

        def surfaces():
            try:
                return json.loads(ipc("surfaces") or "{}")
            except ValueError:
                return {}

        def network_row():
            try:
                found = json.loads(ipc("controls") or "[]")
            except ValueError:
                return None
            return next((c for c in found if c["surface"] == "quick" and c["kind"] == "row" and c["text"] == "Network"), None)

        def target(name):
            try:
                return json.loads(ipc("pointer_target", name) or "null")
            except ValueError:
                return None

        def screenshot(name):
            if shots:
                subprocess.run(["grim", str(shots / name)], check=True, timeout=20)

        session_bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        session_bus.call_sync("org.a11y.Bus", "/org/a11y/bus", "org.freedesktop.DBus.Properties", "Set",
                              GLib.Variant("(ssv)", ("org.a11y.Status", "IsEnabled", GLib.Variant("b", True))),
                              None, Gio.DBusCallFlags.NONE, 5000, None)
        pointer = Pointer()
        original = next(w for w in read("workspaces") if w["is_focused"])
        shell_process = settings_window = None
        try:
            # The focused output's empty workspace, so GNOME's window opens where nothing else is.
            niri("action", "focus-workspace", max((w for w in read("workspaces") if w["output"] == original["output"]
                                                   and w["active_window_id"] is None), key=lambda w: w["idx"])["idx"])
            shell_process = subprocess.Popen(["quickshell", "-p", shell], env=env, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for(lambda: ipc("desk_state") == "closed", "the development shell", timeout=20)

            # The shell: the Network row's words follow NetworkManager's connectivity.
            ipc("quick_open")
            wait_for(lambda: surfaces().get("quick"), "Quick settings to open")
            connectivity = nmcli("-t", "-f", "CONNECTIVITY", "general").stdout.strip()
            label = CONNECTIVITY.get(connectivity, "Network unavailable")
            wait_for(lambda: (r := network_row()) and label in r["words"],
                           lambda: f"the Network row to show {label!r} for connectivity {connectivity!r} ({network_row()})")
            steps.append(f"Quick settings' Network row shows {label!r} for NetworkManager's connectivity {connectivity!r} "
                         "(the guest's wired connection decides it; the test leaves that alone)")

            # The hand-off: a click on the row opens GNOME's Wi-Fi panel, which lists the network.
            before = {w["id"] for w in read("windows")}
            pointer.click(pointer.hover(lambda: target("quick_network"), "Quick settings' Network row"))
            wait_for(lambda: not surfaces().get("quick"), "the click to close Quick settings")
            settings_window = wait_for(lambda: next((w for w in read("windows") if w["id"] not in before
                                                     and w.get("app_id") == "org.gnome.Settings"), None),
                                       "the Network row to open GNOME's Settings", timeout=30)
            pointer.park()

            def settings_app():
                desktop = pyatspi.Registry.getDesktop(0)
                for i in range(desktop.childCount):
                    app = desktop.getChildAtIndex(i)
                    if app is not None and app.get_process_id() == settings_window["pid"]:
                        return app
                return None

            def find(node, test, depth=0):
                if node is None or depth > 40:
                    return None
                if test(node):
                    return node
                for i in range(node.childCount):
                    hit = find(node.getChildAtIndex(i), test, depth + 1)
                    if hit is not None:
                        return hit
                return None

            def listed():
                """The test network's row in GNOME's Wi-Fi panel, under the simulated radio, as
                (row, connected), or None while the panel does not show it."""
                app = settings_app()
                if app is None:
                    return None
                app.clear_cache()
                wifi = find(app, lambda n: n.getRole() == pyatspi.ROLE_GROUPING and n.name == "Wi-Fi"
                            and n.getState().contains(pyatspi.STATE_SHOWING))
                radio = find(wifi, lambda n: n.getRole() == pyatspi.ROLE_PANEL and n.name == lab.client)
                row = find(radio, lambda n: n.getRole() == pyatspi.ROLE_LIST_ITEM
                           and (n.name == lab.ssid or n.name.endswith(f"(SSID: {lab.ssid})")))
                if row is None:
                    return None
                secure = find(row, lambda n: n.getRole() == pyatspi.ROLE_IMAGE and "WPA2" in n.name)
                connected = find(row, lambda n: n.getRole() == pyatspi.ROLE_LABEL and n.name == "Connected")
                return row, secure is not None, connected is not None

            row, secure, connected = wait_for(listed, f"GNOME's Wi-Fi panel to list {lab.ssid} under {lab.client}", timeout=45)
            if not secure or connected:
                raise RuntimeError(f"GNOME's Wi-Fi panel shows {lab.ssid} as secure={secure}, connected={connected}")
            steps.append(f"A click on the row closed Quick settings and opened GNOME's Wi-Fi panel, which lists {lab.ssid} "
                         f"under {lab.client} as a WPA2 network ({row.description})")
            screenshot("1-gnome-wifi-panel.png")

            # NetworkManager: the right passphrase connects, over the radio.
            good = lab.profile("good", lab.passphrase)
            nmcli("--wait", "30", "connection", "up", "uuid", good, timeout=40)
            if not device_state().startswith("100"):
                raise RuntimeError(f"{lab.client} is {device_state()!r} after connecting")
            mac = Path(f"/sys/class/net/{lab.client}/address").read_text().strip()
            wait_for(lambda: f"AP-STA-CONNECTED {mac}" in lab.events(), f"the access point to report {mac}")
            ping = run("ping", "-c", "3", "-W", "2", "-I", lab.client, AP_ADDRESS, check=False)
            if ping.returncode != 0:
                raise RuntimeError(f"No ping reached the access point over {lab.client}: {ping.stdout[-300:]}")
            if json.loads(run("ip", "-j", "route", "show", "default").stdout or "[]") != default_routes:
                raise RuntimeError("Connecting the simulated radio changed the default route")
            wait_for(lambda: (r := listed()) and r[2], f"GNOME's Wi-Fi panel to show {lab.ssid} as connected")
            steps.append(f"The right passphrase connected {lab.client} ({mac}): the access point reported it, a ping "
                         f"reached {AP_ADDRESS} over the radio, and GNOME's panel shows the network as connected")
            screenshot("2-gnome-wifi-panel-connected.png")

            # Disconnecting ends it on both sides.
            nmcli("connection", "down", "uuid", good)
            wait_for(lambda: device_state().startswith("30"), lambda: f"{lab.client} to disconnect ({device_state()!r})")
            wait_for(lambda: f"AP-STA-DISCONNECTED {mac}" in lab.events(), "the access point to see the station leave")
            wait_for(lambda: (r := listed()) and not r[2], f"GNOME's Wi-Fi panel to show {lab.ssid} as not connected")
            steps.append("Disconnecting ended the connection: the access point saw the station leave and GNOME's panel "
                         "no longer shows the network as connected")

            # A wrong passphrase is refused.
            joined = lab.events().count("AP-STA-CONNECTED")
            others = {w["id"] for w in read("windows")}
            wrong = lab.profile("wrong", lab.passphrase[::-1] + "x")
            refused = nmcli("--wait", "20", "connection", "up", "uuid", wrong, check=False, timeout=30)
            state = device_state()
            nmcli("device", "disconnect", lab.client, check=False)
            if refused.returncode == 0 or state.startswith("100"):
                raise RuntimeError(f"A wrong passphrase connected {lab.client} ({state!r})")
            if "AP-STA-POSSIBLE-PSK-MISMATCH" not in lab.events():
                raise RuntimeError(f"The access point never saw the wrong passphrase: {lab.events()[-800:]}")
            if lab.events().count("AP-STA-CONNECTED") != joined:
                raise RuntimeError("The access point accepted the station with a wrong passphrase")
            prompts = [w for w in read("windows") if w["id"] not in others]
            for w in prompts:
                niri("action", "close-window", "--id", w["id"])
            if prompts:
                raise RuntimeError(f"A wrong passphrase from nmcli opened windows in the session: "
                                   f"{[(w['app_id'], w['title']) for w in prompts]}")
            steps.append(f"A wrong passphrase was refused: the access point reported a passphrase mismatch and "
                         f"{lab.client} never connected ({state})")
        finally:
            if settings_window:
                subprocess.run(["niri", "msg", "action", "close-window", "--id", str(settings_window["id"])],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if shell_process and shell_process.poll() is None:
                shell_process.terminate()
                try:
                    shell_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    shell_process.kill()
            try:
                niri("action", "focus-workspace", original["idx"])
            except (OSError, subprocess.SubprocessError):
                pass


def main():
    shell = os.environ.get("FLOW_SHELL")
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not shell or "unix:" not in bus:
        raise RuntimeError("Run under dbus-run-session with FLOW_SHELL set (tests/run_wifi_hwsim_smoke.sh)")
    if os.geteuid() == 0:
        raise RuntimeError("Run as the session's user; the test uses sudo -n where it needs root")
    if run("sudo", "-n", "true", check=False).returncode != 0:
        raise RuntimeError("The test needs passwordless sudo (modprobe, ip netns, hostapd)")
    tools = {name: shutil.which(name) for name in ("iw", "hostapd", "stdbuf", "nmcli", "flow-wifi-settings", "flow-pointer", "grim")}
    missing = [name for name, path in tools.items() if not path]
    if missing:
        raise RuntimeError(f"Not on PATH: {', '.join(missing)}")
    if Path("/sys/module/mac80211_hwsim").exists():
        raise RuntimeError("mac80211_hwsim is already loaded (another run, or someone's radios): unload it first")
    wifi = [nm_fields(line)[0] for line in nmcli("-t", "-f", "DEVICE,TYPE", "device").stdout.splitlines()
            if nm_fields(line)[1] == "wifi"]
    if wifi:
        raise RuntimeError(f"NetworkManager has a Wi-Fi device ({', '.join(wifi)}). A new wireless interface makes "
                           "NixOS restart wpa_supplicant, which would drop its connection: run without it")
    if f"{SUBNET}." in run("ip", "-4", "addr").stdout + run("ip", "-4", "route").stdout:
        raise RuntimeError(f"The guest already uses {SUBNET}.0/24, the test's simulated network")
    others = subprocess.run(["pgrep", "-a", "quickshell"], text=True, stdout=subprocess.PIPE).stdout.strip()
    if others:
        raise RuntimeError(f"Another shell is running; stop it (systemctl --user stop flow-shell) first: {others}")
    shots = Path(os.environ["FLOW_WIFI_SHOTS"]) if os.environ.get("FLOW_WIFI_SHOTS") else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    # GNOME's Settings is started by the shell and leaves it; as a subreaper the test still owns it.
    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    real_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "flow"
    real_files = [real_state / name for name in ("spaces.json", "groups.json", "session.json")]
    real_digests = [digest(path) for path in real_files]
    steps, problems, error = [], [], None
    with tempfile.TemporaryDirectory(prefix="flow-wifi-") as tmp:
        lab = Lab(Path(tmp), uuid.uuid4().hex[:6], tools["iw"], tools["hostapd"])
        network = real_network()
        try:
            lab.start()
            check(shell, bus, lab, steps, shots)
        except (OSError, RuntimeError, StopIteration, subprocess.SubprocessError, ValueError, KeyError) as exc:
            error = exc
        finally:
            problems = lab.close()
            if error and lab.events():
                print("hostapd's log:\n" + lab.events()[-2000:], file=sys.stderr)
        # NetworkManager drops the radio's devices a moment after the module goes.
        try:
            wait_for(lambda: real_network() == network, "NetworkManager's devices to match the start", timeout=20)
        except RuntimeError:
            problems.append(f"The real network changed: before {network}, after {real_network()}")
    if [digest(path) for path in real_files] != real_digests:
        problems.append("A real Flow Group, pin, or session file changed")
    if error or problems:
        raise RuntimeError("\n".join(([str(error)] if error else []) + [f"Clean-up: {p}" for p in problems]))
    print(json.dumps({"result": "pass", "steps": steps}, indent=1))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Wi-Fi test failed: {exc}", file=sys.stderr)
        sys.exit(1)
