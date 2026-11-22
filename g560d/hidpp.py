"""Minimal HID++ 2.0 transport over Linux hidraw.

Only feature calls (long report 0x11) are used. HID input reports are
broadcast to every open handle of the hidraw node, so replies are matched on
feature index, function and software id.
"""

from __future__ import annotations

import os
import select
import threading
import time

REPORT_LONG = 0x11
DEVICE_INDEX = 0xFF
LONG_LEN = 20
SW_ID = 0x0A  # arbitrary non-zero software id (0 is reserved for notifications)
ERROR_FEATURE = 0xFF

ERRORS = {
    1: "unknown",
    2: "invalid argument",
    3: "out of range",
    4: "hardware error",
    5: "logitech internal",
    6: "invalid feature index",
    7: "invalid function",
    8: "busy",
    9: "unsupported",
}


class HidppError(Exception):
    def __init__(self, code: int, feature_index: int, function: int):
        self.code = code
        super().__init__(
            f"HID++ error {code} ({ERRORS.get(code, '?')}) on feature index "
            f"{feature_index} fn {function}"
        )


class HidppTimeout(Exception):
    pass


def build_request(feature_index: int, function: int, params: bytes = b"") -> bytes:
    if len(params) > LONG_LEN - 4:
        raise ValueError("params too long for a long report")
    if not 0 <= function <= 0x0F:
        raise ValueError("function must be 0..15")
    head = bytes([REPORT_LONG, DEVICE_INDEX, feature_index, (function << 4) | SW_ID])
    return (head + bytes(params)).ljust(LONG_LEN, b"\0")


def match_reply(data: bytes, feature_index: int, function: int) -> bytes | None:
    """Return the reply payload (bytes after the 4-byte header) if `data` answers
    our request, raise HidppError for a matching error report, else None."""
    if len(data) < 4 or data[0] != REPORT_LONG or data[1] != DEVICE_INDEX:
        return None
    fc = (function << 4) | SW_ID
    if data[2] == feature_index and data[3] == fc:
        return bytes(data[4:])
    if data[2] == ERROR_FEATURE and len(data) >= 6 and data[3] == feature_index and data[4] == fc:
        raise HidppError(data[5], feature_index, function)
    return None


class Hidpp:
    """A HID++ 2.0 device behind a hidraw node."""

    def __init__(self, path: str, timeout: float = 0.8):
        self.path = path
        self.timeout = timeout
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self._lock = threading.Lock()
        self._features: dict[int, int] = {}

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _drain(self) -> None:
        while True:
            try:
                os.read(self.fd, 64)
            except BlockingIOError:
                return

    def request(self, feature_index: int, function: int, params: bytes = b"",
                expect_reply: bool = True) -> bytes | None:
        with self._lock:
            self._drain()
            os.write(self.fd, build_request(feature_index, function, params))
            if not expect_reply:
                return None
            deadline = time.monotonic() + self.timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise HidppTimeout(f"no reply for feature index {feature_index} fn {function}")
                r, _, _ = select.select([self.fd], [], [], left)
                if not r:
                    continue
                try:
                    data = os.read(self.fd, 64)
                except BlockingIOError:
                    continue
                reply = match_reply(data, feature_index, function)
                if reply is not None:
                    return reply

    def feature_index(self, feature_id: int) -> int | None:
        """IRoot.getFeature (feature index 0, fn 0). Cached; None if absent."""
        if feature_id in self._features:
            return self._features[feature_id]
        reply = self.request(0, 0, feature_id.to_bytes(2, "big"))
        idx = reply[0] or None
        if feature_id == 0:
            idx = 0
        if idx is not None:
            self._features[feature_id] = idx
        return idx

    def call(self, feature_id: int, function: int, params: bytes = b"") -> bytes:
        idx = self.feature_index(feature_id)
        if idx is None:
            raise HidppError(9, 0, function)
        return self.request(idx, function, params)

    def protocol_version(self) -> tuple[int, int]:
        r = self.request(0, 1)
        return r[0], r[1]

    def list_features(self) -> list[tuple[int, int]]:
        """[(index, feature_id)] via IFeatureSet (0x0001)."""
        fs = self.feature_index(0x0001)
        count = self.request(fs, 0)[0]
        out = [(0, 0x0000)]
        for i in range(1, count + 1):
            r = self.request(fs, 1, bytes([i]))
            out.append((i, int.from_bytes(r[0:2], "big")))
        return out
