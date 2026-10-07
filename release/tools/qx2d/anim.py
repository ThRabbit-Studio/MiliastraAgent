"""qx2d.anim —— 动画编辑：关键帧文档 → 缓动求值 → 时间轴采样 → JSON / Lua。"""

from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Tuple

from . import emit_json, keyframes, verify
from .easing import canonical

DEFAULT_PALETTE = [(255, 255, 255, 255)]


def run(
    input_path: str,
    out_dir: str,
    speed: float = 1.0,
    start_ms: float = 0.0,
    end_ms: float = 0.0,
    reverse: bool = False,
    ease: str = "",
    fps: int = 0,
    duration_ms: int = 0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    scale: float = 1.0,
    rotate: float = 0.0,
    repeat: int = 1,
    emit: str = "json",
    out_name: str = "anim",
    lua_name: str = "levelScript",
    preview: bool = True,
    preview_scale: int = 0,
    json_indent: int = 1,
    lua_version: str = "5.3",
    image_prefab_id: int = 0,
    image_source: int = 0,
    image_type: int = 0,
    square_asset_id: int = 0,
    tintable: bool = True,
    base_pixel_size: float = 8,
    canvas_margin: float = 0.9,
    create_batch: int = 60,
) -> Dict[str, object]:
    os.makedirs(out_dir, exist_ok=True)
    with open(input_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if raw.get("schema") == "qx2d.anim":
        raw = raw.get("keyframes") or {}
        if not raw:
            raise ValueError("这个动画文件里没有 keyframes 字段，无法继续编辑")
        raw.setdefault("schema", "qx2d.keyframes")
    doc = keyframes.normalize_document(raw)

    edited = keyframes.apply_document_ops(
        doc,
        speed=speed,
        start_ms=start_ms,
        end_ms=end_ms,
        reverse=reverse,
        ease_override=ease,
        fps=fps,
        duration_ms=duration_ms,
    )
    sampled = keyframes.sample(
        edited,
        offset_x=offset_x,
        offset_y=offset_y,
        scale=scale,
        rotate=rotate,
        repeat=max(1, int(repeat)),
    )

    palette: List[Tuple[int, int, int, int]] = doc["palette"]  # type: ignore[assignment]
    canvas = doc["canvas"]  # type: ignore[misc]
    targets = [
        {"slot": index, "name": t["name"], "colorIndex": int(t["colorIndex"])}
        for index, t in enumerate(doc["targets"])  # type: ignore[index]
    ]
    events = [dict(e) for e in edited["events"]]  # type: ignore[index]
    if repeat > 1:
        base_events = list(events)
        total = float(edited["duration_ms"])  # type: ignore[arg-type]
        repeated = []
        for index in range(int(repeat)):
            for event in base_events:
                repeated.append(dict(event, time=float(event["time"]) + index * total))
        events = repeated

    keyframe_dump = {
        "canvas": {"width": canvas[0], "height": canvas[1]},
        "fps": edited["fps"],
        "duration_ms": edited["duration_ms"],
        "loop": edited["loop"],
        "targets": edited["targets"],
        "tracks": edited["tracks"],
        "events": edited["events"],
    }

    stats = {
        "targets": len(targets),
        "tracks": len(edited["tracks"]),  # type: ignore[arg-type]
        "keyframes": sum(len(t["keys"]) for t in edited["tracks"]),  # type: ignore[union-attr,arg-type]
        "frame_count": len(sampled["frames"]),
        "fps": sampled["fps"],
        "duration_ms": sum(sampled["durations_ms"]),
        "events": len(events),
        "sizes": len(sampled["sizes"]),
    }
    options = {
        "image_prefab_id": image_prefab_id,
        "image_source": image_source,
        "image_type": image_type,
        "square_asset_id": square_asset_id,
        "tintable": tintable,
        "base_pixel_size": base_pixel_size,
        "canvas_margin": canvas_margin,
        "create_batch": create_batch,
        "lua_version": lua_version,
    }
    plan: Dict[str, object] = {
        "source": {"path": os.path.abspath(input_path), "kind": "keyframes"},
        "canvas": canvas,
        "options": options,
        "palette": palette,
        "sizes": sampled["sizes"],
        "targets": targets,
        "fps": sampled["fps"],
        "loop": sampled["loop"],
        "frames": sampled["frames"],
        "durations_ms": sampled["durations_ms"],
        "events": events,
        "keyframes": keyframe_dump,
        "edit": {
            "speed": speed,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "reverse": reverse,
            "ease": canonical(ease) if ease else "",
            "fps": edited["fps"],
            "duration_ms": edited["duration_ms"],
            "offset": [offset_x, offset_y],
            "scale": scale,
            "rotate": rotate,
            "repeat": max(1, int(repeat)),
        },
        "stats": stats,
    }

    outputs: Dict[str, str] = {}
    emitted: List[str] = []
    data_path = os.path.join(out_dir, "%s.json" % out_name)
    report_path = os.path.join(out_dir, "%s.report.json" % out_name)
    data_bytes = 0
    json_round_trip: Dict[str, object] = {"checked": False}

    if emit in ("json", "both"):
        payload = emit_json.anim_data(plan)
        data_bytes = emit_json.write_json(data_path, payload, json_indent)
        outputs["data_json"] = data_path
        with open(data_path, "r", encoding="utf-8") as f:
            reloaded = json.load(f)
        json_round_trip = verify.verify_anim_data(reloaded)
        emitted.append("json")

    lua_round_trip: Dict[str, object] = {"checked": False}
    if emit in ("lua", "both"):
        from .lua_anim import emit_anim_lua

        lua_text = emit_anim_lua(plan)
        lua_path = os.path.join(out_dir, "%s.lua" % lua_name)
        with open(lua_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(lua_text)
        outputs["lua"] = lua_path
        decoded = verify.decode_anim_lua(lua_text)
        slots_diff = 0
        frames_with_diff = 0
        for index, frame in enumerate(sampled["frames"]):
            got = decoded["frames"][index] if index < len(decoded["frames"]) else []
            diff = verify.anim_slot_diff(got, frame)
            slots_diff += diff
            if diff:
                frames_with_diff += 1
        lua_round_trip = {
            "checked": True,
            "frames_with_diff": frames_with_diff,
            "slot_diff": slots_diff,
            "cursor_at_end": decoded["cursor_at_end"],
            "lua_static": verify.lua_static_check(lua_text),
            "lua_bytes": len(lua_text.encode("utf-8")),
        }
        emitted.append("lua")

    if preview:
        from PIL import Image, ImageDraw

        scale_p = preview_scale if preview_scale > 0 else max(1, min(4, 1024 // max(1, max(canvas[0], canvas[1]))))
        preview_path = os.path.join(out_dir, "%s.preview.png" % out_name)
        image = Image.new("RGBA", (canvas[0], canvas[1]), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image, "RGBA")
        for slot in sampled["frames"][0]:
            if not slot["visible"]:
                continue
            color = palette[int(slot["colorIndex"]) - 1]
            x0 = slot["x"] - slot["width"] / 2.0
            y0 = slot["y"] - slot["height"] / 2.0
            draw.rectangle(
                [x0, y0, x0 + slot["width"], y0 + slot["height"]],
                fill=(color[0], color[1], color[2], int(round(color[3] * slot["opacity"]))),
            )
        if scale_p > 1:
            image = image.resize((canvas[0] * scale_p, canvas[1] * scale_p), Image.NEAREST)
        image.save(preview_path)
        outputs["preview_png"] = preview_path

    json_ok = bool(json_round_trip.get("checked") and json_round_trip.get("max_value_diff", 1) <= 1e-6)
    lua_ok = True
    if lua_round_trip.get("checked"):
        lua_ok = bool(
            lua_round_trip.get("slot_diff") == 0  # type: ignore[union-attr]
            and lua_round_trip.get("cursor_at_end") is True  # type: ignore[union-attr]
            and lua_round_trip.get("lua_static", {}).get("bracket_balanced") is True  # type: ignore[union-attr]
        )
    ok = bool(json_ok and lua_ok and sampled["frames"])

    report: Dict[str, object] = {
        "tool": "qx2d anim",
        "ok": ok,
        "input": {"path": os.path.abspath(input_path), "targets": len(targets)},
        "parameters": {
            "speed": speed,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "reverse": reverse,
            "ease": canonical(ease) if ease else "",
            "fps": edited["fps"],
            "duration_ms": edited["duration_ms"],
            "offset": [offset_x, offset_y],
            "scale": scale,
            "rotate": rotate,
            "repeat": repeat,
            "emit": emit,
            "lua_version": lua_version,
        },
        "timeline": {
            "frame_count": len(sampled["frames"]),
            "fps": sampled["fps"],
            "loop": sampled["loop"],
            "frame_ms": sampled["frame_ms"],
            "duration_ms": sum(sampled["durations_ms"]),
            "slots": len(targets),
        },
        "verify": {"json_round_trip": json_round_trip, "lua_round_trip": lua_round_trip},
        "bytes": {"data_json": data_bytes},
        "emitted": emitted,
        "files": dict(outputs, report=report_path),
        "runtime_verified": "pending",
        "notes": [
            "默认输出 JSON 数据；需要 Lua 时加 --emit lua 或 --emit both。",
            "JSON 的 timeline.frames 是用独立实现重新求值校验过的（相对 keyframes 字段）。",
            "events 的 params 是原样字符串，由接入方自行分发，不解析、不当代码执行。",
            "Lua 里的 CONFIG 常量必须按本项目 annotations.lua / readme.md 替换。",
        ],
    }
    emit_json.write_json(report_path, report, json_indent)
    return report
