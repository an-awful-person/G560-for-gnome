"""g560d: session D-Bus service that owns the G560 and keeps it in the configured state.

- The config file is the source of truth; writes to the speaker are temporary
  (RAM) and are re-applied on start, replug and resume.
- "SaveToSpeaker" writes the current LED setup persistently.
- Lights go off while the screen is locked / during suspend (optional).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys

import gi

gi.require_version("GUdev", "1.0")
from gi.repository import Gio, GLib, GUdev  # noqa: E402

from . import config as cfgmod  # noqa: E402
from .device import G560, GROUPS, ZONES, ZoneEffect, find_hidraw  # noqa: E402

log = logging.getLogger("g560d")

BUS_NAME = "org.g560.Daemon"
OBJ_PATH = "/org/g560/Daemon"

INTROSPECTION = """
<node>
  <interface name="org.g560.Daemon">
    <method name="GetState"><arg type="s" direction="out" name="json"/></method>
    <method name="SetSubwoofer"><arg type="i" direction="in" name="level"/></method>
    <method name="AdjustSubwoofer"><arg type="i" direction="in" name="delta"/></method>
    <method name="SetBrightness"><arg type="i" direction="in" name="level"/></method>
    <method name="SetZone">
      <arg type="s" direction="in" name="target"/>
      <arg type="s" direction="in" name="effect_json"/>
    </method>
    <method name="SetEqPreset"><arg type="s" direction="in" name="name"/></method>
    <method name="SetEq"><arg type="ai" direction="in" name="gains"/></method>
    <method name="SavePreset">
      <arg type="s" direction="in" name="name"/>
      <arg type="ai" direction="in" name="gains"/>
    </method>
    <method name="DeletePreset"><arg type="s" direction="in" name="name"/></method>
    <method name="SetOption">
      <arg type="s" direction="in" name="key"/>
      <arg type="v" direction="in" name="value"/>
    </method>
    <method name="SetStartupAnimation"><arg type="b" direction="in" name="on"/></method>
    <method name="AddRecentColor"><arg type="s" direction="in" name="color"/></method>
    <method name="SaveToSpeaker"/>
    <method name="Reapply"/>
    <signal name="StateChanged"><arg type="s" name="json"/></signal>
  </interface>
