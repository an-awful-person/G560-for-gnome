import os
import tempfile
import unittest

from g560d import config as cfgmod
from g560d.device import ZoneEffect, eq_decode, eq_encode, led_set_payload
from g560d.hidpp import HidppError, build_request, match_reply


class Framing(unittest.TestCase):
    def test_build_request(self):
        r = build_request(9, 1, b"\x32")
        self.assertEqual(len(r), 20)
        self.assertEqual(r[:5], bytes([0x11, 0xFF, 9, 0x1A, 0x32]))

    def test_match_reply(self):
        data = bytes([0x11, 0xFF, 9, 0x0A, 50]) + bytes(15)
        self.assertEqual(match_reply(data, 9, 0)[0], 50)
        self.assertIsNone(match_reply(data, 9, 1))  # other function
        self.assertIsNone(match_reply(data, 8, 0))  # other feature

    def test_error_reply(self):
        data = bytes([0x11, 0xFF, 0xFF, 6, 0x2A, 2]) + bytes(14)
        with self.assertRaises(HidppError) as cm:
            match_reply(data, 6, 2)
        self.assertEqual(cm.exception.code, 2)


class Leds(unittest.TestCase):
    # Byte layouts below were read back from a real G560 (fw 122.3.23).
    def test_solid(self):
        p = led_set_payload(0, ZoneEffect(effect="solid", color="#ff0000"), False)
        self.assertEqual(p[:5], bytes([0, 1, 0xFF, 0, 0]))
        self.assertEqual(p[12], 0)
        self.assertEqual(len(p), 16)

    def test_cycle_roundtrip(self):
        e = ZoneEffect(effect="cycle", period=3000, brightness=80)
        p = led_set_payload(2, e, True)
        self.assertEqual(p[2:10], bytes([0, 0, 0, 0, 0, 0x0B, 0xB8, 80]))
        self.assertEqual(p[12], 1)
        back = ZoneEffect.from_device(bytes.fromhex("00020000000000" "0bb850") + bytes(6))
        self.assertEqual((back.effect, back.period, back.brightness), ("cycle", 3000, 80))

    def test_breathe_roundtrip(self):
        e = ZoneEffect(effect="breathe", color="#00ff00", period=2000, brightness=70)
        p = led_set_payload(0, e, False)
        self.assertEqual(p[2:9], bytes.fromhex("00ff0007d00046"))
        back = ZoneEffect.from_device(p[:12])
        self.assertEqual(back.to_dict(), e.to_dict())

    def test_factory_cycle_offset(self):
        # Factory value seen on a real unit: period one byte later than written layout.
        back = ZoneEffect.from_device(bytes.fromhex("0202000000000000" "1388") + bytes(4))
        self.assertEqual((back.period, back.brightness), (5000, 100))

    def test_clamps(self):
        p = ZoneEffect(effect="cycle", period=5, brightness=500).params()
        self.assertEqual(int.from_bytes(p[5:7], "big"), 100)
        self.assertEqual(p[7], 100)

    def test_bad_color(self):
        with self.assertRaises(ValueError):
            ZoneEffect(effect="solid", color="red").params()


class Eq(unittest.TestCase):
    def test_signed(self):
        raw = eq_encode([3, -4, 99, -99], -20, 6)
        self.assertEqual(raw, bytes([3, 0xFC, 6, 0xEC]))
        self.assertEqual(eq_decode(raw + bytes(6), 10), [3, -4, 6, -20, 0, 0, 0, 0, 0, 0])

    def test_presets_in_range(self):
        for name, gains in cfgmod.BUILTIN_PRESETS.items():
            self.assertEqual(len(gains), 10, name)
            self.assertTrue(all(-20 <= g <= 6 for g in gains), name)


class RecentColors(unittest.TestCase):
    def test_add_recent(self):
        cfg = {"recent_colors": []}
        self.assertTrue(cfgmod.add_recent(cfg, "3FA0FF"))
        self.assertFalse(cfgmod.add_recent(cfg, "#ff0000"))  # palette color skipped
        for c in ("#111111", "#222", "#333333", "#444444"):
            cfgmod.add_recent(cfg, c)
        self.assertEqual(cfg["recent_colors"], ["#444444", "#333333", "#222222", "#111111"])
        cfgmod.add_recent(cfg, "#222222")  # existing color moves to front, no duplicate
        self.assertEqual(cfg["recent_colors"], ["#222222", "#444444", "#333333", "#111111"])
        self.assertFalse(cfgmod.add_recent(cfg, "#222222"))

    def test_bad_color(self):
        with self.assertRaises(ValueError):
            cfgmod.add_recent({"recent_colors": []}, "blue")


class Config(unittest.TestCase):
    def test_roundtrip_and_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            cfg = cfgmod.load(path)
            self.assertEqual(cfg["subwoofer"], 50)
            cfg["subwoofer"] = 70
            cfg["custom_presets"]["Mine"] = [1] * 10
            cfgmod.save(cfg, path)
            again = cfgmod.load(path)
            self.assertEqual(again["subwoofer"], 70)
            self.assertIn("Mine", cfgmod.presets(again))
            self.assertIn("front-left", again["zones"])


if __name__ == "__main__":
    unittest.main()
