"""Logitech G560 (USB 046d:0a78) over HID++ 2.0.

Safety: this module only issues HID++ *feature* calls. It never touches the
Conexant memory-access / EEPROM interface, which can brick the speaker.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

from .hidpp import Hidpp

VENDOR_ID = 0x046D
PRODUCT_ID = 0x0A78
HIDPP_USAGE_PAGE = b"\x06\x43\xff"  # Usage Page (Vendor 0xFF43) in a report descriptor

F_LED = 0x8070
F_BRIGHTNESS = 0x8040
F_EQ = 0x8310
F_BASS = 0x8305

# Zone numbers on the device.
ZONES = {"rear-left": 0, "rear-right": 1, "front-left": 2, "front-right": 3}
GROUPS = {
    "front": ["front-left", "front-right"],
    "rear": ["rear-left", "rear-right"],
    "all": list(ZONES),
}

# Effect slot index (what setZoneEffect takes) on the G560.
EFFECTS = {"off": 0, "solid": 1, "cycle": 2, "visualizer": 3, "breathe": 4}
EFFECT_NAMES = {v: k for k, v in EFFECTS.items()}

PERIOD_MIN, PERIOD_MAX = 100, 60000
EQ_WRITE_PREFIX = b"\x02"  # TODO(verify): see README "EQ encoding"


def find_hidraw() -> str | None:
    """Path of the G560's HID++ hidraw node, or None."""
    want = f"0003:{VENDOR_ID:08X}:{PRODUCT_ID:08X}"
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(node, "device", "uevent")) as f:
                uevent = f.read()
            if f"HID_ID={want}" not in uevent:
                continue
            with open(os.path.join(node, "device", "report_descriptor"), "rb") as f:
                if HIDPP_USAGE_PAGE not in f.read():
                    continue
        except OSError:
            continue
        return "/dev/" + os.path.basename(node)
    return None


