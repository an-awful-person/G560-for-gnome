"""g560 command line. Talks to g560d over D-Bus if it runs, else to the speaker directly."""

from __future__ import annotations

import argparse
import json
import sys

from . import config as cfgmod
from .device import EFFECTS, G560, GROUPS, ZONES, ZoneEffect, find_hidraw
from .hidpp import Hidpp

BUS_NAME = "org.g560.Daemon"
OBJ_PATH = "/org/g560/Daemon"


def daemon_proxy():
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owner = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", (BUS_NAME,)), None,
                              Gio.DBusCallFlags.NONE, -1, None)
        if not owner.unpack()[0]:
            return None
        return Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
                                      BUS_NAME, OBJ_PATH, BUS_NAME, None)
    except Exception:  # noqa: BLE001
        return None


def dcall(proxy, method, sig=None, *args):
    from gi.repository import GLib
    v = GLib.Variant(sig, args) if sig else None
    return proxy.call_sync(method, v, 0, 5000, None)


def effect_from_args(a) -> dict:
    d = {"effect": a.effect}
    if a.color:
        d["color"] = a.color
    if a.period:
        d["period"] = a.period
    if a.level:
        d["brightness"] = a.level
    return d


def print_status(st: dict):
    print(f"connected:   {st.get('connected', True)}")
    print(f"subwoofer:   {st['subwoofer']}")
    print(f"brightness:  {st['brightness']}")
    if st.get("startup_animation") is not None:
        print(f"startup:     {'on' if st['startup_animation'] else 'off'}")
    print(f"eq:          {st.get('eq_preset') or 'custom'} {st['eq']}")
    for name, z in st["zones"].items():
        extra = ""
        if z["effect"] in ("solid", "breathe"):
            extra += f" {z['color']}"
        if z["effect"] in ("cycle", "breathe"):
            extra += f" {z['period']}ms {z['brightness']}%"
        print(f"  {name:12} {z['effect']}{extra}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="g560", description="Control a Logitech G560 speaker")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("sub", help="subwoofer level 0-100, or +N/-N")
    s.add_argument("level")
    s = sub.add_parser("bright", help="LED brightness 0-100")
    s.add_argument("level", type=int)
    s = sub.add_parser("led", help="set LED effect")
    s.add_argument("target", choices=list(GROUPS) + list(ZONES))
    s.add_argument("effect", choices=list(EFFECTS))
    s.add_argument("color", nargs="?")
    s.add_argument("--period", type=int, help="ms, for cycle/breathe")
    s.add_argument("--level", type=int, help="effect brightness 1-100, for cycle/breathe")
    s = sub.add_parser("eq", help="EQ preset name, or 10 gains in dB")
    s.add_argument("values", nargs="+")
    sub.add_parser("presets")
    s = sub.add_parser("startup", help="startup animation on/off (stored on speaker)")
    s.add_argument("state", choices=["on", "off"])
    sub.add_parser("save", help="store current LED setup on the speaker")
    sub.add_parser("reapply")
    sub.add_parser("probe", help="list HID++ features (read-only)")
    a = p.parse_args(argv)

    if a.cmd == "probe":
        path = find_hidraw()
        if not path:
            sys.exit("G560 not found")
        with Hidpp(path) as h:
            print(path, "HID++ %d.%d" % h.protocol_version())
            for i, f in h.list_features():
                print(f"  [{i}] 0x{f:04x}")
        return

    proxy = daemon_proxy()
    if proxy:
        run_daemon(proxy, a)
    else:
        run_direct(a)


def eq_gains(values, presets):
    if len(values) == 1 and not values[0].lstrip("+-").isdigit():
        name = values[0]
        match = next((n for n in presets if n.lower() == name.lower()), None)
        if not match:
            sys.exit(f"unknown preset {name!r}; try: {', '.join(presets)}")
        return match, presets[match]
    return None, [int(v) for v in values]


def run_daemon(proxy, a):
    from gi.repository import GLib
    try:
        st = json.loads(dcall(proxy, "GetState").unpack()[0])
        if a.cmd == "status":
            print_status(st)
        elif a.cmd == "sub":
            if a.level[0] in "+-":
                dcall(proxy, "AdjustSubwoofer", "(i)", int(a.level))
            else:
                dcall(proxy, "SetSubwoofer", "(i)", int(a.level))
        elif a.cmd == "bright":
            dcall(proxy, "SetBrightness", "(i)", a.level)
        elif a.cmd == "led":
            dcall(proxy, "SetZone", "(ss)", a.target, json.dumps(effect_from_args(a)))
            if a.color:
                dcall(proxy, "AddRecentColor", "(s)", a.color)
        elif a.cmd == "eq":
            name, gains = eq_gains(a.values, st["presets"])
            if name:
                dcall(proxy, "SetEqPreset", "(s)", name)
            else:
                dcall(proxy, "SetEq", "(ai)", gains)
        elif a.cmd == "presets":
            for n, g in st["presets"].items():
                print(f"{n:16} {g}")
        elif a.cmd == "startup":
            dcall(proxy, "SetStartupAnimation", "(b)", a.state == "on")
        elif a.cmd == "save":
            dcall(proxy, "SaveToSpeaker")
        elif a.cmd == "reapply":
            dcall(proxy, "Reapply")
    except GLib.Error as e:
        sys.exit(f"g560d: {e.message}")


def run_direct(a):
    """No daemon: talk to the speaker directly (and keep the config in sync)."""
    cfg = cfgmod.load()
    try:
        g = G560()
    except (FileNotFoundError, PermissionError) as e:
        sys.exit(str(e))
    with g:
        if a.cmd == "status":
            st = g.status()
            st["eq_preset"] = next((n for n, v in cfgmod.presets(cfg).items() if v == st["eq"]), "")
            print_status(st)
            return
        if a.cmd == "sub":
            lvl = g.get_subwoofer() + int(a.level) if a.level[0] in "+-" else int(a.level)
            cfg["subwoofer"] = max(0, min(100, lvl))
            g.set_subwoofer(cfg["subwoofer"])
        elif a.cmd == "bright":
            cfg["brightness"] = a.level
            g.set_brightness(a.level)
        elif a.cmd == "led":
            names = GROUPS.get(a.target) or [a.target]
            for n in names:
                eff = ZoneEffect.from_dict({**cfg["zones"][n], **effect_from_args(a)})
                g.set_zone(ZONES[n], eff)
                cfg["zones"][n] = eff.to_dict()
            if a.color:
                cfgmod.add_recent(cfg, a.color)
        elif a.cmd == "eq":
            name, gains = eq_gains(a.values, cfgmod.presets(cfg))
            cfg["eq_preset"] = name or ""
            cfg["eq"] = gains
            g.set_eq(gains)
        elif a.cmd == "presets":
            for n, v in cfgmod.presets(cfg).items():
                print(f"{n:16} {v}")
            return
        elif a.cmd == "startup":
            g.set_startup_animation(a.state == "on")
            return
        elif a.cmd == "save":
            for n, z in ZONES.items():
                g.set_zone(z, ZoneEffect.from_dict(cfg["zones"][n]), persist=True)
            return
        elif a.cmd == "reapply":
            g.set_subwoofer(cfg["subwoofer"])
            g.set_brightness(cfg["brightness"])
            g.set_eq(cfg["eq"])
            for n, z in ZONES.items():
                g.set_zone(z, ZoneEffect.from_dict(cfg["zones"][n]))
        cfgmod.save(cfg)


if __name__ == "__main__":
    main()
