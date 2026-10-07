"""qx2d MCP 服务（stdio，换行分隔 JSON-RPC 2.0）。

把两个工具直接暴露给支持 MCP 的 AI 客户端：

    pixel_reconstruct  参考图 -> 图片控件拼图 Lua（基线 + 压缩）+ 独立校验报告
    frame_animate      帧序列 -> 控件池帧动画 Lua + 独立校验报告

启动：
    python tools/qx2d_mcp.py

客户端配置（示例）：
    {
      "mcpServers": {
        "qx2d": { "command": "python", "args": ["<绝对路径>/tools/qx2d_mcp.py"] }
      }
    }

设计约束：stdout 只输出协议消息，任何日志都走 stderr。
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qx2d import __version__  # noqa: E402
from qx2d import anim as anim_tool  # noqa: E402
from qx2d import frames as frames_tool  # noqa: E402
from qx2d import pixel as pixel_tool  # noqa: E402
from qx2d import shapes as shapes_tool  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"

_COMMON_PROPS: Dict[str, Any] = {
    "input": {"type": "string", "description": "参考图（pixel）或帧目录 / GIF / WebP 的绝对路径"},
    "out_dir": {"type": "string", "description": "输出目录，不存在会创建"},
    "grid": {
        "type": "string",
        "description": "逻辑网格：auto（默认，自动推断整数倍像素单元）| native | 单元尺寸如 8 | 逻辑尺寸如 16x16",
    },
    "tau": {"type": "integer", "description": "允许的逐通道色差阈值，默认 0（不合并颜色）"},
    "max_colors": {"type": "integer", "description": "调色板上限，0 表示不限制（有损，需用户明确同意）"},
    "alpha_threshold": {"type": "integer", "description": "alpha <= N 按透明处理，默认 0；抠图杂边可设 8~128"},
    "merge_strategy": {"type": "string", "enum": ["greedy", "runs", "both"], "description": "矩形分解策略，默认 both"},
    "emit": {"type": "string", "enum": ["json", "lua", "both"], "description": "产物格式，默认 json 数据"},
    "out_name": {"type": "string", "description": "输出基名，默认 pixel / anim"},
    "lua_name": {"type": "string", "description": "Lua 产物基名，默认 levelScript"},
    "preview": {"type": "boolean", "description": "是否生成预览图，默认 true"},
    "preview_scale": {"type": "integer", "description": "预览图放大倍数，0 = 自动"},
    "json_indent": {"type": "integer", "description": "JSON 缩进，0 = 单行"},
    "lua_version": {"type": "string", "enum": ["5.1", "5.3"], "description": "目标 Lua 版本，默认 5.3"},
    "image_prefab_id": {"type": "integer", "description": "CONFIG：图片控件 prefab ID"},
    "image_source": {"type": "integer", "description": "CONFIG：Enum.ImageSource"},
    "image_type": {"type": "integer", "description": "CONFIG：Enum.ImageType"},
    "square_asset_id": {"type": "integer", "description": "CONFIG：方形基础图元 assetID"},
    "tintable": {"type": "boolean", "description": "CONFIG：该图元是否支持染色"},
    "base_pixel_size": {"type": "number", "description": "CONFIG：基准下每个逻辑像素的像素尺寸"},
    "canvas_margin": {"type": "number", "description": "CONFIG：画布留白系数"},
}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "pixel_reconstruct",
        "description": (
            "把一张参考图还原成千星奇域图片控件拼图：自动推断整数倍像素的逻辑网格，做调色板与同色矩形合并，"
            "默认输出 JSON 数据契约（调色板 + 控件记录 + 画布 + CONFIG），也可 emit=lua 输出 "
            "levelScript.lua（未压缩基线与安全 ASCII 压缩版）。"
            "同时给出 O->T（采样）、T->Q（颜色）、Q->栅格（控件）三层误差与独立 round-trip 校验。"
            "返回 JSON 报告；ok=false 表示校验未通过或超出控件上限。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": dict(
                _COMMON_PROPS,
                max_controls={"type": "integer", "description": "控件数上限，0 表示不限制"},
                trim={"type": "boolean", "description": "裁掉四周完全透明的边，默认 false"},
            ),
            "required": ["input", "out_dir"],
        },
    },
    {
        "name": "frame_animate",
        "description": (
            "把帧目录 / GIF / WebP 还原成千星奇域控件帧动画：全动画共享调色板、按颜色分组的控件池 + 每帧绝对几何，"
            "播放期创建与销毁为 0。默认输出 JSON 数据契约（帧时间轴 + 槽位 + 画布 + CONFIG），"
            "也可 emit=lua 输出带独立解码器的 levelScript.lua 播放器。返回逐帧独立栅格校验报告。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": dict(
                _COMMON_PROPS,
                fps={"type": "integer", "description": "目标帧率，0 表示按来源逐帧时长或默认 12"},
                loop={"type": "boolean", "description": "是否循环播放，默认 true"},
                start={"type": "integer", "description": "起始帧（1 基准），默认 1"},
                end={"type": "integer", "description": "结束帧（含），0 表示最后一帧"},
                stride={"type": "integer", "description": "抽帧间隔，默认 1"},
                max_frames={"type": "integer", "description": "最多使用多少帧，0 表示不限制"},
                duration_ms={"type": "string", "description": '逐帧时长："80" 或 "80,80,120"'},
                pingpong={"type": "boolean", "description": "往返播放，默认 false"},
                merge_identical={"type": "boolean", "description": "合并连续相同帧并累加时长，默认 false"},
                trim={"type": "boolean", "description": "按联合内容边界裁剪画布，默认 true"},
                create_batch={"type": "integer", "description": "启动预热时每个 tick 创建的控件数，默认 60"},
            ),
            "required": ["input", "out_dir"],
        },
    },
    {
        "name": "shape_fit",
        "description": (
            "把一张图片用基础图元（矩形 / 椭圆 / 三角形）迭代拟合出来：按误差采样焦点、生成候选、"
            "加权最小二乘求最优颜色、爬山微调，支持透明软权重与溢出惩罚。"
            "默认输出 JSON 数据契约（图元列表 + 调色板 + 画布），也可 emit=lua 输出绘制脚本。"
            "返回拟合质量（MAE/RMSE/PSNR）与独立栅格化 round-trip 结论。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": dict(
                _COMMON_PROPS,
                num_shapes={"type": "integer", "description": "要拟合的图元数量，默认 200"},
                candidates={"type": "integer", "description": "每轮候选数量，默认 16"},
                climb={"type": "integer", "description": "每个候选的爬山微调次数，默认 32"},
                kinds={"type": "string", "description": "允许的图元类型，逗号分隔，默认 rect,ellipse,triangle"},
                allow_rotation={"type": "boolean", "description": "是否允许旋转，默认 true"},
                min_size={"type": "number", "description": "图元最小边长（像素），0 = 自动"},
                max_size={"type": "number", "description": "图元最大边长（像素），0 = 自动"},
                spill_penalty={"type": "number", "description": "溢出内容区域的惩罚系数，默认 4"},
                alpha_min={"type": "number", "description": "图元透明度下限，默认 0.15"},
                alpha_max={"type": "number", "description": "图元透明度上限，默认 1.0"},
                fit_scale={"type": "number", "description": "在缩放后的分辨率上拟合，默认 1.0"},
                color_bits={"type": "integer", "description": "调色板量化位深，默认 8（原样）"},
                min_psnr={"type": "number", "description": "PSNR 门槛，低于它则 ok=false"},
                seed={"type": "integer", "description": "随机种子，默认 12345"},
                rect_asset_id={"type": "integer", "description": "CONFIG：矩形图元 assetID"},
                ellipse_asset_id={"type": "integer", "description": "CONFIG：椭圆图元 assetID"},
                triangle_asset_id={"type": "integer", "description": "CONFIG：三角形图元 assetID"},
            ),
            "required": ["input", "out_dir"],
        },
    },
    {
        "name": "anim_edit",
        "description": (
            "编辑关键帧动画：读取关键帧文档（qx2d.keyframes），支持整体变速、裁剪区间、反向、"
            "统一缓动、时长重定标、平移 / 缩放 / 旋转、重复，并按帧率采样成逐帧绝对状态，"
            "同时保留事件轨。默认输出 JSON 数据契约；emit=lua 时输出带属性缓存的播放器。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": dict(
                _COMMON_PROPS,
                speed={"type": "number", "description": "整体速度倍率，默认 1"},
                start_ms={"type": "number", "description": "裁剪起点（毫秒）"},
                end_ms={"type": "number", "description": "裁剪终点（毫秒，0 = 到结尾）"},
                reverse={"type": "boolean", "description": "时间反向"},
                ease={"type": "string", "description": "把全部关键帧缓动统一改成这个（如 OutCubic）"},
                fps={"type": "integer", "description": "采样帧率，0 = 用文档设置"},
                duration_ms={"type": "integer", "description": "整体时长重定标（毫秒）"},
                offset_x={"type": "number", "description": "整体平移 X"},
                offset_y={"type": "number", "description": "整体平移 Y"},
                scale={"type": "number", "description": "整体缩放（以画布中心为基准）"},
                rotate={"type": "number", "description": "整体旋转角度（度）"},
                repeat={"type": "integer", "description": "时间轴重复次数，默认 1"},
                create_batch={"type": "integer", "description": "Lua 播放器预热批大小，默认 60"},
            ),
            "required": ["input", "out_dir"],
        },
    },
]


def _shared(args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "grid": args.get("grid", "auto"),
        "tau": int(args.get("tau", 0)),
        "max_colors": int(args.get("max_colors", 0)),
        "alpha_threshold": int(args.get("alpha_threshold", 0)),
        "merge_strategy": args.get("merge_strategy", "both"),
        "emit": args.get("emit", "json"),
        "out_name": args.get("out_name") or "",
        "lua_name": args.get("lua_name", "levelScript"),
        "preview": bool(args.get("preview", True)),
        "preview_scale": int(args.get("preview_scale", 0)),
        "json_indent": int(args.get("json_indent", 1)),
        "lua_version": args.get("lua_version", "5.3"),
        "image_prefab_id": int(args.get("image_prefab_id", 0)),
        "image_source": int(args.get("image_source", 0)),
        "image_type": int(args.get("image_type", 0)),
        "square_asset_id": int(args.get("square_asset_id", 0)),
        "tintable": bool(args.get("tintable", True)),
        "base_pixel_size": float(args.get("base_pixel_size", 8)),
        "canvas_margin": float(args.get("canvas_margin", 0.9)),
    }


def _tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    shared = _shared(args)
    if name == "pixel_reconstruct":
        shared["out_name"] = shared["out_name"] or "pixel"
        report = pixel_tool.run(
            input_path=args["input"],
            out_dir=args["out_dir"],
            max_controls=int(args.get("max_controls", 0)),
            trim=bool(args.get("trim", False)),
            **shared,  # type: ignore[arg-type]
        )
    elif name == "frame_animate":
        shared["out_name"] = shared["out_name"] or "anim"
        report = frames_tool.run(
            input_path=args["input"],
            out_dir=args["out_dir"],
            fps=int(args.get("fps", 0)),
            loop=bool(args.get("loop", True)),
            start=int(args.get("start", 1)),
            end=int(args.get("end", 0)),
            stride=int(args.get("stride", 1)),
            max_frames=int(args.get("max_frames", 0)),
            duration_ms=str(args.get("duration_ms", "")),
            pingpong=bool(args.get("pingpong", False)),
            merge_identical=bool(args.get("merge_identical", False)),
            trim=bool(args.get("trim", True)),
            create_batch=int(args.get("create_batch", 60)),
            **shared,  # type: ignore[arg-type]
        )
    elif name == "shape_fit":
        for unused in ("grid", "tau", "max_colors", "merge_strategy", "trim", "square_asset_id"):
            shared.pop(unused, None)
        shared["out_name"] = shared["out_name"] or "shapes"
        report = shapes_tool.run(
            input_path=args["input"],
            out_dir=args["out_dir"],
            num_shapes=int(args.get("num_shapes", 200)),
            candidates=int(args.get("candidates", 16)),
            climb=int(args.get("climb", 32)),
            kinds=str(args.get("kinds", "rect,ellipse,triangle")),
            allow_rotation=bool(args.get("allow_rotation", True)),
            min_size=float(args.get("min_size", 0.0)),
            max_size=float(args.get("max_size", 0.0)),
            spill_penalty=float(args.get("spill_penalty", 4.0)),
            alpha_min=float(args.get("alpha_min", 0.15)),
            alpha_max=float(args.get("alpha_max", 1.0)),
            fit_scale=float(args.get("fit_scale", 1.0)),
            color_bits=int(args.get("color_bits", 8)),
            min_psnr=float(args.get("min_psnr", 0.0)),
            seed=int(args.get("seed", 12345)),
            rect_asset_id=int(args.get("rect_asset_id", 0)),
            ellipse_asset_id=int(args.get("ellipse_asset_id", 0)),
            triangle_asset_id=int(args.get("triangle_asset_id", 0)),
            **shared,  # type: ignore[arg-type]
        )
    elif name == "anim_edit":
        for unused in ("grid", "tau", "max_colors", "merge_strategy", "trim", "alpha_threshold"):
            shared.pop(unused, None)
        shared["out_name"] = shared["out_name"] or "anim"
        report = anim_tool.run(
            input_path=args["input"],
            out_dir=args["out_dir"],
            speed=float(args.get("speed", 1.0)),
            start_ms=float(args.get("start_ms", 0.0)),
            end_ms=float(args.get("end_ms", 0.0)),
            reverse=bool(args.get("reverse", False)),
            ease=str(args.get("ease", "")),
            fps=int(args.get("fps", 0)),
            duration_ms=int(args.get("duration_ms", 0)),
            offset_x=float(args.get("offset_x", 0.0)),
            offset_y=float(args.get("offset_y", 0.0)),
            scale=float(args.get("scale", 1.0)),
            rotate=float(args.get("rotate", 0.0)),
            repeat=int(args.get("repeat", 1)),
            create_batch=int(args.get("create_batch", 60)),
            **shared,  # type: ignore[arg-type]
        )
    else:
        raise ValueError("未知工具：%s" % name)

    text = json.dumps(report, ensure_ascii=False, indent=1)
    return {"content": [{"type": "text", "text": text}], "isError": not bool(report.get("ok"))}


def handle_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理一条 JSON-RPC 消息；通知类返回 None。"""
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "qx2d", "version": __version__},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            result = _tool_call(name, args)
        except Exception as exc:  # 工具内部错误按 MCP 约定回 isError
            result = {
                "content": [{"type": "text", "text": "工具执行失败：%s" % exc}],
                "isError": True,
            }
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if msg_id is None:
        return None  # 通知
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": "不支持的方法：%s" % method},
    }


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}), flush=True)
            continue
        try:
            response = handle_message(msg)
        except Exception:  # 兜底：任何异常都不能让 stdio 通道静默断掉
            traceback.print_exc(file=sys.stderr)
            response = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": "内部错误"},
            }
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
