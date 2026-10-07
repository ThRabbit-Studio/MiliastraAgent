"""qx2d.pixel —— 像素画重建：参考图 → JSON 数据（默认）/ Lua + 独立校验。

默认产物是 JSON 数据契约（见 `emit_json.py`），需要 Lua 时加 `--emit lua|both`。
无论输出哪种格式，都会用独立实现解回来重新栅格化做校验。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List

from . import emit_json, verify
from .core import (
    apply_alpha_threshold,
    build_palette,
    color_counts,
    compare_by_palette,
    drop_transparent,
    infer_cell,
    load_raster,
    rasterize_records,
    records_from_rects,
    rects_by_color,
    sha256_file,
    to_logical,
    unit_stats,
    upscale,
)
from .lua_pixel import emit_pixel_baseline, emit_pixel_compact


def _data_hash(records: object, palette: object) -> str:
    payload = json.dumps(
        {"palette": palette, "records": records}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def run(
    input_path: str,
    out_dir: str,
    grid: str = "auto",
    tau: int = 0,
    max_colors: int = 0,
    max_controls: int = 0,
    trim: bool = False,
    alpha_threshold: int = 0,
    merge_strategy: str = "both",
    emit: str = "json",
    out_name: str = "pixel",
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
) -> Dict[str, object]:
    os.makedirs(out_dir, exist_ok=True)
    source = apply_alpha_threshold(load_raster(input_path), alpha_threshold)
    source_sha = sha256_file(input_path)

    # 1) 逻辑网格
    if grid == "native":
        cell_info = {"cell": 1, "consistency": 1.0, "evidence": "调用方指定 native", "candidates": []}
    elif grid == "auto":
        cell_info = infer_cell(source)
    elif "x" in grid.lower():
        gw, gh = (int(v) for v in grid.lower().split("x", 1))
        if source.width % gw or source.height % gh:
            raise ValueError("指定的逻辑网格 %s 不能整除参考图 %dx%d" % (grid, source.width, source.height))
        cell = source.width // gw
        if source.height // gh != cell:
            raise ValueError("指定的逻辑网格不是等比单元")
        cell_info = {"cell": cell, "consistency": None, "evidence": "调用方指定逻辑网格", "candidates": []}
    else:
        cell = int(grid)
        if source.width % cell or source.height % cell:
            raise ValueError("指定的单元尺寸 %d 不能整除参考图" % cell)
        cell_info = {
            "cell": cell,
            "consistency": None,
            "evidence": "调用方指定单元尺寸",
            "candidates": [],
        }

    cell = int(cell_info["cell"])
    logical = to_logical(source, cell)

    # 1b) 裁剪到非透明内容边界
    origin = (0, 0)
    trimmed = False
    if trim:
        box = logical.content_bounds()
        if box:
            x0, y0, x1, y1 = box
            if x0 or y0 or x1 != logical.width or y1 != logical.height:
                logical = logical.crop(x0, y0, x1 - x0, y1 - y0)
                origin = (x0, y0)
                trimmed = True
    grid_w, grid_h = logical.width, logical.height

    # 2) 调色板
    pal = build_palette(logical, tau=tau, max_colors=max_colors)
    palette = pal["palette"]  # type: ignore[assignment]
    idx = pal["index"]  # type: ignore[assignment]
    t_to_q = compare_by_palette(logical, idx, palette)  # type: ignore[arg-type]

    # 全透明像素不需要控件：从调色板里去掉，索引置 -1
    palette, idx = drop_transparent(palette, idx)  # type: ignore[arg-type]

    # 3) 矩形拟合
    rects = rects_by_color(idx, grid_w, grid_h, len(palette), strategy=merge_strategy)  # type: ignore[arg-type]
    records = records_from_rects(rects)
    stats = unit_stats(records)

    # 4) 三层误差
    if cell > 1:
        window = source.crop(origin[0] * cell, origin[1] * cell, grid_w * cell, grid_h * cell)
        o_to_t = verify.compare(upscale(logical, cell).buf, window.buf, window.width, window.height)
    else:
        o_to_t = {"different_pixels": 0, "maximum_channel_error": 0, "mae": 0.0, "rmse": 0.0}

    rebuilt = rasterize_records(records, palette, grid_w, grid_h)  # type: ignore[arg-type]
    q_to_grid = verify.compare(rebuilt.buf, logical.buf, grid_w, grid_h)

    options = {
        "image_prefab_id": image_prefab_id,
        "image_source": image_source,
        "image_type": image_type,
        "square_asset_id": square_asset_id,
        "tintable": tintable,
        "base_pixel_size": base_pixel_size,
        "canvas_margin": canvas_margin,
        "lua_version": lua_version,
    }
    plan: Dict[str, object] = {
        "source": os.path.abspath(input_path),
        "source_sha256": source_sha,
        "source_width": source.width,
        "source_height": source.height,
        "grid": (grid_w, grid_h),
        "grid_evidence": cell_info["evidence"],
        "cell": cell,
        "origin": origin,
        "canvas": (grid_w, grid_h),
        "trimmed": trimmed,
        "palette": palette,
        "records": records,
        "stats": dict(stats, max_controls=max_controls),
        "options": options,
        "data_hash": _data_hash(records, palette),
    }

    outputs: Dict[str, str] = {}
    emitted: List[str] = []
    data_path = os.path.join(out_dir, "%s.json" % out_name)
    report_path = os.path.join(out_dir, "%s.report.json" % out_name)

    # 5) JSON 数据（默认产物）
    json_round_trip: Dict[str, object] = {"checked": False}
    data_bytes = 0
    if emit in ("json", "both"):
        payload = emit_json.pixel_data(plan)
        data_bytes = emit_json.write_json(data_path, payload, json_indent)
        outputs["data_json"] = data_path
        with open(data_path, "r", encoding="utf-8") as f:
            reloaded = json.load(f)
        decoded = verify.decode_pixel_data(reloaded)
        rt_buf, rw, rh = verify.rasterize_pixel(decoded, (grid_w, grid_h))
        json_round_trip = {
            "checked": True,
            "records_diff": verify.multiset_diff(decoded["records"], records),  # type: ignore[arg-type]
            "pixels": verify.compare(rt_buf, rebuilt.buf, rw, rh),
            "palette_size": len(decoded["palette"]),  # type: ignore[arg-type]
        }
        emitted.append("json")

    # 6) Lua（可选）
    lua_round_trip: Dict[str, object] = {"checked": False}
    if emit in ("lua", "both"):
        baseline_lua = emit_pixel_baseline(plan)
        compact_lua = emit_pixel_compact(plan)
        baseline_path = os.path.join(out_dir, "%s.lua" % lua_name)
        compact_path = os.path.join(out_dir, "%s.compact.lua" % lua_name)
        with open(baseline_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(baseline_lua)
        with open(compact_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(compact_lua)
        outputs["baseline_lua"] = baseline_path
        outputs["compact_lua"] = compact_path
        decoded_lua = verify.decode_pixel_lua(compact_lua)
        rt_buf2, rw2, rh2 = verify.rasterize_pixel(decoded_lua, (grid_w, grid_h))
        lua_round_trip = {
            "checked": True,
            "records_diff": verify.multiset_diff(decoded_lua["records"], records),  # type: ignore[arg-type]
            "pixels": verify.compare(rt_buf2, rebuilt.buf, rw2, rh2),
            "cursor_at_end": decoded_lua["cursor_at_end"],
            "lua_static": verify.lua_static_check(compact_lua),
            "baseline_bytes": len(baseline_lua.encode("utf-8")),
            "compact_bytes": len(compact_lua.encode("utf-8")),
            "recommended": "compact"
            if len(compact_lua.encode("utf-8")) < len(baseline_lua.encode("utf-8"))
            else "baseline",
        }
        emitted.append("lua")

    # 7) 预览图
    if preview:
        scale = preview_scale if preview_scale > 0 else max(1, min(8, 512 // max(1, max(grid_w, grid_h))))
        preview_path = os.path.join(out_dir, "%s.preview.png" % out_name)
        upscale(rebuilt, scale).to_png(preview_path)
        outputs["preview_png"] = preview_path

    over_budget = bool(max_controls and stats["records"] > max_controls)
    json_ok = (
        json_round_trip.get("records_diff", 0) == 0
        and json_round_trip.get("pixels", {}).get("different_pixels", 0) == 0  # type: ignore[union-attr]
    )
    lua_ok = True
    if lua_round_trip.get("checked"):
        lua_ok = bool(
            lua_round_trip.get("records_diff") == 0  # type: ignore[union-attr]
            and lua_round_trip.get("pixels", {}).get("different_pixels") == 0  # type: ignore[union-attr]
            and lua_round_trip.get("cursor_at_end") is True  # type: ignore[union-attr]
            and lua_round_trip.get("lua_static", {}).get("bracket_balanced") is True  # type: ignore[union-attr]
        )
    ok = (
        q_to_grid["different_pixels"] == 0
        and q_to_grid["maximum_channel_error"] == 0
        and json_ok
        and lua_ok
        and not over_budget
    )

    report: Dict[str, object] = {
        "tool": "qx2d pixel",
        "ok": ok,
        "input": {
            "path": os.path.abspath(input_path),
            "sha256": source_sha,
            "width": source.width,
            "height": source.height,
            "distinct_colors": len(color_counts(source)),
            "alpha_threshold": alpha_threshold,
        },
        "parameters": {
            "grid": grid,
            "tau": tau,
            "max_colors": max_colors,
            "max_controls": max_controls,
            "trim": trim,
            "merge_strategy": merge_strategy,
            "emit": emit,
            "lua_version": lua_version,
        },
        "grid": {
            "cell": cell,
            "logical": [grid_w, grid_h],
            "trimmed": trimmed,
            "origin": [origin[0], origin[1]],
            "consistency": cell_info["consistency"],
            "evidence": cell_info["evidence"],
            "candidates": cell_info["candidates"],
        },
        "palette": {
            "size": len(palette),
            "source_colors": pal["source_colors"],
            "merged_colors": pal["merged_colors"],
            "tau": tau,
            "lossy": pal["lossy"],
        },
        "controls": dict(stats, max_controls=max_controls, over_budget=over_budget),
        "errors": {"O_to_T": o_to_t, "T_to_Q": t_to_q, "Q_to_grid": q_to_grid},
        "verify": {"json_round_trip": json_round_trip, "lua_round_trip": lua_round_trip},
        "bytes": {"data_json": data_bytes},
        "emitted": emitted,
        "files": dict(outputs, report=report_path),
        "runtime_verified": "pending",
        "notes": [
            "默认产物是 JSON 数据；需要 Lua 时加 --emit lua 或 --emit both。",
            "O_to_T 是采样误差，T_to_Q 是颜色策略误差，Q_to_grid 必须为 0。",
            "JSON / Lua 都经过独立解码器 round-trip 校验；游戏内运行仍需在目标环境确认。",
            "Lua 里的 CONFIG 常量必须按本项目 annotations.lua / readme.md 替换。",
        ],
    }

    emit_json.write_json(report_path, report, json_indent)
    return report