def parse_color(value: str) -> tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        raise ValueError(f"bad color {value!r}, use RRGGBB")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def format_color(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


@dataclass
class ZoneEffect:
    effect: str = "solid"
    color: str = "#00a0ff"
    period: int = 5000  # ms, cycle / breathe
    brightness: int = 100  # 1..100, cycle / breathe
    raw: list[int] = field(default_factory=list)  # visualizer params as read from device

    def params(self) -> bytes:
        eff = self.effect
        rgb = bytes(parse_color(self.color))
        period = max(PERIOD_MIN, min(PERIOD_MAX, int(self.period))).to_bytes(2, "big")
        bright = bytes([max(1, min(100, int(self.brightness)))])
        if eff == "off":
            return b""
        if eff == "solid":
            return rgb
        if eff == "breathe":
            return rgb + period + b"\x00" + bright
        if eff == "cycle":
            return bytes(5) + period + bright
        if eff == "visualizer":
            # Layout not documented; reuse what the speaker reported, or the
            # factory value seen on this unit.
            return bytes(self.raw[:10]) if self.raw else bytes([1, 0, 0, 0, 0, 0x13, 0x88])
        raise ValueError(f"unknown effect {eff!r}")

    @classmethod
    def from_device(cls, payload: bytes) -> "ZoneEffect":
        """Parse getZoneEffect reply: [zone, effect, p0..p9]."""
        eff = EFFECT_NAMES.get(payload[1], "off")
        p = payload[2:12]
        z = cls(effect=eff)
        if eff in ("solid", "breathe"):
            z.color = format_color(p[0:3])
        if eff == "breathe":
            z.period, z.brightness = _sane(p, 3, 6)
        elif eff == "cycle":
            z.period, z.brightness = _sane(p, 5, 7)
        elif eff == "visualizer":
            z.raw = list(p)
        return z

    def to_dict(self) -> dict:
        d = {"effect": self.effect, "color": self.color, "period": self.period,
             "brightness": self.brightness}
        if self.raw:
            d["raw"] = self.raw
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ZoneEffect":
        return cls(effect=d.get("effect", "solid"), color=d.get("color", "#00a0ff"),
                   period=int(d.get("period", 5000)), brightness=int(d.get("brightness", 100)),
                   raw=list(d.get("raw", [])))


def _sane(p: bytes, period_at: int, bright_at: int) -> tuple[int, int]:
    """Read period/brightness; values outside the valid range (e.g. the factory
    cycle on some units, stored one byte later) fall back to sane values."""
    for shift in (0, 1):
        period = int.from_bytes(p[period_at + shift:period_at + shift + 2], "big")
        bright = p[bright_at + shift] if bright_at + shift < len(p) else 0
        if PERIOD_MIN <= period <= PERIOD_MAX:
            return period, bright if 1 <= bright <= 100 else 100
    return 5000, 100


def led_set_payload(zone: int, effect: ZoneEffect, persist: bool) -> bytes:
    body = bytearray(16)
    body[0] = zone
    body[1] = EFFECTS[effect.effect]
    p = effect.params()
    body[2:2 + len(p)] = p
    body[12] = 1 if persist else 0
    return bytes(body)


def eq_encode(gains: list[int], lo: int, hi: int) -> bytes:
    return bytes((max(lo, min(hi, int(g))) & 0xFF) for g in gains)


def eq_decode(raw: bytes, count: int) -> list[int]:
    return [b - 256 if b > 127 else b for b in raw[:count]]


class G560:
    def __init__(self, path: str | None = None):
        path = path or find_hidraw()
        if not path:
            raise FileNotFoundError("Logitech G560 not found")
        self.dev = Hidpp(path)
        self.path = path
        self._eq_info = None

    def close(self):
        self.dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- subwoofer ----------------------------------------------------------
    def get_subwoofer(self) -> int:
        return min(100, self.dev.call(F_BASS, 0)[0])

    def set_subwoofer(self, level: int) -> None:
        self.dev.call(F_BASS, 1, bytes([max(0, min(100, int(level)))]))

    # --- brightness ---------------------------------------------------------
    def get_brightness(self) -> int:
        return int.from_bytes(self.dev.call(F_BRIGHTNESS, 1)[0:2], "big")

    def set_brightness(self, value: int) -> None:
        self.dev.call(F_BRIGHTNESS, 2, max(0, min(100, int(value))).to_bytes(2, "big"))

    # --- LEDs ---------------------------------------------------------------
    def get_zone(self, zone: int, saved: bool = False) -> ZoneEffect:
        return ZoneEffect.from_device(self.dev.call(F_LED, 14, bytes([zone, 1 if saved else 0])))

    def set_zone(self, zone: int, effect: ZoneEffect, persist: bool = False) -> None:
        self.dev.call(F_LED, 3, led_set_payload(zone, effect, persist))

    def get_startup_animation(self) -> bool:
        return self.dev.call(F_LED, 4, b"\x00\x01")[2] == 1

    def set_startup_animation(self, on: bool) -> None:
        self.dev.call(F_LED, 5, bytes([0, 1, 1 if on else 2]))

    # --- equalizer ----------------------------------------------------------
    def eq_info(self) -> dict:
        if self._eq_info is None:
            r = self.dev.call(F_EQ, 0)
            count, db_range = r[0], r[1]
            lo = r[3] - 256 if r[3] > 127 else r[3]
            hi = r[4] - 256 if r[4] > 127 else r[4]
            lo = lo or -db_range
            hi = hi or db_range
            freqs: list[int] = []
            while len(freqs) < count:
                f = self.dev.call(F_EQ, 1, bytes([len(freqs)]))
                chunk = [int.from_bytes(f[1 + 2 * i:3 + 2 * i], "big") for i in range(7)]
                freqs += [x for x in chunk if x][: count - len(freqs)]
                if not any(chunk):
                    break
            self._eq_info = {"count": count, "min": lo, "max": hi, "freqs": freqs}
        return self._eq_info

    def get_eq(self) -> list[int]:
        info = self.eq_info()
        return eq_decode(self.dev.call(F_EQ, 2, b"\x00"), info["count"])

    def set_eq(self, gains: list[int]) -> None:
        info = self.eq_info()
        gains = (list(gains) + [0] * info["count"])[: info["count"]]
        self.dev.call(F_EQ, 3, EQ_WRITE_PREFIX + eq_encode(gains, info["min"], info["max"]))

    # --- summary ------------------------------------------------------------
    def status(self) -> dict:
        return {
            "path": self.path,
            "subwoofer": self.get_subwoofer(),
            "brightness": self.get_brightness(),
            "startup_animation": self.get_startup_animation(),
            "zones": {name: self.get_zone(z).to_dict() for name, z in ZONES.items()},
            "eq": self.get_eq(),
            "eq_info": self.eq_info(),
        }
