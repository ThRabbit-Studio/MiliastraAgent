"""qx2d.frames —— 帧动画重建：帧序列 → 控件池时间轴 → JSON 数据（默认）/ Lua + 独立校验。"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

from . import emit_json, verify
from .core import (
    RGBA,
    apply_alpha_threshold,
    color_counts,
    compare_by_palette,
    drop_transparent,
    encode_uint,
    index_of_palette,
    infer_cell,
    load_frames,
    palette_from_counts,
    rasterize_records,
    records_from_rects,
    rects_by_color,
    sha256_file,
    to_logical,
    upscale,
)
from .lua_frames import emit_frames_lua, encode_frame_stream


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _dir_sha(path: str, names: Sequence[str]) -> str:
    h = hashlib.sha256()
    for name in names:
        h.update(name.encode("utf-8"))
        h.update(sha256_file(os.path.join(path, name)).encode("ascii"))
    return h.hexdigest()


def _match_order(prev: Sequence[Tuple[int, int, int, int]], cur: List[Dict[str, int]]) -> List[Dict[str, int]]:
    """按上一帧槽位做贪心最小代价配对，决定本帧矩形顺序（提升播放期缓存命中率）。"""
    pairs: List[Tuple[int, int, int]] = []
    for i, r in enumerate(cur):
        for j, p in enumerate(prev):
            cost = (
                abs(r["x"] - p[0])
                + abs(r["y"] - p[1])
                + 2 * (abs(r["width"] - p[2]) + abs(r["height"] - p[3]))
            )
            pairs.append((cost, i, j))
    pairs.sort()
    used_i, used_j = set(), set()
    matched: Dict[int, int] = {}
    for _cost, i, j in pairs:
        if i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        matched[i] = j
    big = 10 ** 9
    order = sorted(range(len(cur)), key=lambda i: (matched.get(i, big), cur[i]["y"], cur[i]["x"]))
    return [cur[i] for i in order]


def _parse_durations(spec: str, count: int) -> Optional[List[int]]:
    """解析 --duration-ms：单个值或逗号分隔列表。"""
    spec = (spec or "").strip()
    if not spec:
        return None
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    values = [int(p) for p in parts]
    if len(values) == 1:
        return values * count
    if len(values) != count:
        raise ValueError("--duration-ms 给了 %d 个值，但最终帧数是 %d" % (len(values), count))
    return values


def run(
    input_path: str,
    out_dir: str,
    grid: str = "auto",
    tau: int = 0,
    max_colors: int = 0,
    fps: int = 0,
    loop: bool = True,
    start: int = 1,
    end: int = 0,
    stride: int = 1,
    max_frames: int = 0,
    duration_ms: str = "",
    pingpong: bool = False,
    merge_identical: bool = False,
    trim: bool = True,
    alpha_threshold: int = 0,
    merge_strategy: str = "both",
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
    frames, src_durations, meta = load_frames(input_path)
    if not frames:
        raise ValueError("没有读到任何帧")
    frames = [apply_alpha_threshold(f, alpha_threshold) for f in frames]
    src_durations = list(src_durations) if src_durations else [0] * len(frames)
    input_frame_count = len(frames)

    source_sha = (
        sha256_file(input_path)
        if os.path.isfile(input_path)
        else _dir_sha(input_path, list(meta.get("files") or []))
    )

    # 0) 选帧：--start / --end / --stride / --max-frames
    total = len(frames)
    if start < 1:
        start = 1
    last = total if (not end or end > total) else end
    if last < start:
        raise ValueError("--end(%d) 小于 --start(%d)" % (end, start))
    picked = list(range(start - 1, last, max(1, stride)))
    if max_frames and len(picked) > max_frames:
        picked = picked[:max_frames]
    frames = [frames[i] for i in picked]
    src_durations = [src_durations[i] for i in picked]

    w0, h0 = frames[0].width, frames[0].height
    for i, f in enumerate(frames):
        if f.width != w0 or f.height != h0:
            raise ValueError("第 %d 帧尺寸 %dx%d 与第 1 帧 %dx%d 不一致" % (i + 1, f.width, f.height, w0, h0))

    # 1) 逻辑网格（以第一帧推断，全动画共用）
    if grid == "native":
        cell_info = {"cell": 1, "consistency": 1.0, "evidence": "调用方指定 native", "candidates": []}
    elif grid == "auto":
        cell_info = infer_cell(frames[0])
    elif "x" in grid.lower():
        gw, gh = (int(v) for v in grid.lower().split("x", 1))
        if w0 % gw or h0 % gh or (w0 // gw) != (h0 // gh):
            raise ValueError("指定的逻辑网格 %s 与帧尺寸 %dx%d 不符" % (grid, w0, h0))
        cell_info = {"cell": w0 // gw, "consistency": None, "evidence": "调用方指定逻辑网格", "candidates": []}
    else:
        cell = int(grid)
        if w0 % cell or h0 % cell:
            raise ValueError("指定的单元尺寸 %d 不能整除帧尺寸" % cell)
        cell_info = {"cell": cell, "consistency": None, "evidence": "调用方指定单元尺寸", "candidates": []}

    cell = int(cell_info["cell"])
    logical = [to_logical(f, cell) for f in frames]
    gw, gh = logical[0].width, logical[0].height

    # 2) 合并连续相同帧（时长累加）
    merged_frames = 0
    if merge_identical and len(logical) > 1:
        kept_frames = [logical[0]]
        kept_durations = [src_durations[0]]
        for lf, d in zip(logical[1:], src_durations[1:]):
            if lf.buf == kept_frames[-1].buf:
                kept_durations[-1] += d
                merged_frames += 1
            else:
                kept_frames.append(lf)
                kept_durations.append(d)
        logical = kept_frames
        src_durations = kept_durations

    # 3) 往返播放：追加反向帧（去掉首尾，避免重复停在端点）
    if pingpong and len(logical) > 2:
        logical = logical + logical[-2:0:-1]
        src_durations = src_durations + src_durations[-2:0:-1]

    # 4) 时长与帧率
    explicit = _parse_durations(duration_ms, len(logical))
    if explicit is not None:
        durations_ms = explicit
        duration_source = "调用方指定的逐帧时长"
    elif fps:
        durations_ms = [int(round(1000.0 / fps))] * len(logical)
        duration_source = "调用方指定的帧率"
    elif all(d > 0 for d in src_durations):
        durations_ms = [int(d) for d in src_durations]
        duration_source = "来源文件的逐帧时长"
    else:
        durations_ms = [int(round(1000.0 / 12))] * len(logical)
        duration_source = "默认 12 FPS"
    ordered = sorted(durations_ms)
    effective_fps = max(1, int(round(1000.0 / max(1, ordered[len(ordered) // 2]))))

    # 5) 全动画共享调色板
    counts: Dict[RGBA, int] = {}
    for lf in logical:
        for color, n in color_counts(lf).items():
            counts[color] = counts.get(color, 0) + n
    pal = palette_from_counts(counts, tau=tau, max_colors=max_colors)
    palette: List[RGBA] = pal["palette"]  # type: ignore[assignment]
    indexes = [index_of_palette(lf, pal["mapping"], palette) for lf in logical]  # type: ignore[arg-type]

    color_error = {"different_pixels": 0, "maximum_channel_error": 0, "mae": 0.0, "rmse": 0.0}
    for lf, idx in zip(logical, indexes):
        e = compare_by_palette(lf, idx, palette)
        color_error["different_pixels"] += e["different_pixels"]
        color_error["maximum_channel_error"] = max(color_error["maximum_channel_error"], e["maximum_channel_error"])
        color_error["mae"] += e["mae"]
        color_error["rmse"] += e["rmse"]
    n_frames = max(1, len(logical))
    color_error["mae"] = round(color_error["mae"] / n_frames, 6)
    color_error["rmse"] = round(color_error["rmse"] / n_frames, 6)

    # 全透明像素不需要控件
    dropped_palette: Optional[List[RGBA]] = None
    new_indexes: List[List[int]] = []
    for idx in indexes:
        p2, i2 = drop_transparent(palette, idx)
        dropped_palette = p2
        new_indexes.append(i2)
    if dropped_palette is not None:
        palette = dropped_palette
    indexes = new_indexes

    # 6) 逐帧矩形拟合
    frame_rects: List[List[Dict[str, int]]] = []
    for idx in indexes:
        rects = rects_by_color(idx, gw, gh, len(palette), strategy=merge_strategy)
        frame_rects.append(records_from_rects(rects))
    if not any(frame_rects):
        raise ValueError("所有帧都是全透明画面，没有可绘制内容")

    # 7) 画布与联合内容边界
    if trim:
        x0 = min(r["x"] for fr in frame_rects for r in fr)
        y0 = min(r["y"] for fr in frame_rects for r in fr)
        x1 = max(r["x"] + r["width"] for fr in frame_rects for r in fr)
        y1 = max(r["y"] + r["height"] for fr in frame_rects for r in fr)
        origin = (x0, y0)
        local_w, local_h = x1 - x0, y1 - y0
    else:
        origin = (0, 0)
        local_w, local_h = gw, gh

    # 8) 颜色分组、池大小与槽位顺序
    colors = sorted({r["colorIndex"] for fr in frame_rects for r in fr})
    group_pool: Dict[int, int] = {c: 0 for c in colors}
    for fr in frame_rects:
        per: Dict[int, int] = {}
        for r in fr:
            per[r["colorIndex"]] = per.get(r["colorIndex"], 0) + 1
        for c, n in per.items():
            group_pool[c] = max(group_pool[c], n)
    groups = []
    base = 0
    for c in colors:
        groups.append({"colorIndex": c, "pool": group_pool[c], "base": base})
        base += group_pool[c]
    pool_size = base
    base_of = {g["colorIndex"]: g["base"] for g in groups}
    pool_of = {g["colorIndex"]: g["pool"] for g in groups}

    prev_state: Dict[int, List[Tuple[int, int, int, int]]] = {}
    timeline: List[List[Dict[str, int]]] = []
    for fr in frame_rects:
        by_color: Dict[int, List[Dict[str, int]]] = {}
        for r in fr:
            by_color.setdefault(r["colorIndex"], []).append(r)
        slots: List[Dict[str, int]] = []
        for c in colors:
            items = by_color.get(c, [])
            if not items:
                prev_state[c] = []
                continue
            if c in prev_state and prev_state[c]:
                order = _match_order(prev_state[c], items)
            else:
                order = sorted(items, key=lambda r: (r["y"], r["x"]))
            if len(order) > pool_of[c]:
                raise ValueError("颜色组槽位不足")
            prev_state[c] = [(r["x"], r["y"], r["width"], r["height"]) for r in order]
            for i, r in enumerate(order):
                slots.append(
                    {
                        "slot": base_of[c] + i,
                        "colorIndex": c,
                        "x": r["x"],
                        "y": r["y"],
                        "width": r["width"],
                        "height": r["height"],
                    }
                )
        timeline.append(slots)

    sizes = [(r["width"], r["height"]) for fr in timeline for r in fr]
    size_dict: List[Tuple[int, int]] = []
    for s in sizes:
        if s not in size_dict:
            size_dict.append(s)

    cost = _simulate_cost(timeline, pool_size, origin, local_w, local_h)
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
        "source": os.path.abspath(input_path),
        "source_sha256": source_sha,
        "source_frames": input_frame_count,
        "source_width": w0,
        "source_height": h0,
        "grid": (gw, gh),
        "grid_evidence": cell_info["evidence"],
        "cell": cell,
        "origin": origin,
        "local": (local_w, local_h),
        "trimmed": bool(trim),
        "palette": palette,
        "sizes": size_dict,
        "groups": groups,
        "pool_size": pool_size,
        "frames": timeline,
        "durations_ms": durations_ms,
        "fps": effective_fps,
        "loop": loop,
        "options": options,
        "stats": {
            "pool_size": pool_size,
            "peak_visible_controls": max(len(fr) for fr in timeline),
            "frame_count": len(timeline),
            "merged_identical_frames": merged_frames,
            "size_dict": len(size_dict),
        },
        "data_hash": _hash_payload(
            {
                "palette": palette,
                "groups": groups,
                "origin": origin,
                "local": (local_w, local_h),
                "frames": timeline,
                "durations_ms": durations_ms,
                "loop": loop,
            }
        ),
    }

    outputs: Dict[str, str] = {}
    emitted: List[str] = []
    data_path = os.path.join(out_dir, "%s.json" % out_name)
    report_path = os.path.join(out_dir, "%s.report.json" % out_name)

    # 9) JSON 数据（默认产物）
    json_round_trip: Dict[str, object] = {"checked": False}
    data_bytes = 0
    if emit in ("json", "both"):
        payload = emit_json.frames_data(plan)
        data_bytes = emit_json.write_json(data_path, payload, json_indent)
        outputs["data_json"] = data_path
        with open(data_path, "r", encoding="utf-8") as f:
            reloaded = json.load(f)
        decoded_json = verify.decode_frames_data(reloaded)
        pixel_diffs = 0
        worst = {"different_pixels": 0, "maximum_channel_error": 0, "mae": 0.0, "rmse": 0.0}
        slot_diff = 0
        for i in range(len(timeline)):
            ref = rasterize_records(frame_rects[i], palette, gw, gh)
            buf = verify.rasterize_frame(decoded_json, i, gw, gh)
            d = verify.compare(buf, ref.buf, gw, gh)
            if d["different_pixels"]:
                pixel_diffs += 1
            for k in ("different_pixels", "maximum_channel_error"):
                worst[k] = max(worst[k], d[k])
            worst["mae"] = max(worst["mae"], d["mae"])
            worst["rmse"] = max(worst["rmse"], d["rmse"])
            slot_diff += verify.multiset_diff(decoded_json["frames"][i], timeline[i])  # type: ignore[index]
        json_round_trip = {
            "checked": True,
            "frames_with_pixel_diff": pixel_diffs,
            "slot_multiset_diff": slot_diff,
            "pixels": worst,
        }
        emitted.append("json")

    # 10) Lua（可选）
    lua_round_trip: Dict[str, object] = {"checked": False}
    if emit in ("lua", "both"):
        size_index = {s: i for i, s in enumerate(size_dict)}
        frame_data = encode_frame_stream(timeline, groups, size_index, origin, local_w, local_h)
        plan["frame_data"] = frame_data
        plan["duration_data"] = "".join(encode_uint(d) for d in durations_ms)
        lua_text = emit_frames_lua(plan)
        lua_path = os.path.join(out_dir, "%s.lua" % lua_name)
        with open(lua_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(lua_text)
        outputs["lua"] = lua_path
        decoded_lua = verify.decode_frames_lua(lua_text)
        pixel_diffs = 0
        worst = {"different_pixels": 0, "maximum_channel_error": 0, "mae": 0.0, "rmse": 0.0}
        slot_diff = 0
        for i in range(len(timeline)):
            ref = rasterize_records(frame_rects[i], palette, gw, gh)
            buf = verify.rasterize_frame(decoded_lua, i, gw, gh)
            d = verify.compare(buf, ref.buf, gw, gh)
            if d["different_pixels"]:
                pixel_diffs += 1
            for k in ("different_pixels", "maximum_channel_error"):
                worst[k] = max(worst[k], d[k])
            worst["mae"] = max(worst["mae"], d["mae"])
            worst["rmse"] = max(worst["rmse"], d["rmse"])
            slot_diff += verify.multiset_diff(decoded_lua["frames"][i], timeline[i])  # type: ignore[index]
        lua_round_trip = {
            "checked": True,
            "frames_with_pixel_diff": pixel_diffs,
            "slot_multiset_diff": slot_diff,
            "pixels": worst,
            "cursor_at_end": decoded_lua["cursor_at_end"],
            "lua_static": verify.lua_static_check(lua_text),
            "lua_bytes": len(lua_text.encode("utf-8")),
            "frame_stream": len(plan["frame_data"]),
        }
        emitted.append("lua")

    # 11) 预览图
    if preview:
        scale = preview_scale if preview_scale > 0 else max(1, min(8, 512 // max(1, max(gw, gh))))
        preview_path = os.path.join(out_dir, "%s.preview.png" % out_name)
        upscale(logical[0], scale).to_png(preview_path)
        outputs["preview_png"] = preview_path

    json_ok = bool(
        json_round_trip.get("frames_with_pixel_diff", 0) == 0
        and json_round_trip.get("slot_multiset_diff", 0) == 0
    )
    lua_ok = True
    if lua_round_trip.get("checked"):
        lua_ok = bool(
            lua_round_trip.get("frames_with_pixel_diff") == 0  # type: ignore[union-attr]
            and lua_round_trip.get("slot_multiset_diff") == 0  # type: ignore[union-attr]
            and lua_round_trip.get("cursor_at_end") is True  # type: ignore[union-attr]
            and lua_round_trip.get("lua_static", {}).get("bracket_balanced") is True  # type: ignore[union-attr]
        )
    ok = json_ok and lua_ok

    report: Dict[str, object] = {
        "tool": "qx2d frames",
        "ok": ok,
        "input": {
            "path": os.path.abspath(input_path),
            "sha256": source_sha,
            "frames": input_frame_count,
            "width": w0,
            "height": h0,
            "kind": meta.get("kind"),
            "distinct_colors": len(counts),
            "alpha_threshold": alpha_threshold,
        },
        "parameters": {
            "grid": grid,
            "tau": tau,
            "max_colors": max_colors,
            "fps": fps,
            "loop": loop,
            "start": start,
            "end": end,
            "stride": stride,
            "max_frames": max_frames,
            "duration_ms": duration_ms,
            "pingpong": pingpong,
            "merge_identical": merge_identical,
            "trim": trim,
            "merge_strategy": merge_strategy,
            "emit": emit,
            "lua_version": lua_version,
        },
        "grid": {
            "cell": cell,
            "logical": [gw, gh],
            "consistency": cell_info["consistency"],
            "evidence": cell_info["evidence"],
        },
        "palette": {
            "size": len(palette),
            "source_colors": pal["source_colors"],
            "merged_colors": pal["merged_colors"],
            "tau": tau,
            "lossy": pal["lossy"],
        },
        "timeline": {
            "fps": effective_fps,
            "duration_source": duration_source,
            "durations_ms": durations_ms,
            "loop": loop,
            "frame_count": len(timeline),
            "input_frames": input_frame_count,
            "merged_identical_frames": merged_frames,
            "pool_size": pool_size,
            "groups": groups,
            "peak_visible_controls": max(len(fr) for fr in timeline),
            "size_dict": len(size_dict),
            "trimmed": bool(trim),
            "local_bounds": [local_w, local_h],
            "origin": [origin[0], origin[1]],
        },
        "cost": cost,
        "errors": {"O_to_T": _sampling_error(frames, logical, cell), "T_to_Q": color_error, "Q_to_grid": json_round_trip.get("pixels", {})},
        "verify": {"json_round_trip": json_round_trip, "lua_round_trip": lua_round_trip},
        "bytes": {"data_json": data_bytes},
        "emitted": emitted,
        "files": dict(outputs, report=report_path),
        "runtime_verified": "pending",
        "notes": [
            "默认产物是 JSON 数据；需要 Lua 时加 --emit lua 或 --emit both。",
            "Q_to_grid 为独立解码后逐帧栅格对比结果，无损任务必须全为 0。",
            "播放期创建/销毁为 0：控件池在启动阶段分批预热，之后只改属性。",
            "父节点承载共同运动的优化未启用（当前格式不需要）。",
        ],
    }

    emit_json.write_json(report_path, report, json_indent)
    return report


def _sampling_error(frames: Sequence[object], logical: Sequence[object], cell: int) -> Dict[str, float]:
    if cell <= 1:
        return {"different_pixels": 0, "maximum_channel_error": 0, "mae": 0.0, "rmse": 0.0}
    out = {"different_pixels": 0, "maximum_channel_error": 0, "mae": 0.0, "rmse": 0.0}
    for src, lf in zip(frames, logical):
        e = verify.compare(upscale(lf, cell).buf, src.buf, src.width, src.height)  # type: ignore[attr-defined]
        out["different_pixels"] += e["different_pixels"]
        out["maximum_channel_error"] = max(out["maximum_channel_error"], e["maximum_channel_error"])
    return out


def _simulate_cost(
    timeline: Sequence[Sequence[Dict[str, int]]],
    pool_size: int,
    origin: Tuple[int, int],
    local_w: int,
    local_h: int,
) -> Dict[str, object]:
    """按播放器的缓存逻辑，估算每帧 setter 调用与显隐切换次数。"""
    cache_visible = [False] * pool_size
    cache_size = [None] * pool_size
    cache_pos = [None] * pool_size
    total_setters = 0
    total_visibility = 0
    peak_dirty = 0
    total_dirty = 0
    for fr in timeline:
        visible = [False] * pool_size
        state: Dict[int, Tuple[int, int, int, int]] = {}
        for r in fr:
            visible[r["slot"]] = True
            state[r["slot"]] = (r["x"], r["y"], r["width"], r["height"])
        dirty = 0
        for slot in range(pool_size):
            if visible[slot] != cache_visible[slot]:
                total_visibility += 1
                dirty += 1
                cache_visible[slot] = visible[slot]
            if visible[slot]:
                x, y, w, h = state[slot]
                px = -local_w / 2 + (x - origin[0] + w / 2)
                py = local_h / 2 - (y - origin[1] + h / 2)
                if cache_size[slot] != (w, h):
                    total_setters += 1
                    dirty += 1
                    cache_size[slot] = (w, h)
                if cache_pos[slot] != (px, py):
                    total_setters += 1
                    dirty += 1
                    cache_pos[slot] = (px, py)
        peak_dirty = max(peak_dirty, dirty)
        total_dirty += dirty
    n = max(1, len(timeline))
    return {
        "startup_created_controls": pool_size,
        "playback_created_controls": 0,
        "playback_destroyed_controls": 0,
        "peak_pool_controls": pool_size,
        "average_dirty_controls_per_frame": round(total_dirty / n, 3),
        "peak_dirty_controls_per_frame": peak_dirty,
        "setter_calls_total": total_setters,
        "visibility_changes_total": total_visibility,
        "note": "按播放器缓存逻辑静态模拟，不含渲染与真实 dt 抖动",
    }
