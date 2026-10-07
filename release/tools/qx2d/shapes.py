"""qx2d.shapes —— 图元拟合：用矩形 / 椭圆 / 三角形迭代拟合一张图片。

输出 JSON 数据契约（默认）或 Lua 绘制脚本（--emit lua），并做独立栅格化 round-trip 校验。
"""

from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Sequence, Tuple

import numpy as np

from . import emit_json, verify
from .core import apply_alpha_threshold, load_raster, sha256_file
from .primitives import (
    KIND_BY_NAME,
    NAME_BY_KIND,
    FitParams,
    Fitter,
    coverage_map,
)

DEFAULT_KINDS = "rect,ellipse,triangle"


def _parse_kinds(spec: str) -> List[int]:
    out: List[int] = []
    for raw in (spec or DEFAULT_KINDS).split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if name not in KIND_BY_NAME:
            raise ValueError("未知图元类型：%s（可用：rect, ellipse, triangle）" % name)
        kind = KIND_BY_NAME[name]
        if kind not in out:
            out.append(kind)
    if not out:
        raise ValueError("至少要允许一种图元类型")
    return out


def _quantize(color: np.ndarray, bits: int) -> Tuple[int, int, int, int]:
    step = 1 << max(0, 8 - int(bits))
    vals = []
    for c in color[:3]:
        v = int(round(float(c) * 255.0)) // step * step + step // 2
        vals.append(int(max(0, min(255, v))))
    return (vals[0], vals[1], vals[2], 255)


def render_elements(
    elements: Sequence[Dict[str, object]],
    palette: Sequence[Tuple[int, int, int, int]],
    width: int,
    height: int,
) -> np.ndarray:
    """把图元列表按顺序合成成 (H, W, 4) float32（0..1）。"""
    canvas = np.zeros((height, width, 4), dtype=np.float32)
    for element in elements:
        cx = float(element["x"])
        cy = float(element["y"])
        hw = max(float(element["width"]) / 2.0, 1e-6)
        hh = max(float(element["height"]) / 2.0, 1e-6)
        rot = float(element.get("rotation", 0.0))
        opacity = float(element.get("opacity", 1.0))
        rgba = palette[int(element["colorIndex"]) - 1]
        x0, y0, x1, y1 = verify_shape_bounds(int(element["kind"]), cx, cy, hw, hh, rot, width, height)
        xs = np.arange(x0, x1, dtype=np.float32) + 0.5
        ys = np.arange(y0, y1, dtype=np.float32) + 0.5
        gx, gy = np.meshgrid(xs, ys)
        cov = coverage_map(int(element["kind"]), gx, gy, cx, cy, hw, hh, rot).astype(np.float32) * opacity
        if not np.any(cov > 0.0):
            continue
        color = np.array([rgba[0] / 255.0, rgba[1] / 255.0, rgba[2] / 255.0, rgba[3] / 255.0], dtype=np.float32)
        a = cov[..., None]
        region = canvas[y0:y1, x0:x1]
        canvas[y0:y1, x0:x1] = region * (1.0 - a) + color[None, None, :] * a
    return canvas


