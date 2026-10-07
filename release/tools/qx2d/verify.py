"""qx2d.verify —— 独立校验。

本模块刻意 **不导入** qx2d.core 的编码/解码函数，也不复用生成器的实现：
变长整数解码、Lua 数据抽取、栅格化都在这里重新实现一遍，用于 round-trip 与逐像素校验。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

# 与生成器相同的字符表（独立重建，不 import core）
ALPHABET = "".join(chr(c) for c in range(33, 127) if chr(c) not in ('"', "\\"))
BASE = len(ALPHABET) // 2
_DIGIT = {ch: i for i, ch in enumerate(ALPHABET)}

RGBA = Tuple[int, int, int, int]


# --------------------------------------------------------------------------
# 独立变长整数解码
# --------------------------------------------------------------------------

def read_uint(text: str, pos: int) -> Tuple[int, int]:
    """返回 (值, 新位置)。字符表基数 46，高 46 个字符为延续位。"""
    value = 0
    multiplier = 1
    while True:
        if pos >= len(text):
            raise ValueError("数据流提前结束（位置 %d）" % pos)
        digit = _DIGIT[text[pos]]
        pos += 1
        continued = digit >= BASE
        if continued:
            digit -= BASE
        value += digit * multiplier
        if not continued:
            return value, pos
        multiplier *= BASE


def read_zigzag(text: str, pos: int) -> Tuple[int, int]:
    raw, pos = read_uint(text, pos)
    return (raw // 2 if raw % 2 == 0 else -(raw + 1) // 2), pos


# --------------------------------------------------------------------------
# 从 Lua 文本抽取数据
# --------------------------------------------------------------------------

def _string_literals(text: str, var: str) -> str:
    """抽取 `local VAR = "..."` 或 `local VAR = "..." .. "..."` 的拼接结果。"""
    pattern = re.compile(r"local\s+%s\s*=\s*((?:\s*\"(?:[^\"\\]|\\.)*\"\s*(?:\.\.\s*)?)+)" % re.escape(var))
    m = pattern.search(text)
    if not m:
        raise ValueError("在 Lua 文本里找不到变量 %s" % var)
    parts = re.findall(r"\"((?:[^\"\\]|\\.)*)\"", m.group(1))
    out = []
    for part in parts:
        out.append(_unescape(part))
    return "".join(out)


def _unescape(s: str) -> str:
    def rep(match):
        body = match.group(1)
        if body.startswith("x"):
            return chr(int(body[1:3], 16))
        if body.isdigit():
            return chr(int(body))
        table = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}
        return table.get(body, body)

    return re.sub(r"\\(x[0-9A-Fa-f]{2}|[0-9]{1,3}|.)", rep, s)


def _table_numbers(text: str, var: str) -> List[float]:
    pattern = re.compile(r"local\s+%s\s*=\s*\{(.*?)\}" % re.escape(var), re.S)
    m = pattern.search(text)
    if not m:
        raise ValueError("在 Lua 文本里找不到表 %s" % var)
    return [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", m.group(1))]


def _scalar_number(text: str, var: str) -> float:
    pattern = re.compile(r"local\s+%s\s*=\s*(-?\d+(?:\.\d+)?)" % re.escape(var))
    m = pattern.search(text)
    if not m:
        raise ValueError("在 Lua 文本里找不到数值 %s" % var)
    return float(m.group(1))


def _palette_from_lua(text: str) -> List[RGBA]:
    pattern = re.compile(r"local\s+PALETTE\s*=\s*\{(.*?)\n\}", re.S)
    m = pattern.search(text)
    if not m:
        raise ValueError("在 Lua 文本里找不到 PALETTE")
    body = m.group(1)
    out: List[RGBA] = []
    for fn, args in re.findall(r"Color\.(FromRGBA|FromRGB)\s*\(([^)]*)\)", body):
        vals = [int(float(v)) for v in re.findall(r"-?\d+(?:\.\d+)?", args)]
        if fn == "FromRGB":
            vals = vals + [255]
        out.append((vals[0], vals[1], vals[2], vals[3]))
    if not out:
        raise ValueError("PALETTE 里没有解析到颜色")
    return out


# --------------------------------------------------------------------------
# 独立栅格
# --------------------------------------------------------------------------

def blank(w: int, h: int) -> bytearray:
    return bytearray(w * h * 4)


def paint(buf: bytearray, w: int, h: int, x: int, y: int, rw: int, rh: int, rgba: RGBA) -> None:
    for yy in range(y, y + rh):
        if yy < 0 or yy >= h:
            continue
        base = yy * w * 4
        for xx in range(x, x + rw):
            if xx < 0 or xx >= w:
                continue
            i = base + xx * 4
            buf[i] = rgba[0]
            buf[i + 1] = rgba[1]
            buf[i + 2] = rgba[2]
            buf[i + 3] = rgba[3]


def compare(a: bytes, b: bytes, w: int, h: int) -> Dict[str, float]:
    """逐像素比较两份 RGBA，返回差异指标。"""
    if len(a) != len(b):
        raise ValueError("两份栅格尺寸不一致")
    diff = 0
    max_err = 0
    total = 0.0
    sq = 0.0
    n = max(1, w * h)
    for p in range(n):
        i = p * 4
        e = 0
        for k in range(4):
            d = abs(a[i + k] - b[i + k])
            if d > e:
                e = d
            total += d
            sq += d * d
        if e:
            diff += 1
            if e > max_err:
                max_err = e
    return {
        "different_pixels": diff,
        "maximum_channel_error": max_err,
        "mae": round(total / (n * 4), 6),
        "rmse": round((sq / (n * 4)) ** 0.5, 6),
    }


# --------------------------------------------------------------------------
# 独立解码：像素画压缩流
# --------------------------------------------------------------------------

def decode_pixel_lua(lua_text: str) -> Dict[str, object]:
    """从像素画压缩 Lua 里恢复调色板与规范化记录（完全独立于生成器）。"""
    palette = _palette_from_lua(lua_text)
    size_w = [int(v) for v in _table_numbers(lua_text, "SIZE_W")]
    size_h = [int(v) for v in _table_numbers(lua_text, "SIZE_H")]
    if len(size_w) != len(size_h):
        raise ValueError("尺寸字典两张表长度不一致")
    stream = _string_literals(lua_text, "ROWS")
    grid_w = int(_scalar_number(lua_text, "GRID_WIDTH"))
    grid_h = int(_scalar_number(lua_text, "GRID_HEIGHT"))

    records: List[Dict[str, int]] = []
    pos = 0
    prev_y = -1
    while pos < len(stream):
        dy, pos = read_zigzag(stream, pos)
        y = prev_y + dy
        prev_y = y
        count, pos = read_uint(stream, pos)
        right = 0
        for _ in range(count):
            gap, pos = read_uint(stream, pos)
            size_index, pos = read_uint(stream, pos)
            color_index, pos = read_uint(stream, pos)
            x = right + gap
            records.append(
                {
                    "kind": 0,
                    "x": x,
                    "y": y,
                    "width": size_w[size_index],
                    "height": size_h[size_index],
                    "angle": 0,
                    "colorIndex": color_index,
                }
            )
            right = x + size_w[size_index]
    return {
        "palette": palette,
        "sizes": list(zip(size_w, size_h)),
        "records": records,
        "grid": (grid_w, grid_h),
        "cursor_at_end": pos == len(stream),
    }


def rasterize_pixel(decoded: Dict[str, object], grid: Optional[Tuple[int, int]] = None) -> Tuple[bytearray, int, int]:
    w, h = grid or decoded["grid"]  # type: ignore[index]
    buf = blank(w, h)
    palette = decoded["palette"]  # type: ignore[index]
    for rec in decoded["records"]:  # type: ignore[index]
        paint(buf, w, h, rec["x"], rec["y"], rec["width"], rec["height"], palette[rec["colorIndex"] - 1])
    return buf, w, h


# --------------------------------------------------------------------------
# 独立解码：帧动画压缩流
# --------------------------------------------------------------------------

def decode_frames_lua(lua_text: str) -> Dict[str, object]:
    palette = _palette_from_lua(lua_text)
    size_w = [int(v) for v in _table_numbers(lua_text, "SIZE_W")]
    size_h = [int(v) for v in _table_numbers(lua_text, "SIZE_H")]
    group_color = [int(v) for v in _table_numbers(lua_text, "GROUP_COLOR")]
    group_pool = [int(v) for v in _table_numbers(lua_text, "GROUP_POOL")]
    group_base = [int(v) for v in _table_numbers(lua_text, "GROUP_BASE")]
    local_w = int(_scalar_number(lua_text, "LOCAL_W"))
    local_h = int(_scalar_number(lua_text, "LOCAL_H"))
    origin_x = int(_scalar_number(lua_text, "ORIGIN_X"))
    origin_y = int(_scalar_number(lua_text, "ORIGIN_Y"))
    frame_count = int(_scalar_number(lua_text, "FRAME_COUNT"))
    stream = _string_literals(lua_text, "FRAME_DATA")
    area = local_w * local_h

    frames: List[List[Dict[str, int]]] = []
    pos = 0
    for _ in range(frame_count):
        slots: List[Dict[str, int]] = []
        for gi, pool in enumerate(group_pool):
            count, pos = read_uint(stream, pos)
            if count > pool:
                raise ValueError("第 %d 组的活跃数量 %d 超过池大小 %d" % (gi, count, pool))
            for i in range(count):
                q, pos = read_uint(stream, pos)
                size_index = q // area
                cell = q % area
                lx = cell % local_w
                ly = cell // local_w
                slots.append(
                    {
                        "slot": group_base[gi] + i,
                        "colorIndex": group_color[gi],
                        "x": lx + origin_x,
                        "y": ly + origin_y,
                        "width": size_w[size_index],
                        "height": size_h[size_index],
                        "visible": 1,
                    }
                )
        frames.append(slots)
    return {
        "palette": palette,
        "sizes": list(zip(size_w, size_h)),
        "groups": list(zip(group_color, group_pool, group_base)),
        "origin": (origin_x, origin_y),
        "local": (local_w, local_h),
        "frames": frames,
        "cursor_at_end": pos == len(stream),
    }


def rasterize_frame(decoded: Dict[str, object], frame_index: int, w: int, h: int) -> bytearray:
    buf = blank(w, h)
    palette = decoded["palette"]  # type: ignore[index]
    for slot in decoded["frames"][frame_index]:  # type: ignore[index]
        paint(
            buf, w, h,
            slot["x"], slot["y"], slot["width"], slot["height"],
            palette[slot["colorIndex"] - 1],
        )
    return buf


# --------------------------------------------------------------------------
# Lua 静态冒烟检查
# --------------------------------------------------------------------------

def lua_static_check(text: str) -> Dict[str, object]:
    """先屏蔽字符串，再去掉注释，最后检查括号配对。

    注意顺序：安全 ASCII 数据里可能含有看似注释或括号的字符。
    这不是 Lua 解析器，只是冒烟检查；运行时验证仍需在目标环境执行。
    """
    issues: List[str] = []
    out = []
    i = 0
    n = len(text)
    in_str = False
    in_comment = False
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if in_comment:
            if ch == "\n":
                in_comment = False
                out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_str = True
            i += 1
            continue
        if ch == "-" and i + 1 < n and text[i + 1] == "-":
            in_comment = True
            i += 2
            continue
        out.append(ch)
        i += 1

    if in_str:
        issues.append("字符串没有闭合")
    code = "".join(out)
    stack: List[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for ch in code:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                issues.append("括号不配对：出现多余的 %s" % ch)
                break
            stack.pop()
    if stack:
        issues.append("有未闭合的括号：%s" % "".join(stack))
    return {
        "bracket_balanced": not issues,
        "issues": issues,
        "characters": len(text),
    }


# --------------------------------------------------------------------------
# 记录级比较
# --------------------------------------------------------------------------

def multiset(records: Sequence[Dict[str, int]]) -> Dict[Tuple[int, ...], int]:
    out: Dict[Tuple[int, ...], int] = {}
    for r in records:
        key = (r["colorIndex"], r["x"], r["y"], r["width"], r["height"])
        out[key] = out.get(key, 0) + 1
    return out


def multiset_diff(a: Sequence[Dict[str, int]], b: Sequence[Dict[str, int]]) -> int:
    ma, mb = multiset(a), multiset(b)
    diff = 0
    for key in set(ma) | set(mb):
        diff += abs(ma.get(key, 0) - mb.get(key, 0))
    return diff


# --------------------------------------------------------------------------
# 独立解码：JSON 数据契约（从磁盘上的 .json 读回来重建）
# --------------------------------------------------------------------------

def decode_pixel_data(payload: Dict[str, object]) -> Dict[str, object]:
    """从 pixel JSON 数据重建调色板与记录；只依赖契约里的字段。"""
    if payload.get("schema") != "qx2d.pixel":
        raise ValueError("不是 qx2d.pixel 数据：%r" % payload.get("schema"))
    palette: List[RGBA] = []
    for item in payload["palette"]:  # type: ignore[index]
        rgba = item["rgba"]  # type: ignore[index]
        if len(rgba) != 4:
            raise ValueError("调色板项不是 RGBA：%r" % (rgba,))
        palette.append((int(rgba[0]), int(rgba[1]), int(rgba[2]), int(rgba[3])))
    canvas = payload["canvas"]  # type: ignore[index]
    records: List[Dict[str, int]] = []
    for rec in payload["records"]:  # type: ignore[index]
        records.append(
            {
                "kind": int(rec["kind"]),
                "x": int(rec["x"]),
                "y": int(rec["y"]),
                "width": int(rec["width"]),
                "height": int(rec["height"]),
                "angle": int(rec.get("angle", 0)),
                "colorIndex": int(rec["colorIndex"]),
            }
        )
        if not (1 <= records[-1]["colorIndex"] <= len(palette)):
            raise ValueError("colorIndex 越界：%d" % records[-1]["colorIndex"])
    return {
        "palette": palette,
        "records": records,
        "grid": (int(canvas["width"]), int(canvas["height"])),
    }


def decode_frames_data(payload: Dict[str, object]) -> Dict[str, object]:
    """从 frames JSON 数据重建逐帧槽位；只依赖契约里的字段。"""
    if payload.get("schema") != "qx2d.frames":
        raise ValueError("不是 qx2d.frames 数据：%r" % payload.get("schema"))
    palette: List[RGBA] = []
    for item in payload["palette"]:  # type: ignore[index]
        rgba = item["rgba"]  # type: ignore[index]
        palette.append((int(rgba[0]), int(rgba[1]), int(rgba[2]), int(rgba[3])))
    sizes = [(int(s[0]), int(s[1])) for s in payload["sizes"]]  # type: ignore[index]
    frames: List[List[Dict[str, int]]] = []
    for frame in payload["timeline"]["frames"]:  # type: ignore[index]
        slots = []
        for slot in frame:
            slots.append(
                {
                    "slot": int(slot["slot"]),
                    "colorIndex": int(slot["colorIndex"]),
                    "x": int(slot["x"]),
                    "y": int(slot["y"]),
                    "width": int(slot["width"]),
                    "height": int(slot["height"]),
                }
            )
        frames.append(slots)
    canvas = payload["canvas"]  # type: ignore[index]
    return {
        "palette": palette,
        "sizes": sizes,
        "frames": frames,
        "origin": (int(canvas["origin_x"]), int(canvas["origin_y"])),
        "local": (int(canvas["local_width"]), int(canvas["local_height"])),
    }


# --------------------------------------------------------------------------
# 独立解码：图元拟合数据契约
# --------------------------------------------------------------------------

def _shape_coverage(kind: int, u: float, v: float, hw: float, hh: float) -> float:
    """局部坐标下的覆盖度（与生成端各自独立实现）。"""
    if kind == 0:  # 矩形
        cu = min(1.0, max(0.0, hw - abs(u) + 0.5))
        cv = min(1.0, max(0.0, hh - abs(v) + 0.5))
        return cu * cv
    if kind == 1:  # 椭圆
        d = ((u / max(hw, 1e-6)) ** 2 + (v / max(hh, 1e-6)) ** 2) ** 0.5
        edge = max(1.0, min(hw, hh))
        return min(1.0, max(0.0, (1.0 - d) * edge + 0.5))
    # 三角形：顶点 (0,-hh)、左下 (-hw,hh)、右下 (hw,hh)
    ax, ay = 0.0, -hh
    bx, by = -hw, hh
    cx, cy = hw, hh

    def edge(px: float, py: float, qx: float, qy: float) -> float:
        return (qx - px) * (v - py) - (qy - py) * (u - px)

    sign = 1.0 if ((cx - bx) * (ay - by) - (cy - by) * (ax - bx)) > 0 else -1.0
    d1 = sign * edge(bx, by, cx, cy) / max(((cx - bx) ** 2 + (cy - by) ** 2) ** 0.5, 1e-6)
    d2 = sign * edge(cx, cy, ax, ay) / max(((ax - cx) ** 2 + (ay - cy) ** 2) ** 0.5, 1e-6)
    d3 = sign * edge(ax, ay, bx, by) / max(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5, 1e-6)
    return min(1.0, max(0.0, d1 + 0.5)) * min(1.0, max(0.0, d2 + 0.5)) * min(1.0, max(0.0, d3 + 0.5))


def rasterize_shapes(payload: Dict[str, object]):
    """从 shapes JSON 独立栅格化出 RGBA（纯 Python 循环实现），返回 numpy uint8 数组。"""
    import math

    import numpy as np

    if payload.get("schema") != "qx2d.shapes":
        raise ValueError("不是 qx2d.shapes 数据：%r" % payload.get("schema"))
    palette = []
    for item in payload["palette"]:  # type: ignore[index]
        rgba = item["rgba"]  # type: ignore[index]
        palette.append((int(rgba[0]), int(rgba[1]), int(rgba[2]), int(rgba[3])))
    canvas = payload["canvas"]  # type: ignore[index]
    width, height = int(canvas["width"]), int(canvas["height"])
    out = np.zeros((height, width, 4), dtype=np.float64)
    for element in payload["elements"]:  # type: ignore[index]
        kind = int(element["kind"])
        cx, cy = float(element["x"]), float(element["y"])
        hw = max(float(element["width"]) / 2.0, 1e-6)
        hh = max(float(element["height"]) / 2.0, 1e-6)
        rot = float(element.get("rotation", 0.0))
        opacity = float(element.get("opacity", 1.0))
        color = palette[int(element["colorIndex"]) - 1]
        theta = math.radians(rot)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        ex = hw * abs(cos_t) + hh * abs(sin_t)
        ey = hw * abs(sin_t) + hh * abs(cos_t)
        x0 = max(0, int(math.floor(cx - ex)) - 1)
        y0 = max(0, int(math.floor(cy - ey)) - 1)
        x1 = min(width, int(math.ceil(cx + ex)) + 2)
        y1 = min(height, int(math.ceil(cy + ey)) + 2)
        rgb = (color[0] / 255.0, color[1] / 255.0, color[2] / 255.0, color[3] / 255.0)
        for py in range(y0, y1):
            dy = py + 0.5 - cy
            for px in range(x0, x1):
                dx = px + 0.5 - cx
                u = dx * cos_t + dy * sin_t
                v = -dx * sin_t + dy * cos_t
                cov = _shape_coverage(kind, u, v, hw, hh) * opacity
                if cov <= 0.0:
                    continue
                inv = 1.0 - cov
                for ch in range(4):
                    out[py, px, ch] = out[py, px, ch] * inv + rgb[ch] * cov
    return (out * 255.0 + 0.5).astype(np.uint8)


# --------------------------------------------------------------------------
# 独立解码：动画数据契约
# --------------------------------------------------------------------------

def _ease_independent(name: str, t: float) -> float:
    """独立实现的缓动表（与生成端各写一份，用于交叉校验）。"""
    import math as _m

    key = (name or "Linear").strip()
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    if key in ("Linear", ""):
        return t
    if key == "Step":
        return 0.0
    inv = 1.0 - t
    if key == "InSine":
        return 1 - _m.cos(t * _m.pi / 2)
    if key == "OutSine":
        return _m.sin(t * _m.pi / 2)
    if key == "InOutSine":
        return -(_m.cos(_m.pi * t) - 1) / 2
    powers = {"Quad": 2, "Cubic": 3, "Quart": 4, "Quint": 5}
    for suffix, power in powers.items():
        if key == "In" + suffix:
            return t ** power
        if key == "Out" + suffix:
            return 1 - inv ** power
        if key == "InOut" + suffix:
            return (2 ** (power - 1)) * (t ** power) if t < 0.5 else 1 - ((-2 * t + 2) ** power) / 2
    if key == "InExpo":
        return 2 ** (10 * t - 10)
    if key == "OutExpo":
        return 1 - 2 ** (-10 * t)
    if key == "InOutExpo":
        return (2 ** (20 * t - 10)) / 2 if t < 0.5 else (2 - 2 ** (-20 * t + 10)) / 2
    if key == "InCirc":
        return 1 - _m.sqrt(1 - t * t)
    if key == "OutCirc":
        return _m.sqrt(1 - (t - 1) ** 2)
    if key == "InOutCirc":
        return (1 - _m.sqrt(1 - (2 * t) ** 2)) / 2 if t < 0.5 else (_m.sqrt(1 - (-2 * t + 2) ** 2) + 1) / 2
    c1, c2 = 1.70158, 1.70158 * 1.525
    if key == "InBack":
        return (c1 + 1) * t ** 3 - c1 * t * t
    if key == "OutBack":
        return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2
    if key == "InOutBack":
        return ((2 * t) ** 2 * ((c2 + 1) * 2 * t - c2)) / 2 if t < 0.5 else ((2 * t - 2) ** 2 * ((c2 + 1) * (t * 2 - 2) + c2) + 2) / 2
    period = 2 * _m.pi / 3
    if key == "InElastic":
        return -(2 ** (10 * t - 10)) * _m.sin((10 * t - 10.75) * period)
    if key == "OutElastic":
        return (2 ** (-10 * t)) * _m.sin((10 * t - 0.75) * period) + 1
    if key == "InOutElastic":
        p2 = 2 * _m.pi / 4.5
        if t < 0.5:
            return -((2 ** (20 * t - 10)) * _m.sin((20 * t - 11.125) * p2)) / 2
        return (2 ** (-20 * t + 10)) * _m.sin((20 * t - 11.125) * p2) / 2 + 1

    def out_bounce(x: float) -> float:
        n1, d1 = 7.5625, 2.75
        if x < 1 / d1:
            return n1 * x * x
        if x < 2 / d1:
            x -= 1.5 / d1
            return n1 * x * x + 0.75
        if x < 2.5 / d1:
            x -= 2.25 / d1
            return n1 * x * x + 0.9375
        x -= 2.625 / d1
        return n1 * x * x + 0.984375

    if key == "InBounce":
        return 1 - out_bounce(1 - t)
    if key == "OutBounce":
        return out_bounce(t)
    if key == "InOutBounce":
        return (1 - out_bounce(1 - 2 * t)) / 2 if t < 0.5 else (1 + out_bounce(2 * t - 1)) / 2
    return t


def _evaluate_key(keys, time_ms: float, base, field: str):
    if not keys:
        return base
    if time_ms <= float(keys[0]["time"]):
        return base if field == "visible" else keys[0]["value"]
    if time_ms >= float(keys[-1]["time"]):
        return keys[-1]["value"]
    for index in range(len(keys) - 1):
        key = keys[index]
        nxt = keys[index + 1]
        if time_ms >= float(nxt["time"]):
            continue
        if key.get("interpolation") == "step" or field in ("visible", "colorIndex"):
            return key["value"]
        span = float(nxt["time"]) - float(key["time"])
        progress = 0.0 if span <= 0 else (time_ms - float(key["time"])) / span
        eased = _ease_independent(str(key.get("ease") or "Linear"), progress)
        return float(key["value"]) + (float(nxt["value"]) - float(key["value"])) * eased
    return keys[-1]["value"]


def verify_anim_data(payload: Dict[str, object]) -> Dict[str, object]:
    """用独立实现重算 keyframes -> 逐帧状态，与 timeline.frames 逐槽位比较。"""
    import math

    if payload.get("schema") != "qx2d.anim":
        raise ValueError("不是 qx2d.anim 数据：%r" % payload.get("schema"))
    doc = payload["keyframes"]  # type: ignore[index]
    edit = payload.get("edit") or {}
    fps = int(doc["fps"])
    duration = float(doc["duration_ms"])
    frame_ms = 1000.0 / max(1, fps)
    per_loop = max(1, int(math.ceil(duration / frame_ms)))
    repeat = max(1, int(edit.get("repeat", 1) or 1))
    offset = edit.get("offset") or [0.0, 0.0]
    scale = float(edit.get("scale", 1.0) or 1.0)
    rotate = float(edit.get("rotate", 0.0) or 0.0)
    canvas = payload["canvas"]  # type: ignore[index]
    cx, cy = float(canvas["width"]) / 2.0, float(canvas["height"]) / 2.0
    theta = math.radians(rotate)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    frames = payload["timeline"]["frames"]  # type: ignore[index]
    tracks_by_target: Dict[str, list] = {}
    for track in doc["tracks"]:  # type: ignore[index]
        tracks_by_target.setdefault(str(track["target"]), []).append(track)

    max_diff = 0.0
    slot_diff = 0
    for frame_index, frame in enumerate(frames):
        loop_index = frame_index % per_loop
        time_ms = loop_index * frame_ms
        for slot_index, slot in enumerate(frame):
            target = doc["targets"][slot_index]  # type: ignore[index]
            state = {
                "x": float(target["x"]),
                "y": float(target["y"]),
                "width": float(target["width"]),
                "height": float(target["height"]),
                "rotation": float(target["rotation"]),
                "opacity": float(target["opacity"]),
                "visible": bool(target["visible"]),
                "colorIndex": int(target["colorIndex"]),
            }
            for track in tracks_by_target.get(str(target["name"]), []):
                field = str(track["field"])
                state[field] = _evaluate_key(track["keys"], time_ms, state[field], field)
            state["opacity"] = max(0.0, min(1.0, float(state["opacity"])))
            sx = float(state["x"]) * scale
            sy = float(state["y"]) * scale
            width = max(1.0, float(state["width"]) * scale)
            height = max(1.0, float(state["height"]) * scale)
            rx = cx + (sx - cx) * cos_t - (sy - cy) * sin_t + float(offset[0])
            ry = cy + (sx - cx) * sin_t + (sy - cy) * cos_t + float(offset[1])
            expected = {
                "visible": 1 if bool(state["visible"]) else 0,
                "x": round(rx, 3),
                "y": round(ry, 3),
                "width": round(width, 3),
                "height": round(height, 3),
                "rotation": round(float(state["rotation"]) + rotate, 3),
                "colorIndex": int(state["colorIndex"]),
                "opacity": round(float(state["opacity"]), 3),
            }
            for key, value in expected.items():
                got = slot.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    delta = abs(float(got) - float(value))
                    max_diff = max(max_diff, delta)
                    if delta > 1e-6:
                        slot_diff += 1
                elif got != value:
                    slot_diff += 1
    return {
        "checked": True,
        "frames": len(frames),
        "expected_frames": per_loop * repeat,
        "slot_diff": slot_diff,
        "max_value_diff": round(max_diff, 9),
    }


def _palette_quads_from_lua(text: str) -> List[RGBA]:
    """解析 `PALETTE = {[1] = {r, g, b, a}, ...}` 形式的调色板。"""
    pattern = re.compile(r"local\s+PALETTE\s*=\s*\{(.*?)\n\}", re.S)
    m = pattern.search(text)
    if not m:
        raise ValueError("在 Lua 文本里找不到 PALETTE")
    out: List[RGBA] = []
    for _index, body in re.findall(r"\[(\d+)\]\s*=\s*\{([^}]*)\}", m.group(1)):
        vals = [int(float(v)) for v in re.findall(r"-?\d+(?:\.\d+)?", body)]
        while len(vals) < 4:
            vals.append(255)
        out.append((vals[0], vals[1], vals[2], vals[3]))
    if not out:
        raise ValueError("PALETTE 里没有解析到颜色")
    return out


def decode_anim_lua(lua_text: str) -> Dict[str, object]:
    """独立解码动画 Lua 的逐帧槽位流。"""
    palette = _palette_quads_from_lua(lua_text)
    slot_count = int(_scalar_number(lua_text, "SLOT_COUNT"))
    frame_count = int(_scalar_number(lua_text, "FRAME_COUNT"))
    size_w = [int(v) for v in _table_numbers(lua_text, "SIZE_W")]
    size_h = [int(v) for v in _table_numbers(lua_text, "SIZE_H")]
    target_color = [int(v) for v in _table_numbers(lua_text, "SLOT_COLOR")]
    stream = _string_literals(lua_text, "ANIM_DATA")
    position = 0
    frames: List[List[Dict[str, object]]] = []
    for _ in range(frame_count):
        slots: List[Dict[str, object]] = []
        for slot in range(slot_count):
            visible, position = read_uint(stream, position)
            color_index, position = read_uint(stream, position)
            if not visible:
                slots.append({"slot": slot, "visible": 0, "colorIndex": color_index})
                continue
            raw_x, position = read_zigzag(stream, position)
            raw_y, position = read_zigzag(stream, position)
            size_index, position = read_uint(stream, position)
            raw_rot, position = read_zigzag(stream, position)
            alpha, position = read_uint(stream, position)
            slots.append(
                {
                    "slot": slot,
                    "visible": 1,
                    "x": round(raw_x / 10.0, 3),
                    "y": round(raw_y / 10.0, 3),
                    "width": size_w[size_index],
                    "height": size_h[size_index],
                    "rotation": round(raw_rot / 10.0, 3),
                    "opacity": round(alpha / 255.0, 3),
                    "colorIndex": color_index if color_index else target_color[slot],
                }
            )
        frames.append(slots)
    return {"palette": palette, "frames": frames, "cursor_at_end": position == len(stream)}


def anim_slot_diff(got: Sequence[Dict[str, object]], expected: Sequence[Dict[str, object]]) -> int:
    """比较两帧的槽位状态，返回不一致的字段数。"""
    if len(got) != len(expected):
        return abs(len(got) - len(expected)) + 1
    diff = 0
    for a, b in zip(got, expected):
        for key in ("visible", "colorIndex"):
            if int(a.get(key, 0)) != int(b.get(key, 0)):
                diff += 1
        if int(b.get("visible", 0)):
            for key in ("x", "y", "width", "height", "rotation"):
                if abs(float(a.get(key, 0.0)) - float(b.get(key, 0.0))) > 0.11:
                    diff += 1
            if abs(float(a.get("opacity", 0.0)) - float(b.get("opacity", 0.0))) > 1.0 / 255.0 + 1e-9:
                diff += 1
    return diff
