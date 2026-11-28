#!/usr/bin/env python3
"""Display-scoped pinned-space storage and display identity.

Version 2 of ``spaces.json`` is ``{"version": 2, "displays": {key: record}, "pins": [...]}``.
Each pin carries ``display``: a display key, or ``None`` while it is unassigned. A display's
pin order is the order of its pins in ``pins``; pins of other displays keep their places.

A display key comes from the monitor's make, model and serial when the serial is present and
unique among connected outputs, so a monitor keeps its pins when it moves to another connector.
Without a usable serial, the connector is part of the key: such a monitor on a new connector
is a new display, and its old pins stay with the disconnected record until the user moves them.
An output that reports no EDID at all (no serial, make and model missing or "Unknown") and has no record
of its own is the display last seen on its connector, when exactly one record with an identity was last
seen there and no connected output has that record's make and model; a flaky dock, KVM switch or virtual
GPU can return a known monitor that way. Otherwise it is keyed by port like any other output without a serial.
Legacy pins stored a connector name; they keep it under a ``legacy:`` key until an output with
that connector is seen. Version 1 files are copied to ``spaces.v1.json`` before conversion.
"""
import json
import os


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _known(value):
    text = _text(value)
    return text if text and text.lower() != "unknown" else None


def _has_edid(record):
    return bool(_text(record.get("serial")) or _known(record.get("make")) or _known(record.get("model")))


def identities(outputs, shared=frozenset(), displays=None):
    """Map each active connector to its display record; ambiguous serials fall back to the port.

    ``shared`` holds serial keys once seen on two connected monitors at the same time. They stay keyed
    by port even when only one of the monitors is connected, so unplugging a twin never turns the
    remaining one into a different display.

    ``displays`` are the stored records. An output without EDID and without a record of its own takes the
    key and names of the one record with an identity last seen on its connector whose make and model are not
    connected, so the display keeps its number and pins and no placeholder record is written. With none, or
    more than one, it keeps its port key.
    """
    active = {name: out for name, out in (outputs or {}).items()
              if isinstance(out, dict) and out.get("logical") is not None}
    edid = {}
    for name, out in active.items():
        make, model, serial = _text(out.get("make")), _text(out.get("model")), _text(out.get("serial"))
        if serial:
            edid.setdefault(f"edid:{make or ''}|{model or ''}|{serial}", []).append(name)
    result, ambiguous = {}, set()
    for name, out in active.items():
        make, model, serial = _text(out.get("make")), _text(out.get("model")), _text(out.get("serial"))
        key = f"edid:{make or ''}|{model or ''}|{serial}" if serial else None
        twin = key if key and (len(edid[key]) > 1 or key in shared) else None
        if twin:
            ambiguous.add(twin)
            key = None
        if not key:
            key = f"port:{name}|{make or ''}|{model or ''}"
        label = " ".join(part for part in (make, model) if part and part.lower() != "unknown") or name
        result[name] = {"key": key, "connector": name, "make": make, "model": model,
                        "serial": serial, "label": label}
        if twin:
            result[name]["shared_serial"] = twin
    live = {(record["make"], record["model"]) for record in result.values() if _has_edid(record)}
    for name, record in result.items():
        if _has_edid(record) or record["key"] in (displays or {}):
            continue
        # A record whose make and model are connected may be that monitor on another port: never take it.
        last_here = [key for key, stored in (displays or {}).items()
                     if stored.get("connector") == name and _has_edid(stored)
                     and (_text(stored.get("make")), _text(stored.get("model"))) not in live]
        if len(last_here) == 1:
            stored = displays[last_here[0]]
            result[name] = {"key": last_here[0], "connector": name,
                            **{k: stored.get(k) for k in ("make", "model", "serial")},
                            "label": stored.get("label") or name}
    return result, ambiguous


def read(path):
    """Return (pins, displays, version) without changing anything; reject unknown formats."""
    if not path.exists():
        return [], {}, 2
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        pins, displays, version = data, {}, 1
    elif isinstance(data, dict) and data.get("version") == 2:
        pins, displays, version = data.get("pins"), data.get("displays"), 2
    else:
        raise ValueError("Invalid saved spaces; state was not changed")
    if (not isinstance(pins, list) or not isinstance(displays, dict)
            or any(not isinstance(pin, dict) or not isinstance(pin.get("id"), str) for pin in pins)
            or len({pin["id"] for pin in pins}) != len(pins)
            or any(not isinstance(key, str) or not isinstance(record, dict) for key, record in displays.items())
            or (version == 2 and any(pin.get("display") is not None and pin["display"] not in displays
                                     for pin in pins))):
        raise ValueError("Invalid saved spaces; state was not changed")
    return pins, displays, version


def write(path, pins, displays, version):
    import state_files  # here, not above: flow-spaces runs after every burst of Niri events and mostly only reads
    rollback = path.parent / "spaces.v1.json"
    if version == 1 and path.exists() and not rollback.exists():
        # Keep the original single-display list for rollback before the first conversion. A copy cut short would be
        # kept, as it exists, while the original it stands for is replaced: it is made whole or not at all, first.
        state_files.replace_durably(rollback, path.read_bytes(), like=path)
    data = json.dumps({"version": 2, "displays": displays, "pins": pins}, ensure_ascii=False)
    state_files.replace_durably(path, data.encode(), pause="spaces.write")


