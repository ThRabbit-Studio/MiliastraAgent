"""qx2d.keyframes —— 关键帧文档模型：规范化、缓动求值、时间轴编辑与采样。

字段（每个目标一套）：
    x / y          控件中心（画布像素，原点左上、y 向下）
    width/height   控件尺寸
    rotation       顺时针为正，单位度
    opacity        0..1
    visible        布尔
    colorIndex     指向调色板（1 基准）

关键帧：{time: 毫秒, value: 数值/布尔, ease: 缓动名, interpolation: "linear"|"step"}
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional, Sequence, Tuple

from .easing import canonical, ease

FIELDS = ("x", "y", "width", "height", "rotation", "opacity", "visible", "colorIndex")
NUMERIC_FIELDS = ("x", "y", "width", "height", "rotation", "opacity", "colorIndex")


# --------------------------------------------------------------------------
# 规范化
# --------------------------------------------------------------------------

def normalize_document(raw: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("关键帧文档必须是 JSON 对象")
    if raw.get("schema") not in (None, "qx2d.keyframes"):
        raise ValueError("不是 qx2d.keyframes 文档：%r" % raw.get("schema"))

    canvas_raw = raw.get("canvas") or {}
    width = int(canvas_raw.get("width", 1600) or 1600)
    height = int(canvas_raw.get("height", 900) or 900)

    fps = int(raw.get("fps", 30) or 30)
    if fps <= 0:
        raise ValueError("fps 必须为正整数")

    targets_raw = raw.get("targets") or []
    if not targets_raw:
        raise ValueError("关键帧文档缺少 targets")
    targets: List[Dict[str, object]] = []
    seen = set()
    for item in targets_raw:
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("target 缺少 name")
        if name in seen:
            raise ValueError("target 名称重复：%s" % name)
        seen.add(name)
        target = {
            "name": name,
            "x": float(item.get("x", width / 2.0)),
            "y": float(item.get("y", height / 2.0)),
            "width": float(item.get("width", 100.0)),
            "height": float(item.get("height", 100.0)),
            "rotation": float(item.get("rotation", 0.0)),
            "opacity": float(item.get("opacity", 1.0)),
            "visible": bool(item.get("visible", True)),
            "colorIndex": int(item.get("colorIndex", 1)),
        }
        targets.append(target)

    tracks: List[Dict[str, object]] = []
    for track in raw.get("tracks") or []:
        target = str(track.get("target") or "").strip()
        field = str(track.get("field") or "").strip()
        if target not in seen:
            raise ValueError("轨道引用了不存在的 target：%s" % target)
        if field not in FIELDS:
            raise ValueError("轨道字段不支持：%s（可用：%s）" % (field, ", ".join(FIELDS)))
        keys: List[Dict[str, object]] = []
        for key in track.get("keys") or []:
            time_ms = float(key.get("time", 0.0))
            if time_ms < 0 or not math.isfinite(time_ms):
                raise ValueError("关键帧时间必须是非负有限数")
            value = key.get("value")
            if field == "visible":
                value = bool(value)
            elif field == "colorIndex":
                value = int(value)
            else:
                value = float(value)
            keys.append(
                {
                    "time": time_ms,
                    "value": value,
                    "ease": canonical(str(key.get("ease") or ("Step" if key.get("interpolation") == "step" else "Linear"))),
                    "interpolation": "step" if key.get("interpolation") == "step" or canonical(str(key.get("ease") or "")) == "Step" else "linear",
                }
            )
        keys.sort(key=lambda k: k["time"])
        if not keys:
            raise ValueError("轨道 %s.%s 没有关键帧" % (target, field))
        tracks.append({"target": target, "field": field, "keys": keys})

    duration_ms = int(raw.get("duration_ms") or 0)
    if duration_ms <= 0:
        last = 0.0
        for track in tracks:
            last = max(last, float(track["keys"][-1]["time"]))  # type: ignore[index]
        duration_ms = int(max(1.0, math.ceil(last)))

    events = []
    for event in raw.get("events") or []:
        params = event.get("params", "")
        if isinstance(params, (dict, list)):
            raise ValueError("事件 params 必须是字符串，不能是 JSON 结构")
        params = str(params)
        if len(params) > 4096:
            raise ValueError("事件 params 超过 4096 字")
        events.append(
            {
                "time": float(event.get("time", 0.0)),
                "name": str(event.get("name") or ""),
                "target": str(event.get("target") or ""),
                "params": params,
            }
        )
    events.sort(key=lambda e: (e["time"], e["name"]))

    palette_raw = raw.get("palette") or [[255, 255, 255, 255]]
    palette: List[Tuple[int, int, int, int]] = []
    for item in palette_raw:
        if isinstance(item, dict):
            rgba = item.get("rgba") or [255, 255, 255, 255]
        else:
            rgba = item
        vals = [int(v) for v in rgba]
        while len(vals) < 4:
            vals.append(255)
        palette.append((vals[0], vals[1], vals[2], vals[3]))
    for target in targets:
        ci = int(target["colorIndex"])
        if ci < 1 or ci > len(palette):
            raise ValueError("target %s 的 colorIndex 越界" % target["name"])

    return {
        "canvas": (width, height),
        "fps": fps,
        "duration_ms": duration_ms,
        "loop": bool(raw.get("loop", True)),
        "targets": targets,
        "tracks": tracks,
        "events": events,
        "palette": palette,
        "source": raw.get("source") or {"kind": "keyframes"},
    }


# --------------------------------------------------------------------------
# 求值
# --------------------------------------------------------------------------

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def evaluate_track(keys: Sequence[Dict[str, object]], time_ms: float, base: object, field: str) -> object:
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
        if key["interpolation"] == "step" or field == "visible" or field == "colorIndex":
            return key["value"]
        span = float(nxt["time"]) - float(key["time"])
        progress = 0.0 if span <= 0 else (time_ms - float(key["time"])) / span
        eased = ease(str(key["ease"]), progress)
        return _lerp(float(key["value"]), float(nxt["value"]), eased)
    return keys[-1]["value"]


def target_state(doc: Dict[str, object], target: Dict[str, object], time_ms: float) -> Dict[str, object]:
    state = {
        "name": target["name"],
        "x": float(target["x"]),
        "y": float(target["y"]),
        "width": float(target["width"]),
        "height": float(target["height"]),
        "rotation": float(target["rotation"]),
        "opacity": float(target["opacity"]),
        "visible": bool(target["visible"]),
        "colorIndex": int(target["colorIndex"]),
    }
    for track in doc["tracks"]:  # type: ignore[index]
        if track["target"] != target["name"]:
            continue
        field = str(track["field"])
        state[field] = evaluate_track(track["keys"], time_ms, state[field], field)  # type: ignore[arg-type]
    state["opacity"] = max(0.0, min(1.0, float(state["opacity"])))
    return state


# --------------------------------------------------------------------------
# 文档级编辑
# --------------------------------------------------------------------------

def apply_document_ops(doc: Dict[str, object], speed: float = 1.0, start_ms: float = 0.0,
                       end_ms: float = 0.0, reverse: bool = False, ease_override: str = "",
                       fps: int = 0, duration_ms: int = 0) -> Dict[str, object]:
    out = copy.deepcopy(doc)
    if ease_override:
        name = canonical(ease_override)
        for track in out["tracks"]:  # type: ignore[index]
            for key in track["keys"]:  # type: ignore[index]
                key["ease"] = name
                key["interpolation"] = "step" if name == "Step" else "linear"
    if speed and abs(float(speed) - 1.0) > 1e-9:
        if speed <= 0:
            raise ValueError("--speed 必须为正数")
        for track in out["tracks"]:  # type: ignore[index]
            for key in track["keys"]:  # type: ignore[index]
                key["time"] = float(key["time"]) / float(speed)
        for event in out["events"]:  # type: ignore[index]
            event["time"] = float(event["time"]) / float(speed)
        out["duration_ms"] = int(round(float(out["duration_ms"]) / float(speed)))  # type: ignore[arg-type]

    if start_ms > 0 and (not end_ms or end_ms <= start_ms):
        end_ms = float(out["duration_ms"])  # type: ignore[arg-type]
    if end_ms and end_ms > start_ms:
        for track in out["tracks"]:  # type: ignore[index]
            keys = track["keys"]  # type: ignore[index]
            value_at_start = evaluate_track(keys, start_ms, 0.0, str(track["field"]))
            value_at_end = evaluate_track(keys, end_ms, 0.0, str(track["field"]))
            kept = [k for k in keys if start_ms < float(k["time"]) < end_ms]
            head = {"time": 0.0, "value": value_at_start, "ease": "Linear", "interpolation": "linear"}
            tail = {"time": end_ms - start_ms, "value": value_at_end, "ease": "Linear", "interpolation": "linear"}
            track["keys"] = [head] + kept + [tail]
            for key in track["keys"]:  # type: ignore[index]
                key["time"] = float(key["time"]) - start_ms
        out["events"] = [
            dict(e, time=float(e["time"]) - start_ms)  # type: ignore[arg-type]
            for e in out["events"]  # type: ignore[index]
            if start_ms <= float(e["time"]) <= end_ms  # type: ignore[arg-type]
        ]
        out["duration_ms"] = int(round(end_ms - start_ms))

    if reverse:
        total = float(out["duration_ms"])  # type: ignore[arg-type]
        for track in out["tracks"]:  # type: ignore[index]
            track["keys"] = [dict(k, time=total - float(k["time"])) for k in track["keys"]]  # type: ignore[index]
            track["keys"].sort(key=lambda k: float(k["time"]))  # type: ignore[index]
        out["events"] = [dict(e, time=total - float(e["time"])) for e in out["events"]]  # type: ignore[index]
        out["events"].sort(key=lambda e: (float(e["time"]), str(e["name"])))  # type: ignore[index]

    if fps:
        out["fps"] = int(fps)
    if duration_ms:
        scale = float(duration_ms) / max(1.0, float(out["duration_ms"]))  # type: ignore[arg-type]
        for track in out["tracks"]:  # type: ignore[index]
            for key in track["keys"]:  # type: ignore[index]
                key["time"] = float(key["time"]) * scale
        for event in out["events"]:  # type: ignore[index]
            event["time"] = float(event["time"]) * scale
        out["duration_ms"] = int(duration_ms)
    return out


# --------------------------------------------------------------------------
# 采样成时间轴
# --------------------------------------------------------------------------

def sample(doc: Dict[str, object], offset_x: float = 0.0, offset_y: float = 0.0,
           scale: float = 1.0, rotate: float = 0.0, repeat: int = 1) -> Dict[str, object]:
    fps = int(doc["fps"])  # type: ignore[arg-type]
    duration = int(doc["duration_ms"])  # type: ignore[arg-type]
    frame_ms = 1000.0 / fps
    count = max(1, int(math.ceil(duration / frame_ms)))
    canvas = doc["canvas"]  # type: ignore[index]
    cx, cy = canvas[0] / 2.0, canvas[1] / 2.0  # type: ignore[index]
    theta = math.radians(rotate)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    frames: List[List[Dict[str, object]]] = []
    sizes: List[Tuple[int, int]] = []
    for index in range(count):
        time_ms = index * frame_ms
        slots: List[Dict[str, object]] = []
        for slot, target in enumerate(doc["targets"]):  # type: ignore[index]
            state = target_state(doc, target, time_ms)
            sx = float(state["x"]) * scale
            sy = float(state["y"]) * scale
            w = max(1.0, float(state["width"]) * scale)
            h = max(1.0, float(state["height"]) * scale)
            rx = cx + (sx - cx) * cos_t - (sy - cy) * sin_t + offset_x
            ry = cy + (sx - cx) * sin_t + (sy - cy) * cos_t + offset_y
            size = (int(round(w)), int(round(h)))
            if size not in sizes:
                sizes.append(size)
            slots.append(
                {
                    "slot": slot,
                    "visible": 1 if bool(state["visible"]) else 0,
                    "x": round(rx, 3),
                    "y": round(ry, 3),
                    "width": round(w, 3),
                    "height": round(h, 3),
                    "rotation": round(float(state["rotation"]) + rotate, 3),
                    "colorIndex": int(state["colorIndex"]),
                    "opacity": round(float(state["opacity"]), 3),
                }
            )
        frames.append(slots)

    durations = [int(round(frame_ms))] * count
    if repeat > 1:
        frames = frames * repeat
        durations = durations * repeat
    return {
        "frames": frames,
        "durations_ms": durations,
        "sizes": sizes,
        "fps": fps,
        "loop": bool(doc["loop"]),
        "frame_ms": frame_ms,
    }