def verify_shape_bounds(kind: int, cx: float, cy: float, hw: float, hh: float, rot: float, w: int, h: int):
    theta = math.radians(rot)
    cos_t, sin_t = abs(math.cos(theta)), abs(math.sin(theta))
    ex = hw * cos_t + hh * sin_t
    ey = hw * sin_t + hh * cos_t
    x0 = max(0, int(math.floor(cx - ex)) - 1)
    y0 = max(0, int(math.floor(cy - ey)) - 1)
    x1 = min(w, int(math.ceil(cx + ex)) + 2)
    y1 = min(h, int(math.ceil(cy + ey)) + 2)
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def run(
    input_path: str,
    out_dir: str,
    num_shapes: int = 200,
    candidates: int = 16,
    climb: int = 32,
    kinds: str = DEFAULT_KINDS,
    allow_rotation: bool = True,
    min_size: float = 0.0,
    max_size: float = 0.0,
    spill_penalty: float = 4.0,
    alpha_min: float = 0.15,
    alpha_max: float = 1.0,
    alpha_threshold: int = 0,
    fit_scale: float = 1.0,
    color_bits: int = 8,
    min_psnr: float = 0.0,
    seed: int = 12345,
    emit: str = "json",
    out_name: str = "shapes",
    lua_name: str = "levelScript",
    preview: bool = True,
    preview_scale: int = 0,
    json_indent: int = 1,
    lua_version: str = "5.3",
    image_prefab_id: int = 0,
    image_source: int = 0,
    image_type: int = 0,
    rect_asset_id: int = 0,
    ellipse_asset_id: int = 0,
    triangle_asset_id: int = 0,
    tintable: bool = True,
    base_pixel_size: float = 8,
    canvas_margin: float = 0.9,
) -> Dict[str, object]:
    os.makedirs(out_dir, exist_ok=True)
    raster = apply_alpha_threshold(load_raster(input_path), alpha_threshold)
    source_sha = sha256_file(input_path)
    src_w, src_h = raster.width, raster.height

    arr = np.frombuffer(bytes(raster.buf), dtype=np.uint8).reshape(src_h, src_w, 4).astype(np.float32) / 255.0
    has_alpha = bool(np.any(arr[:, :, 3] < 0.999))
    mode = "png-alpha" if has_alpha else "full-frame"
    weight_full = arr[:, :, 3] if has_alpha else np.ones((src_h, src_w), dtype=np.float32)

    scale = float(fit_scale) if fit_scale and fit_scale > 0 else 1.0
    if abs(scale - 1.0) > 1e-6:
        from PIL import Image

        img = Image.fromarray((arr * 255.0 + 0.5).astype(np.uint8), "RGBA")
        nw, nh = max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale)))
        small = np.asarray(img.resize((nw, nh), Image.BILINEAR)).astype(np.float32) / 255.0
    else:
        nw, nh = src_w, src_h
        small = arr

    target = small[:, :, :3]
    weight = small[:, :, 3] if has_alpha else np.ones((nh, nw), dtype=np.float32)

    params = FitParams(
        num_shapes=num_shapes,
        candidates=candidates,
        climb_iterations=climb,
        allowed_kinds=_parse_kinds(kinds),
        min_size=min_size * scale,
        max_size=max_size * scale,
        allow_rotation=allow_rotation,
        spill_penalty=spill_penalty,
        alpha_min=alpha_min,
        alpha_max=alpha_max,
        seed=seed,
    )
    fitter = Fitter(target, weight, params)
    raw_shapes = fitter.fit()
    quality = fitter.quality()

    # 颜色量化 -> 调色板
    palette: List[Tuple[int, int, int, int]] = []
    index_of: Dict[Tuple[int, int, int, int], int] = {}
    elements: List[Dict[str, object]] = []
    inv = 1.0 / scale
    for shape in raw_shapes:
        rgba = _quantize(np.array(shape["color"], dtype=np.float32), color_bits)
        if rgba not in index_of:
            index_of[rgba] = len(palette) + 1
            palette.append(rgba)
        elements.append(
            {
                "kind": int(shape["kind"]),
                "x": round(float(shape["cx"]) * inv, 3),
                "y": round(float(shape["cy"]) * inv, 3),
                "width": round(float(shape["hw"]) * 2.0 * inv, 3),
                "height": round(float(shape["hh"]) * 2.0 * inv, 3),
                "rotation": round(float(shape["rotation"]) % 360.0, 3),
                "colorIndex": index_of[rgba],
                "opacity": round(float(shape["opacity"]), 3),
            }
        )

    render = render_elements(elements, palette, src_w, src_h)
    rendered_pixels = (render * 255.0 + 0.5).astype(np.uint8)
    target_pixels = (arr * 255.0 + 0.5).astype(np.uint8)
    mask = arr[:, :, 3] > 0.0
    if not np.any(mask):
        mask = np.ones((src_h, src_w), dtype=bool)
    diff = np.abs(rendered_pixels[:, :, :3].astype(np.int16) - target_pixels[:, :, :3].astype(np.int16))
    diff_where = diff[mask]
    mae = float(diff_where.mean()) if diff_where.size else 0.0
    mse = float((diff_where.astype(np.float64) ** 2).mean()) if diff_where.size else 0.0
    psnr = 99.0 if mse <= 1e-12 else float(10.0 * math.log10((255.0 ** 2) / mse))
    different = int((diff.max(axis=2) > 0)[mask].sum()) if diff_where.size else 0
    image_quality = {
        "mae": round(mae, 4),
        "rmse": round(math.sqrt(mse), 4),
        "psnr": round(psnr, 3),
        "different_pixels": different,
        "pixels_in_mask": int(mask.sum()),
    }

    kind_counts: Dict[str, int] = {}
    for element in elements:
        name = NAME_BY_KIND[int(element["kind"])]
        kind_counts[name] = kind_counts.get(name, 0) + 1

    stats = {
        "elements": len(elements),
        "by_kind": kind_counts,
        "palette_size": len(palette),
        "color_bits": color_bits,
        "mean_opacity": round(float(np.mean([e["opacity"] for e in elements])) if elements else 0.0, 4),
        "fit_resolution": [nw, nh],
        "fit_quality": {k: round(v, 6) if isinstance(v, float) else v for k, v in quality.items()},
        "image_quality": image_quality,
    }

    options = {
        "image_prefab_id": image_prefab_id,
        "image_source": image_source,
        "image_type": image_type,
        "rect_asset_id": rect_asset_id,
        "ellipse_asset_id": ellipse_asset_id,
        "triangle_asset_id": triangle_asset_id,
        "tintable": tintable,
        "base_pixel_size": base_pixel_size,
        "canvas_margin": canvas_margin,
        "lua_version": lua_version,
    }
    plan: Dict[str, object] = {
        "source": os.path.abspath(input_path),
        "source_sha256": source_sha,
        "source_width": src_w,
        "source_height": src_h,
        "canvas": (src_w, src_h),
        "fit_scale": scale,
        "palette": palette,
        "elements": elements,
        "stats": stats,
        "options": options,
    }

    outputs: Dict[str, str] = {}
    emitted: List[str] = []
    data_path = os.path.join(out_dir, "%s.json" % out_name)
    report_path = os.path.join(out_dir, "%s.report.json" % out_name)
    data_bytes = 0
    json_round_trip: Dict[str, object] = {"checked": False}

    if emit in ("json", "both"):
        payload = emit_json.shapes_data(plan)
        data_bytes = emit_json.write_json(data_path, payload, json_indent)
        outputs["data_json"] = data_path
        with open(data_path, "r", encoding="utf-8") as f:
            reloaded = json.load(f)
        indep = verify.rasterize_shapes(reloaded)
        ref = (render * 255.0 + 0.5).astype(np.uint8)
        diff_rt = np.abs(indep.astype(np.int16) - ref.astype(np.int16))
        json_round_trip = {
            "checked": True,
            "max_channel_error": int(diff_rt.max()) if diff_rt.size else 0,
            "different_pixels": int((diff_rt.max(axis=2) > 0).sum()) if diff_rt.size else 0,
            "elements": len(reloaded["elements"]),
        }
        emitted.append("json")

    lua_round_trip: Dict[str, object] = {"checked": False}
    if emit in ("lua", "both"):
        from .lua_shapes import emit_shapes_lua

        lua_text = emit_shapes_lua(plan)
        lua_path = os.path.join(out_dir, "%s.lua" % lua_name)
        with open(lua_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(lua_text)
        outputs["lua"] = lua_path
        lua_round_trip = {
            "checked": True,
            "lua_static": verify.lua_static_check(lua_text),
            "lua_bytes": len(lua_text.encode("utf-8")),
            "elements_in_script": lua_text.count("\n    {"),
        }
        emitted.append("lua")

    if preview:
        from PIL import Image

        scale_p = preview_scale if preview_scale > 0 else max(1, min(4, 1024 // max(1, max(src_w, src_h))))
        preview_path = os.path.join(out_dir, "%s.preview.png" % out_name)
        img = rendered_pixels
        if scale_p > 1:
            img = np.asarray(Image.fromarray(img, "RGBA").resize((src_w * scale_p, src_h * scale_p), Image.NEAREST))
        Image.fromarray(img, "RGBA").save(preview_path)
        outputs["preview_png"] = preview_path

    round_trip_ok = bool(
        json_round_trip.get("checked")
        and int(json_round_trip.get("max_channel_error", 0)) <= 1
    ) or not json_round_trip.get("checked")
    lua_ok = True
    if lua_round_trip.get("checked"):
        lua_ok = bool(lua_round_trip.get("lua_static", {}).get("bracket_balanced"))  # type: ignore[union-attr]
    psnr_ok = (min_psnr <= 0.0) or (image_quality["psnr"] >= float(min_psnr))
    ok = bool(round_trip_ok and lua_ok and psnr_ok and elements)

    report: Dict[str, object] = {
        "tool": "qx2d shapes",
        "ok": ok,
        "input": {
            "path": os.path.abspath(input_path),
            "sha256": source_sha,
            "width": src_w,
            "height": src_h,
            "mode": mode,
            "alpha_threshold": alpha_threshold,
        },
        "parameters": {
            "num_shapes": num_shapes,
            "candidates": candidates,
            "climb": climb,
            "kinds": kinds,
            "allow_rotation": allow_rotation,
            "min_size": min_size,
            "max_size": max_size,
            "spill_penalty": spill_penalty,
            "alpha_range": [alpha_min, alpha_max],
            "fit_scale": scale,
            "color_bits": color_bits,
            "min_psnr": min_psnr,
            "seed": seed,
            "emit": emit,
            "lua_version": lua_version,
        },
        "fit": stats,
        "verify": {"json_round_trip": json_round_trip, "lua_round_trip": lua_round_trip},
        "bytes": {"data_json": data_bytes},
        "emitted": emitted,
        "files": dict(outputs, report=report_path),
        "runtime_verified": "pending",
        "notes": [
            "图元拟合是有损还原：Elements 越少误差越大，用 fit.image_quality.psnr 判断够不够用。",
            "默认输出 JSON 数据；需要 Lua 时加 --emit lua 或 --emit both。",
            "JSON 已按独立栅格化器 round-trip 校验（最大通道误差 <= 1）。",
            "Lua 里的 CONFIG 常量必须按本项目 annotations.lua / readme.md 替换。",
        ],
    }
    emit_json.write_json(report_path, report, json_indent)
    return report
