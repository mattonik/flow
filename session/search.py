#!/usr/bin/env python3
"""Search local applications and the current Flow workspace."""
import json
import subprocess
import sys


def entries(snapshot, apps):
    result = []
    for pin in snapshot.get("pins", []):
        if pin.get("available"):
            result.append((f"{pin['label']}  ·  Pinned space", ("pin", pin["id"])))
    for window in snapshot.get("windows", []):
        result.append((f"{window['label']}  ·  Open window  ·  {window['app_id']}", ("window", str(window["id"]))))
    for app in sorted(apps, key=lambda a: a.get_display_name().casefold()):
        if app.should_show():
            result.append((f"{app.get_display_name()}  ·  Application", ("app", app)))
    return [(" ".join(label.split()), action) for label, action in result]


def main():
    from gi.repository import Gio
    try:
        snapshot = json.loads(subprocess.check_output(["flow-spaces", "refresh"], text=True))
    except (OSError, ValueError, subprocess.SubprocessError):
        snapshot = {}
    choices = entries(snapshot, Gio.AppInfo.get_all())
    result = subprocess.run(["fuzzel", "--dmenu", "--index", "--only-match", "--prompt", "Search: "],
                            input="".join(label + "\n" for label, _ in choices), text=True, capture_output=True)
    if result.returncode != 0:
        return
    try:
        index = int(result.stdout.strip())
        if not 0 <= index < len(choices):
            return
        kind, value = choices[index][1]
        if kind == "app":
            value.launch([], None)
        else:
            subprocess.run(["flow-spaces", "focus" if kind == "pin" else "focus-window", value], check=True)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"Flow search: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
