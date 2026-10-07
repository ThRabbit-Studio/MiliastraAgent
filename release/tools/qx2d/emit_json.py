"""qx2d.emit_json —— 把结果输出成 JSON 数据（工具的默认产物）。

JSON 是"数据契约"：调色板、控件记录 / 槽位时间轴、画布与 CONFIG 全部显式写出，
任何消费者（AI、脚本、另一个生成器）都能据此自己生成 Lua 或做别的实现。
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Sequence, Tuple

from . import __version__

SCHEMA_VERSION = 1


def _rgba_list(palette: Sequence[Tuple[int, int, int, int]]) -> List[Dict[str, object]]:
    return [{"index": i + 1, "rgba": [c[0], c[1], c[2], c[3]]} for i, c in enumerate(palette)]


def pixel_data(plan: Dict[str, object]) -> Dict[str, object]:
    """像素画数据契约。"""
    palette = plan["palette"]  # type: ignore[assignment]
    records: List[Dict[str, int]] = plan["records"]  # type: ignore[assignment]
    options: Dict[str, object] = plan["options"]  # type: ignore[assignment]
    origin = plan.get("origin", (0, 0))  # type: ignore[misc]
    canvas = plan["canvas"]  # type: ignore[misc]
    return {
        "schema": "qx2d.pixel",
        "schema_version": SCHEMA_VERSION,
        "tool": "qx2d",
        "tool_version": __version__,
        "source": {
            "path": plan["source"],
            "sha256": plan["source_sha256"],
            "width": plan["source_width"],
            "height": plan["source_height"],
        },
        "grid": {
            "cell": plan["cell"],
            "width": plan["grid"][0],  # type: ignore[index]
            "height": plan["grid"][1],  # type: ignore[index]
            "evidence": plan.get("grid_evidence", ""),
            "trimmed": bool(plan.get("trimmed", False)),
        },
        "canvas": {
            "width": canvas[0],  # type: ignore[index]
            "height": canvas[1],  # type: ignore[index]
            "origin_x": origin[0],
            "origin_y": origin[1],
            "base_pixel_size": options["base_pixel_size"],
            "canvas_margin": options["canvas_margin"],
        },
        "config": {
            "image_prefab_id": options["image_prefab_id"],
            "image_source": options["image_source"],
            "image_type": options["image_type"],
            "square_asset_id": options["square_asset_id"],
            "tintable": options["tintable"],
            "lua_version": options.get("lua_version", "5.3"),
        },
        "palette": _rgba_list(palette),  # type: ignore[arg-type]
        "records": records,
        "record_keys": ["kind", "x", "y", "width", "height", "angle", "colorIndex"],
        "stats": plan["stats"],
        "notes": [
            "坐标以画布左上角为原点，x 向右、y 向下；覆盖区间 x <= X < x+width。",
            "colorIndex 指向 palette[].index（1 基准）。painter order = records 数组顺序。",
            "kind=0 表示一个可独立缩放宽高的实心矩形图元（方形基础图元）。",
            "config 里的常量必须替换为本项目 annotations.lua / readme.md 的真实值。",
            "运行时验证状态见同名 .report.json 的 runtime_verified 字段。",
        ],
    }


def frames_data(plan: Dict[str, object]) -> Dict[str, object]:
    """帧动画数据契约。"""
    palette = plan["palette"]  # type: ignore[assignment]
    groups: List[Dict[str, int]] = plan["groups"]  # type: ignore[assignment]
    sizes: List[Tuple[int, int]] = plan["sizes"]  # type: ignore[assignment]
    options: Dict[str, object] = plan["options"]  # type: ignore[assignment]
    origin = plan["origin"]  # type: ignore[misc]
    local = plan["local"]  # type: ignore[misc]
    timeline: List[List[Dict[str, int]]] = plan["frames"]  # type: ignore[assignment]
    return {
        "schema": "qx2d.frames",
        "schema_version": SCHEMA_VERSION,
        "tool": "qx2d",
        "tool_version": __version__,
        "source": {
            "path": plan["source"],
            "sha256": plan["source_sha256"],
            "frames": plan["source_frames"],
            "width": plan["source_width"],
            "height": plan["source_height"],
        },
        "grid": {
            "cell": plan["cell"],
            "width": plan["grid"][0],  # type: ignore[index]
            "height": plan["grid"][1],  # type: ignore[index]
            "evidence": plan.get("grid_evidence", ""),
            "trimmed": bool(plan.get("trimmed", False)),
        },
        "canvas": {
            "origin_x": origin[0],
            "origin_y": origin[1],
            "local_width": local[0],
            "local_height": local[1],
            "base_pixel_size": options["base_pixel_size"],
            "canvas_margin": options["canvas_margin"],
        },
        "config": {
            "image_prefab_id": options["image_prefab_id"],
            "image_source": options["image_source"],
            "image_type": options["image_type"],
            "square_asset_id": options["square_asset_id"],
            "tintable": options["tintable"],
            "create_batch": options["create_batch"],
            "lua_version": options.get("lua_version", "5.3"),
        },
        "palette": _rgba_list(palette),  # type: ignore[arg-type]
        "sizes": [[s[0], s[1]] for s in sizes],
        "groups": groups,
        "pool_size": plan["pool_size"],
        "timeline": {
            "fps": plan["fps"],
            "loop": plan["loop"],
            "frame_count": len(timeline),
            "durations_ms": plan["durations_ms"],
            "frames": timeline,
        },
        "frame_keys": ["slot", "colorIndex", "x", "y", "width", "height"],
        "stats": plan["stats"],
        "notes": [
            "同一帧里同色矩形按 slot 升序填入该颜色组的控件池前缀，未出现在帧里的槽位视为隐藏。",
            "控件池大小 pool_size = 各颜色组 groups[].pool 之和；播放期不创建、不销毁控件。",
            "坐标以画布左上角为原点；source 坐标系与 local 坐标系通过 canvas.origin_x/origin_y 换算。",
            "config 里的常量必须替换为本项目 annotations.lua / readme.md 的真实值。",
            "运行时验证状态见同名 .report.json 的 runtime_verified 字段。",
        ],
    }


def write_json(path: str, payload: object, indent: int = 1) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=(indent if indent > 0 else None),
                      separators=(",", ":") if indent <= 0 else None)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return len(text.encode("utf-8"))


def shapes_data(plan: Dict[str, object]) -> Dict[str, object]:
    """图元拟合数据契约。"""
    palette = plan["palette"]  # type: ignore[assignment]
    elements: List[Dict[str, object]] = plan["elements"]  # type: ignore[assignment]
    options: Dict[str, object] = plan["options"]  # type: ignore[assignment]
    canvas = plan["canvas"]  # type: ignore[misc]
    return {
        "schema": "qx2d.shapes",
        "schema_version": SCHEMA_VERSION,
        "tool": "qx2d",
        "tool_version": __version__,
        "source": {
            "path": plan["source"],
            "sha256": plan["source_sha256"],
            "width": plan["source_width"],
            "height": plan["source_height"],
        },
        "canvas": {
            "width": canvas[0],  # type: ignore[index]
            "height": canvas[1],  # type: ignore[index]
            "fit_scale": plan.get("fit_scale", 1.0),
            "background": "transparent",
        },
        "config": {
            "image_prefab_id": options["image_prefab_id"],
            "image_source": options["image_source"],
            "image_type": options["image_type"],
            "rect_asset_id": options["rect_asset_id"],
            "ellipse_asset_id": options["ellipse_asset_id"],
            "triangle_asset_id": options["triangle_asset_id"],
            "tintable": options["tintable"],
            "lua_version": options.get("lua_version", "5.3"),
        },
        "palette": _rgba_list(palette),  # type: ignore[arg-type]
        "elements": elements,
        "element_keys": ["kind", "x", "y", "width", "height", "rotation", "colorIndex", "opacity"],
        "stats": plan["stats"],
        "notes": [
            "kind：0=矩形（可旋转）、1=椭圆、2=等腰三角形；都是可独立缩放宽高的图片控件。",
            "x/y 是图元中心（画布像素，原点左上、y 向下）；width/height 是外接尺寸；rotation 顺时针为正、单位度。",
            "数组顺序就是绘制顺序（先画底层）；opacity 是 0..1，与调色板颜色相乘。",
            "三角形轴心取质心，其余形状取中心；接入时按这个规则设 pivot。",
            "config 里的常量必须替换为本项目 annotations.lua / readme.md 的真实值。",
        ],
    }


def anim_data(plan: Dict[str, object]) -> Dict[str, object]:
    """动画数据契约（关键帧 + 采样后的逐帧状态 + 事件）。"""
    palette = plan["palette"]  # type: ignore[assignment]
    options: Dict[str, object] = plan["options"]  # type: ignore[assignment]
    canvas = plan["canvas"]  # type: ignore[misc]
    return {
        "schema": "qx2d.anim",
        "schema_version": SCHEMA_VERSION,
        "tool": "qx2d",
        "tool_version": __version__,
        "source": plan["source"],
        "canvas": {
            "width": canvas[0],  # type: ignore[index]
            "height": canvas[1],  # type: ignore[index]
            "base_pixel_size": options["base_pixel_size"],
            "canvas_margin": options["canvas_margin"],
        },
        "config": {
            "image_prefab_id": options["image_prefab_id"],
            "image_source": options["image_source"],
            "image_type": options["image_type"],
            "square_asset_id": options["square_asset_id"],
            "tintable": options["tintable"],
            "create_batch": options["create_batch"],
            "lua_version": options.get("lua_version", "5.3"),
        },
        "palette": _rgba_list(palette),  # type: ignore[arg-type]
        "sizes": [[s[0], s[1]] for s in plan["sizes"]],  # type: ignore[index]
        "targets": plan["targets"],
        "timeline": {
            "fps": plan["fps"],
            "loop": plan["loop"],
            "frame_count": len(plan["frames"]),  # type: ignore[arg-type]
            "durations_ms": plan["durations_ms"],
            "frames": plan["frames"],
        },
        "events": plan["events"],
        "keyframes": plan["keyframes"],
        "edit": plan.get("edit", {}),
        "stats": plan["stats"],
        "frame_keys": ["slot", "visible", "x", "y", "width", "height", "rotation", "colorIndex", "opacity"],
        "notes": [
            "keyframes 是编辑后的规范化关键帧；timeline.frames 是按 fps 采样出来的逐帧绝对状态。",
            "每帧每个 slot 的状态：visible / x / y / width / height / rotation / colorIndex / opacity。",
            "x/y 是控件中心（画布像素，原点左上、y 向下）；rotation 顺时针为正、单位度。",
            "events 的 params 是原样传递的字符串，由接入方自行分发，不解析、不当代码执行。",
            "config 里的常量必须替换为本项目 annotations.lua / readme.md 的真实值。",
        ],
    }
