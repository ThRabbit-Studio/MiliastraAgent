"""qx2d.easing —— 缓动函数表（关键帧插值用）。

可用名称（大小写不敏感，也可写成 easeIn/easeOut/easeInOut + 类型）：
    Linear, Step,
    InSine / OutSine / InOutSine,
    InQuad / OutQuad / InOutQuad,
    InCubic / OutCubic / InOutCubic,
    InQuart / OutQuart / InOutQuart,
    InQuint / OutQuint / InOutQuint,
    InExpo / OutExpo / InOutExpo,
    InCirc / OutCirc / InOutCirc,
    InBack / OutBack / InOutBack,
    InElastic / OutElastic / InOutElastic,
    InBounce / OutBounce / InOutBounce
"""

from __future__ import annotations

import math
from typing import List

_SIMPLE = [
    ("Sine", 1),
    ("Quad", 2),
    ("Cubic", 3),
    ("Quart", 4),
    ("Quint", 5),
]


def _out_bounce(t: float) -> float:
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def ease(name: str, t: float) -> float:
    """把 0..1 的进度映射成缓动后的进度。"""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    key = canonical(name)
    if key == "Linear":
        return t
    if key == "Step":
        return 0.0
    inverse = 1.0 - t
    mode = key[:5] if key.startswith("InOut") else key[:2] if key.startswith("In") else "Out"
    kind = key[5:] if key.startswith("InOut") else key[2:] if key.startswith("In") else key[3:]
    if kind == "Sine":
        if mode == "In":
            return 1 - math.cos(t * math.pi / 2)
        if mode == "Out":
            return math.sin(t * math.pi / 2)
        return -(math.cos(math.pi * t) - 1) / 2
    for suffix, power in _SIMPLE:
        if kind == suffix:
            if mode == "In":
                return t ** power
            if mode == "Out":
                return 1 - inverse ** power
            return (2 ** (power - 1)) * (t ** power) if t < 0.5 else 1 - ((-2 * t + 2) ** power) / 2
    if kind == "Expo":
        if mode == "In":
            return 2 ** (10 * t - 10)
        if mode == "Out":
            return 1 - 2 ** (-10 * t)
        return (2 ** (20 * t - 10)) / 2 if t < 0.5 else (2 - 2 ** (-20 * t + 10)) / 2
    if kind == "Circ":
        if mode == "In":
            return 1 - math.sqrt(1 - t * t)
        if mode == "Out":
            return math.sqrt(1 - (t - 1) ** 2)
        return (1 - math.sqrt(1 - (2 * t) ** 2)) / 2 if t < 0.5 else (math.sqrt(1 - (-2 * t + 2) ** 2) + 1) / 2
    if kind == "Back":
        c1, c2 = 1.70158, 1.70158 * 1.525
        if mode == "In":
            return (c1 + 1) * t ** 3 - c1 * t * t
        if mode == "Out":
            return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2
        return ((2 * t) ** 2 * ((c2 + 1) * 2 * t - c2)) / 2 if t < 0.5 else ((2 * t - 2) ** 2 * ((c2 + 1) * (t * 2 - 2) + c2) + 2) / 2
    if kind == "Elastic":
        period = 2 * math.pi / 3
        if mode == "In":
            return -(2 ** (10 * t - 10)) * math.sin((10 * t - 10.75) * period)
        if mode == "Out":
            return (2 ** (-10 * t)) * math.sin((10 * t - 0.75) * period) + 1
        period2 = 2 * math.pi / 4.5
        if t < 0.5:
            return -((2 ** (20 * t - 10)) * math.sin((20 * t - 11.125) * period2)) / 2
        return (2 ** (-20 * t + 10)) * math.sin((20 * t - 11.125) * period2) / 2 + 1
    if kind == "Bounce":
        if mode == "In":
            return 1 - _out_bounce(1 - t)
        if mode == "Out":
            return _out_bounce(t)
        return (1 - _out_bounce(1 - 2 * t)) / 2 if t < 0.5 else (1 + _out_bounce(2 * t - 1)) / 2
    return t


_ALIASES = {
    "linear": "Linear",
    "step": "Step",
    "none": "Linear",
    "ease": "InOutQuad",
    "easein": "InQuad",
    "easeout": "OutQuad",
    "easeinout": "InOutQuad",
}


def canonical(name: str) -> str:
    raw = (name or "Linear").strip()
    lower = raw.lower().replace("-", "").replace("_", "")
    if lower in _ALIASES:
        return _ALIASES[lower]
    if lower in ("linear", "step"):
        return lower.capitalize()
    for prefix in ("InOut", "In", "Out"):
        if lower.startswith(prefix.lower()):
            kind = lower[len(prefix):]
            for suffix, _ in _SIMPLE + [("Expo", 0), ("Circ", 0), ("Back", 0), ("Elastic", 0), ("Bounce", 0), ("Sine", 0)]:
                if kind == suffix.lower():
                    return prefix + suffix
    for suffix, _ in _SIMPLE + [("Expo", 0), ("Circ", 0), ("Back", 0), ("Elastic", 0), ("Bounce", 0), ("Sine", 0)]:
        if lower == suffix.lower():
            return suffix
    return "Linear"


def known_names() -> List[str]:
    names = ["Linear", "Step"]
    for suffix, _ in _SIMPLE + [("Expo", 0), ("Circ", 0), ("Back", 0), ("Elastic", 0), ("Bounce", 0), ("Sine", 0)]:
        names.extend([suffix, "In" + suffix, "Out" + suffix, "InOut" + suffix])
    return sorted(set(names))