def migrate(pins, displays, version, connected, locate):
    """Bring stored displays and pins up to date with the connected outputs, in place.

    ``connected`` maps connector to identity record; ``locate(pin)`` returns the connector whose
    active workspace holds the pin's live window or Group, or None. Nothing is guessed: a pin is
    assigned only when its live window says where it is, or when one display is all there is.
    """
    by_key = {record["key"]: record for record in connected.values()}
    if version == 1:
        for pin in pins:
            name = pin.get("display")
            if isinstance(name, str) and name:
                key = connected[name]["key"] if name in connected else "legacy:" + name
                displays.setdefault(key, {"connector": name, "label": name})
                pin["display"] = key
            else:
                pin["display"] = None
    for key, record in list(displays.items()):
        if key.startswith("legacy:"):
            live = connected.get(record.get("connector"))
            if live:
                # The first output seen on a legacy connector inherits its pins.
                for pin in pins:
                    if pin.get("display") == key:
                        pin["display"] = live["key"]
                del displays[key]
    for key, record in by_key.items():
        stored = displays.setdefault(key, {})
        stored.update({k: record[k] for k in ("connector", "make", "model", "serial", "label", "shared_serial")
                       if k in record})
    for pin in pins:
        if pin.get("display") is None:
            where = locate(pin)
            if where in connected:
                pin["display"] = connected[where]["key"]
            elif len(connected) == 1:
                pin["display"] = next(iter(connected.values()))["key"]
    return displays


def shared_serials(displays):
    """Serial keys that two connected monitors have reported at once."""
    return frozenset(r["shared_serial"] for r in displays.values() if isinstance(r.get("shared_serial"), str))


def status(key, connected, ambiguous):
    if key is None:
        return "unassigned"
    if key in {record["key"] for record in connected.values()}:
        return "connected"
    return "ambiguous" if key in ambiguous else "disconnected"


def move_here(pins, pin_id, key):
    """Move a pin to the end of the given display's pinned spaces."""
    index = next((i for i, pin in enumerate(pins) if pin["id"] == pin_id), None)
    if index is None:
        raise ValueError("Pinned space no longer exists")
    if pins[index].get("display") == key:
        raise ValueError("This space is already pinned on this display")
    pin = pins.pop(index)
    pin["display"] = key
    last = max((i for i, other in enumerate(pins) if other.get("display") == key), default=len(pins) - 1)
    pins.insert(last + 1, pin)


def swap(pins, pin_id, direction, key):
    """Swap a pin with its neighbor on the same display; other displays keep their order."""
    local = [i for i, pin in enumerate(pins) if key is None or pin.get("display") == key]
    position = next((n for n, i in enumerate(local) if pins[i]["id"] == pin_id), None)
    if position is None:
        raise ValueError("Pinned space no longer exists on this display")
    target = position + (-1 if direction == "left" else 1)
    if 0 <= target < len(local):
        a, b = local[position], local[target]
        pins[a], pins[b] = pins[b], pins[a]


INTERNAL_CONNECTORS = ("eDP", "LVDS", "DSI")


def assign_numbers(displays, order):
    """Give displays user-stable numbers in place: 0 is Main, external displays count from 1.

    ``order`` lists the connected display keys in screen order. A number, once given, stays with its
    display record while it is disconnected; only the user changes it (see ``make_main``). The first
    Main is a built-in panel when one is connected, otherwise the first display in screen order.
    """
    seen = set()
    for key in sorted(displays):
        number = displays[key].get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number < 0 or number in seen:
            displays[key].pop("number", None)
        else:
            seen.add(number)
    if 0 not in seen and order:
        internal = [k for k in order if str(displays[k].get("connector", "")).startswith(INTERNAL_CONNECTORS)]
        main = (internal or order)[0]
        seen.discard(displays[main].get("number"))
        displays[main]["number"] = 0
        seen.add(0)
    for key in order:
        if "number" not in displays[key]:
            number = next(n for n in range(1, len(seen) + 2) if n not in seen)
            displays[key]["number"] = number
            seen.add(number)


def display_name(record):
    number = (record or {}).get("number")
    if number == 0:
        return "Main"
    return f"External {number}" if isinstance(number, int) else "Display"


def by_number(displays, number):
    return next((key for key, record in displays.items() if record.get("number") == number), None)


def make_main(displays, key):
    """Make a display Main; the previous Main takes its number."""
    if key not in displays:
        raise ValueError("That display is not known")
    previous = by_number(displays, 0)
    if previous == key:
        raise ValueError("This is already the main display")
    number = displays[key].get("number")
    displays[key]["number"] = 0
    if previous:
        if isinstance(number, int):
            displays[previous]["number"] = number
        else:
            displays[previous].pop("number", None)
