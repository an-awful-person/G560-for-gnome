"""Persistent desired state: ~/.config/g560/config.json (source of truth)."""

from __future__ import annotations

import copy
import json
import os
import tempfile

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "g560")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# 10 bands: 32 64 125 250 500 1k 2k 4k 8k 16k (dB, device range -20..+6)
BUILTIN_PRESETS = {
    "Flat":          [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "Bass Boost":    [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
    "FPS/Footsteps": [-4, -3, -2, 0, 1, 3, 5, 5, 3, 0],
    "Movie":         [4, 3, 2, 0, -1, 0, 2, 3, 3, 2],
    "Vocal":         [-3, -2, -1, 1, 3, 4, 4, 2, 0, -1],
}

# Fixed colors shown in the top-bar menu (custom picks go to recent_colors).
SWATCHES = ["#ff0000", "#ff6a00", "#ffd000", "#00ff40", "#00d0ff",
            "#0040ff", "#a000ff", "#ff00a0", "#ffffff"]
MAX_RECENT = 4

DEFAULT_ZONE = {"effect": "solid", "color": "#00a0ff", "period": 5000, "brightness": 100}

DEFAULTS = {
    "subwoofer": 50,
    "brightness": 100,
    "zones": {z: dict(DEFAULT_ZONE) for z in ("front-left", "front-right", "rear-left", "rear-right")},
    "link_zones": True,
    "eq_preset": "Flat",
    "eq": [0] * 10,
    "custom_presets": {},
    "recent_colors": [],
    "lights_off_on_lock": True,
    "scroll_step": 5,
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "custom_presets":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str = CONFIG_PATH) -> dict:
    try:
        with open(path) as f:
            return _merge(DEFAULTS, json.load(f))
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULTS)


def save(cfg: dict, path: str = CONFIG_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".config-")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def presets(cfg: dict) -> dict:
    return {**BUILTIN_PRESETS, **cfg.get("custom_presets", {})}


def normalize_color(color: str) -> str:
    v = color.strip().lower().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6 or any(c not in "0123456789abcdef" for c in v):
        raise ValueError(f"bad color {color!r}, use RRGGBB")
    return "#" + v


def add_recent(cfg: dict, color: str) -> bool:
    """Put a custom color at the front of recent_colors. Returns True if changed."""
    c = normalize_color(color)
    if c in SWATCHES:
        return False
    old = list(cfg.get("recent_colors", []))
    new = ([c] + [x for x in old if x != c])[:MAX_RECENT]
    cfg["recent_colors"] = new
    return new != old
