"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional

from . import __version__
from . import anim as anim_tool
from . import frames as frames_tool
from . import pixel as pixel_tool
from . import shapes as shapes_tool


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--input", required=True, help="参考图 / 帧目录 / 多帧文件路径")
    p.add_argument("--out", required=True, help="输出目录（不存在会创建）")
    p.add_argument(
        "--grid",
        default="auto",
        help="逻辑网格：auto（默认，自动推断整数倍像素单元）| native（原始分辨率）| 单元尺寸如 8 | 逻辑尺寸如 16x16",
    )
    p.add_argument("--tau", type=int, default=0, help="允许的逐通道色差阈值，默认 0（不合并颜色）")
    p.add_argument("--max-colors", type=int, default=0, help="调色板上限，0 表示不限制（有损，需用户明确同意）")
    p.add_argument(
        "--alpha-threshold",
        type=int,
        default=0,
        help="alpha <= N 的像素按透明处理，默认 0（只处理完全透明）；抠图杂边可设 8~128",
    )
    p.add_argument(
        "--merge-strategy",
        choices=["greedy", "runs", "both"],
        default="both",
        help="矩形分解策略：greedy 记录更少 / runs 更快 / both 取更少者（默认）",
    )
    p.add_argument(
        "--emit",
        choices=["json", "lua", "both"],
        default="json",
        help="产物格式：json 数据（默认）| lua 脚本 | both",
    )
    p.add_argument("--out-name", default="", help="输出基名，默认 pixel（像素画）/ anim（帧动画）")
    p.add_argument("--lua-name", default="levelScript", help="Lua 产物的基名，默认 levelScript")
    p.add_argument("--preview", dest="preview", action="store_true", default=True, help="生成预览图（默认）")
    p.add_argument("--no-preview", dest="preview", action="store_false", help="不生成预览图")
    p.add_argument("--preview-scale", type=int, default=0, help="预览图放大倍数，0 = 自动")
    p.add_argument("--json-indent", type=int, default=1, help="JSON 缩进，0 表示单行紧凑输出")
    p.add_argument("--lua-version", choices=["5.1", "5.3"], default="5.3", help="目标 Lua 版本，影响整除写法")
    p.add_argument("--image-prefab-id", type=int, default=0, help="CONFIG：图片控件 prefab ID")
    p.add_argument("--image-source", type=int, default=0, help="CONFIG：Enum.ImageSource")
    p.add_argument("--image-type", type=int, default=0, help="CONFIG：Enum.ImageType")
    p.add_argument("--square-asset-id", type=int, default=0, help="CONFIG：方形基础图元 assetID")
    p.add_argument("--no-tintable", action="store_true", help="CONFIG：该图元不支持染色")
    p.add_argument("--base-pixel-size", type=float, default=8, help="CONFIG：基准下每个逻辑像素的像素尺寸")
    p.add_argument("--canvas-margin", type=float, default=0.9, help="CONFIG：画布留白系数")
    p.add_argument("--json", action="store_true", help="把完整报告打印到 stdout")
    p.add_argument("--quiet", action="store_true", help="不打印摘要，只用退出码表示结果")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qx2d", description="千星奇域像素画 / 帧动画工具")
    parser.add_argument("--version", action="version", version="qx2d %s" % __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pixel", help="参考图 → JSON 数据 / Lua（图片控件拼图）")
    _add_common(p)
    p.add_argument("--max-controls", type=int, default=0, help="控件数上限，超出时报告 ok=false（退出码 2）")
    p.add_argument(
        "--trim",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="裁掉四周完全透明的边（默认不裁）",
    )

    f = sub.add_parser("frames", help="帧序列 → JSON 数据 / Lua（控件池帧动画）")
    _add_common(f)
    f.add_argument("--fps", type=int, default=0, help="目标帧率，0 表示按来源逐帧时长或默认 12")
    f.add_argument("--loop", dest="loop", action="store_true", default=True, help="循环播放（默认）")
    f.add_argument("--no-loop", dest="loop", action="store_false", help="只播一次")
    f.add_argument("--start", type=int, default=1, help="从第几帧开始（1 基准，默认 1）")
    f.add_argument("--end", type=int, default=0, help="到第几帧结束（含，0 表示最后一帧）")
    f.add_argument("--stride", type=int, default=1, help="每隔几帧取一帧（默认 1）")
    f.add_argument("--max-frames", type=int, default=0, help="最多使用多少帧（0 表示不限制）")
    f.add_argument(
        "--duration-ms",
        default="",
        help='逐帧时长：单个值（如 "80"）或逗号分隔（如 "80,80,120"），覆盖来源时长与 --fps',
    )
    f.add_argument("--pingpong", action="store_true", help="往返播放：自动追加反向帧")
    f.add_argument("--merge-identical", action="store_true", help="合并连续相同帧并把时长累加")
    f.add_argument(
        "--trim",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="按所有帧的联合内容边界裁剪画布（默认裁）",
    )
    f.add_argument("--create-batch", type=int, default=60, help="启动预热时每个 tick 创建的控件数")

    s = sub.add_parser("shapes", help="图元拟合：用矩形 / 椭圆 / 三角形拟合一张图片")
    _add_common(s)
    s.add_argument("--num-shapes", type=int, default=200, help="要拟合的图元数量")
    s.add_argument("--candidates", type=int, default=16, help="每轮候选图元数量")
    s.add_argument("--climb", type=int, default=32, help="每个候选的爬山微调次数")
    s.add_argument("--kinds", default="rect,ellipse,triangle", help="允许的图元类型，逗号分隔")
    s.add_argument("--no-rotation", dest="allow_rotation", action="store_false", default=True, help="禁止图元旋转")
    s.add_argument("--min-size", type=float, default=0.0, help="图元最小边长（像素），0 = 自动")
    s.add_argument("--max-size", type=float, default=0.0, help="图元最大边长（像素），0 = 自动")
    s.add_argument("--spill-penalty", type=float, default=4.0, help="图元溢出内容区域的惩罚系数")
    s.add_argument("--alpha-min", type=float, default=0.15, help="图元透明度下限")
    s.add_argument("--alpha-max", type=float, default=1.0, help="图元透明度上限")
    s.add_argument("--fit-scale", type=float, default=1.0, help="在缩放后的分辨率上拟合（<1 更快，结果按源尺寸输出）")
    s.add_argument("--color-bits", type=int, default=8, help="调色板量化位深，越小颜色越少（8 = 原样）")
    s.add_argument("--min-psnr", type=float, default=0.0, help="PSNR 门槛，低于它则报告 ok=false")
    s.add_argument("--seed", type=int, default=12345, help="随机种子（保证可复现）")
    s.add_argument("--rect-asset-id", type=int, default=0, help="CONFIG：矩形图元 assetID")
    s.add_argument("--ellipse-asset-id", type=int, default=0, help="CONFIG：椭圆图元 assetID")
    s.add_argument("--triangle-asset-id", type=int, default=0, help="CONFIG：三角形图元 assetID")

    a = sub.add_parser("anim", help="动画编辑：关键帧 + 缓动 + 时间轴操作 → 采样成动画")
    _add_common(a)
    a.add_argument("--speed", type=float, default=1.0, help="整体速度倍率（2 = 快一倍）")
    a.add_argument("--start-ms", type=float, default=0.0, help="裁剪起点（毫秒）")
    a.add_argument("--end-ms", type=float, default=0.0, help="裁剪终点（毫秒，0 = 到结尾）")
    a.add_argument("--reverse", action="store_true", help="时间反向")
    a.add_argument("--ease", default="", help="把全部关键帧的缓动统一改成这个（如 OutCubic）")
    a.add_argument("--fps", type=int, default=0, help="采样帧率，0 = 用文档里的设置")
    a.add_argument("--duration-ms", type=int, default=0, help="整体时长重定标（毫秒）")
    a.add_argument("--offset-x", type=float, default=0.0, help="整体平移 X（画布像素）")
    a.add_argument("--offset-y", type=float, default=0.0, help="整体平移 Y（画布像素）")
    a.add_argument("--scale", type=float, default=1.0, help="整体缩放（以画布中心为基准）")
    a.add_argument("--rotate", type=float, default=0.0, help="整体旋转角度（度，顺时针为正）")
    a.add_argument("--repeat", type=int, default=1, help="整段时间轴重复次数")
    a.add_argument("--create-batch", type=int, default=60, help="启动预热时每个 tick 创建的控件数")

    return parser


def _common_kwargs(args: argparse.Namespace) -> Dict[str, object]:
    defaults = {"pixel": "pixel", "frames": "anim", "shapes": "shapes", "anim": "anim"}
    out_name = args.out_name or defaults.get(args.command, args.command)
    return {
        "input_path": args.input,
        "out_dir": args.out,
        "grid": args.grid,
        "tau": args.tau,
        "max_colors": args.max_colors,
        "alpha_threshold": args.alpha_threshold,
        "merge_strategy": args.merge_strategy,
        "emit": args.emit,
        "out_name": out_name,
        "lua_name": args.lua_name,
        "preview": args.preview,
        "preview_scale": args.preview_scale,
        "json_indent": args.json_indent,
        "lua_version": args.lua_version,
        "trim": bool(getattr(args, "trim", False)),
        "image_prefab_id": args.image_prefab_id,
        "image_source": args.image_source,
        "image_type": args.image_type,
        "square_asset_id": args.square_asset_id,
        "tintable": not args.no_tintable,
        "base_pixel_size": args.base_pixel_size,
        "canvas_margin": args.canvas_margin,
    }


def summarize(report: Dict[str, object]) -> Dict[str, object]:
    """给调用方（AI / 脚本）看的精简结论。"""
    base = {
        "ok": report["ok"],
        "tool": report["tool"],
        "emitted": report["emitted"],
        "grid": report["grid"],
        "palette_size": report["palette"]["size"],  # type: ignore[index]
        "errors": report["errors"],
        "verify": report["verify"],
        "files": report["files"],
        "runtime_verified": report["runtime_verified"],
    }
    if report.get("tool") == "qx2d pixel":
        base["controls"] = report["controls"]
    elif report.get("tool") == "qx2d shapes":
        base["fit"] = report["fit"]
    elif report.get("tool") == "qx2d anim":
        base["timeline"] = report["timeline"]
    else:
        base["timeline"] = report["timeline"]
        base["cost"] = report["cost"]
    return base


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    kwargs = _common_kwargs(args)
    try:
        if args.command == "pixel":
            report = pixel_tool.run(max_controls=args.max_controls, **kwargs)  # type: ignore[arg-type]
        elif args.command == "frames":
            report = frames_tool.run(  # type: ignore[arg-type]
                fps=args.fps,
                loop=args.loop,
                start=args.start,
                end=args.end,
                stride=args.stride,
                max_frames=args.max_frames,
                duration_ms=args.duration_ms,
                pingpong=args.pingpong,
                merge_identical=args.merge_identical,
                create_batch=args.create_batch,
                **kwargs,
            )
        elif args.command == "shapes":
            for unused in ("grid", "tau", "max_colors", "merge_strategy", "trim", "square_asset_id"):
                kwargs.pop(unused, None)
            report = shapes_tool.run(  # type: ignore[arg-type]
                num_shapes=args.num_shapes,
                candidates=args.candidates,
                climb=args.climb,
                kinds=args.kinds,
                allow_rotation=args.allow_rotation,
                min_size=args.min_size,
                max_size=args.max_size,
                spill_penalty=args.spill_penalty,
                alpha_min=args.alpha_min,
                alpha_max=args.alpha_max,
                fit_scale=args.fit_scale,
                color_bits=args.color_bits,
                min_psnr=args.min_psnr,
                seed=args.seed,
                rect_asset_id=args.rect_asset_id,
                ellipse_asset_id=args.ellipse_asset_id,
                triangle_asset_id=args.triangle_asset_id,
                **kwargs,
            )
        else:
            for unused in ("grid", "tau", "max_colors", "merge_strategy", "trim", "alpha_threshold"):
                kwargs.pop(unused, None)
            report = anim_tool.run(  # type: ignore[arg-type]
                speed=args.speed,
                start_ms=args.start_ms,
                end_ms=args.end_ms,
                reverse=args.reverse,
                ease=args.ease,
                fps=args.fps,
                duration_ms=args.duration_ms,
                offset_x=args.offset_x,
                offset_y=args.offset_y,
                scale=args.scale,
                rotate=args.rotate,
                repeat=args.repeat,
                create_batch=args.create_batch,
                **kwargs,
            )
    except Exception as exc:  # 输入或参数问题
        if not args.quiet:
            print(json.dumps({"ok": False, "tool": "qx2d %s" % args.command, "error": str(exc)}, ensure_ascii=False))
        return 3

    if not args.quiet:
        payload = report if args.json else summarize(report)
        print(json.dumps(payload, ensure_ascii=False))
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