</node>
"""

OPTIONS = {"link_zones": bool, "lights_off_on_lock": bool, "scroll_step": int}


class Daemon:
    def __init__(self):
        self.first_run = not os.path.exists(cfgmod.CONFIG_PATH)
        self.cfg = cfgmod.load()
        self.dev: G560 | None = None
        self.locked = False
        self.sleeping = False
        self.startup_animation: bool | None = None
        self.conn: Gio.DBusConnection | None = None
        self.udev = GUdev.Client(subsystems=["hidraw"])
        self.udev.connect("uevent", self._on_uevent)
        self._reconnect_id = 0

    # --- device lifecycle ---------------------------------------------------
    def connect_device(self) -> bool:
        if self.dev:
            return True
        path = find_hidraw()
        if not path:
            return False
        try:
            self.dev = G560(path)
            self.startup_animation = self.dev.get_startup_animation()
            log.info("connected %s", path)
            if self.first_run:
                self.adopt_device_state()
            else:
                self.apply_all()
        except Exception as e:  # noqa: BLE001
            log.warning("could not open G560 at %s: %s", path, e)
            self.drop_device()
            return False
        self.emit_state()
        return True

    def drop_device(self):
        if self.dev:
            try:
                self.dev.close()
            except OSError:
                pass
        self.dev = None

    def _on_uevent(self, _client, action, udev_device):
        if action == "remove" and self.dev and udev_device.get_device_file() == self.dev.path:
            log.info("G560 removed")
            self.drop_device()
            self.emit_state()
        elif action == "add" and not self.dev:
            # Give the speaker a moment to finish enumerating before talking to it.
            if self._reconnect_id:
                GLib.source_remove(self._reconnect_id)
            self._reconnect_id = GLib.timeout_add(800, self._delayed_connect)

    def _delayed_connect(self):
        self._reconnect_id = 0
        self.connect_device()
        return GLib.SOURCE_REMOVE

    def _io(self, fn, *args):
        """Run a device call; on I/O failure, drop the device (replug will reconnect)."""
        if not self.dev:
            return None
        try:
            return fn(*args)
        except OSError as e:
            log.warning("device I/O failed: %s", e)
            self.drop_device()
            self.emit_state()
        except Exception as e:  # noqa: BLE001  (HID++ error / timeout)
            log.warning("device call failed: %s", e)
        return None

    def adopt_device_state(self):
        """First run: take over what the speaker is doing now instead of defaults."""
        d = self.dev
        self.cfg["subwoofer"] = d.get_subwoofer()
        self.cfg["brightness"] = d.get_brightness()
        for name, z in ZONES.items():
            self.cfg["zones"][name] = d.get_zone(z).to_dict()
        self.cfg["eq"] = d.get_eq()
        self.cfg["eq_preset"] = next((n for n, g in cfgmod.BUILTIN_PRESETS.items()
                                      if g == self.cfg["eq"]), "")
        self.first_run = False
        cfgmod.save(self.cfg)
        log.info("adopted current speaker state into %s", cfgmod.CONFIG_PATH)

    def lights_suppressed(self) -> bool:
        return self.sleeping or (self.locked and self.cfg.get("lights_off_on_lock", True))

    def apply_leds(self):
        if not self.dev:
            return
        for name, z in ZONES.items():
            eff = ZoneEffect.from_dict(self.cfg["zones"][name])
            if self.lights_suppressed():
                eff = ZoneEffect(effect="off")
            self._io(self.dev.set_zone, z, eff, False)
        if not self.lights_suppressed():
            self._io(self.dev.set_brightness, self.cfg["brightness"])

    def apply_all(self):
        if not self.dev:
            return
        self._io(self.dev.set_subwoofer, self.cfg["subwoofer"])
        self._io(self.dev.set_eq, self.cfg["eq"])
        self.apply_leds()

    # --- state --------------------------------------------------------------
    def state(self) -> dict:
        return {
            "connected": self.dev is not None,
            "subwoofer": self.cfg["subwoofer"],
            "brightness": self.cfg["brightness"],
            "zones": self.cfg["zones"],
            "link_zones": self.cfg["link_zones"],
            "eq": self.cfg["eq"],
            "eq_preset": self.cfg["eq_preset"],
            "presets": cfgmod.presets(self.cfg),
            "builtin_presets": list(cfgmod.BUILTIN_PRESETS),
            "eq_freqs": [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000],
            "eq_min": -20,
            "eq_max": 6,
            "lights_off_on_lock": self.cfg["lights_off_on_lock"],
            "scroll_step": self.cfg["scroll_step"],
            "startup_animation": self.startup_animation,
            "swatches": cfgmod.SWATCHES,
            "recent_colors": self.cfg["recent_colors"],
        }

    def save_and_emit(self):
        cfgmod.save(self.cfg)
        self.emit_state()

    def emit_state(self):
        if self.conn:
            self.conn.emit_signal(None, OBJ_PATH, BUS_NAME, "StateChanged",
                                  GLib.Variant("(s)", (json.dumps(self.state()),)))

    # --- D-Bus methods ------------------------------------------------------
    def SetSubwoofer(self, level):
        level = max(0, min(100, int(level)))
        self.cfg["subwoofer"] = level
        if self.dev:
            self._io(self.dev.set_subwoofer, level)
        self.save_and_emit()

    def AdjustSubwoofer(self, delta):
        self.SetSubwoofer(self.cfg["subwoofer"] + int(delta))

    def SetBrightness(self, level):
        self.cfg["brightness"] = max(0, min(100, int(level)))
        if self.dev and not self.lights_suppressed():
            self._io(self.dev.set_brightness, self.cfg["brightness"])
        self.save_and_emit()

    def SetZone(self, target, effect_json):
        names = GROUPS.get(target) or ([target] if target in ZONES else None)
        if not names:
            raise ValueError(f"unknown zone/group {target!r}")
        upd = json.loads(effect_json)
        for name in names:
            merged = {**self.cfg["zones"][name], **upd}
            eff = ZoneEffect.from_dict(merged)
            eff.params()  # validate
            self.cfg["zones"][name] = eff.to_dict()
            if self.dev and not self.lights_suppressed():
                self._io(self.dev.set_zone, ZONES[name], eff, False)
        self.save_and_emit()

    def SetEqPreset(self, name):
        p = cfgmod.presets(self.cfg)
        if name not in p:
            raise ValueError(f"unknown preset {name!r}")
        self.cfg["eq_preset"] = name
        self._set_eq(p[name])

    def SetEq(self, gains):
        self.cfg["eq_preset"] = ""
        self._set_eq(gains)

    def _set_eq(self, gains):
        gains = [max(-20, min(6, int(g))) for g in (list(gains) + [0] * 10)[:10]]
        self.cfg["eq"] = gains
        if self.dev:
            self._io(self.dev.set_eq, gains)
        self.save_and_emit()

    def SavePreset(self, name, gains):
        name = name.strip()
        if not name or name in cfgmod.BUILTIN_PRESETS:
            raise ValueError("preset name empty or reserved")
        self.cfg["custom_presets"][name] = [max(-20, min(6, int(g))) for g in gains][:10]
        self.cfg["eq_preset"] = name
        self._set_eq(self.cfg["custom_presets"][name])

    def DeletePreset(self, name):
        self.cfg["custom_presets"].pop(name, None)
        if self.cfg["eq_preset"] == name:
            self.cfg["eq_preset"] = ""
        self.save_and_emit()

    def SetOption(self, key, value):
        if key not in OPTIONS:
            raise ValueError(f"unknown option {key!r}")
        self.cfg[key] = OPTIONS[key](value)
        if key == "lights_off_on_lock":
            self.apply_leds()
        self.save_and_emit()

    def SetStartupAnimation(self, on):
        if not self.dev:
            raise RuntimeError("G560 not connected")
        self.dev.set_startup_animation(bool(on))
        self.startup_animation = self.dev.get_startup_animation()
        self.emit_state()

    def AddRecentColor(self, color):
        if cfgmod.add_recent(self.cfg, color):
            self.save_and_emit()

    def SaveToSpeaker(self):
        if not self.dev:
            raise RuntimeError("G560 not connected")
        for name, z in ZONES.items():
            self.dev.set_zone(z, ZoneEffect.from_dict(self.cfg["zones"][name]), True)
        if self.lights_suppressed():
            self.apply_leds()

    def Reapply(self):
        if self.dev:
            self.apply_all()
        else:
            self.connect_device()

    # --- session events -----------------------------------------------------
    def on_lock(self, active: bool):
        if self.locked == active:
            return
        self.locked = active
        log.info("screen %s", "locked" if active else "unlocked")
        if self.cfg.get("lights_off_on_lock", True):
            self.apply_leds()

    def on_sleep(self, going: bool):
        log.info("prepare for sleep: %s", going)
        self.sleeping = going
        if going:
            self.apply_leds()
        else:
            # The speaker may have lost RAM state; re-apply once it is back.
            GLib.timeout_add(1500, lambda: (self.connect_device() and self.apply_all(), False)[1])


def _handle_call(daemon, conn, sender, path, iface, method, params, invocation):
    try:
        if method == "GetState":
            invocation.return_value(GLib.Variant("(s)", (json.dumps(daemon.state()),)))
            return
        getattr(daemon, method)(*params.unpack())
        invocation.return_value(None)
    except Exception as e:  # noqa: BLE001
        log.warning("%s failed: %s", method, e)
        invocation.return_dbus_error("org.g560.Daemon.Error", str(e))


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    daemon = Daemon()
    loop = GLib.MainLoop()
    node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)

    def on_bus(conn, _name):
        daemon.conn = conn
        handler = lambda *a: _handle_call(daemon, *a)  # noqa: E731
        if hasattr(conn, "register_object_with_closures2"):
            conn.register_object_with_closures2(OBJ_PATH, node.interfaces[0], handler, None, None)
        else:
            conn.register_object(OBJ_PATH, node.interfaces[0], handler, None, None)
        conn.signal_subscribe(None, "org.gnome.ScreenSaver", "ActiveChanged", None, None,
                              Gio.DBusSignalFlags.NONE,
                              lambda *a: daemon.on_lock(a[5].unpack()[0]))
        try:
            sysbus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            sysbus.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager",
                                    "PrepareForSleep", "/org/freedesktop/login1", None,
                                    Gio.DBusSignalFlags.NONE,
                                    lambda *a: daemon.on_sleep(a[5].unpack()[0]))
            daemon._sysbus = sysbus
        except GLib.Error as e:
            log.warning("no system bus: %s", e)

    def on_name(_conn, name):
        log.info("owning %s", name)
        if not daemon.connect_device():
            log.info("G560 not present; waiting for hotplug")

    def on_lost(_conn, name):
        log.error("could not own %s (already running?)", name)
        loop.quit()

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                     on_bus, on_name, on_lost)
    try:
        from gi.repository import GLibUnix
        add_signal = GLibUnix.signal_add
    except ImportError:
        add_signal = GLib.unix_signal_add
    for sig in (signal.SIGINT, signal.SIGTERM):
        add_signal(GLib.PRIORITY_DEFAULT, sig, loop.quit)
    loop.run()
    daemon.drop_device()
    return 0


if __name__ == "__main__":
    sys.exit(main())
