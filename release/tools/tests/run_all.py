"""端到端自测：生成素材 → 跑两个工具（含新增参数）→ 断言校验指标 → 冒烟测试 MCP 与 CLI。

    python tools/tests/run_all.py

全部通过退出码 0；任一断言失败退出码 1，并打印失败原因。
"""

from __future__ import annotations

import json
import os
import sys
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)

import make_fixtures  # noqa: E402

from qx2d import cli  # noqa: E402
from qx2d import anim as anim_tool  # noqa: E402
from qx2d import frames as frames_tool  # noqa: E402
from qx2d import pixel as pixel_tool  # noqa: E402
from qx2d import shapes as shapes_tool  # noqa: E402
from qx2d_mcp import handle_message  # noqa: E402

OUT = os.path.join(HERE, "out")
FAILURES: list = []
FIX = make_fixtures.FIXTURES


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print("[%s] %s%s" % (mark, name, ("  -> " + detail) if detail else ""))
    if not condition:
        FAILURES.append("%s %s" % (name, detail))


def out(name: str) -> str:
    return os.path.join(OUT, name)


def main() -> int:
    make_fixtures.main()
    ref = os.path.join(FIX, "ref_pixel.png")
    big = os.path.join(FIX, "ref_big.png")
    frames_dir = os.path.join(FIX, "frames")
    frames_dup = os.path.join(FIX, "frames_dup")
    gif = os.path.join(FIX, "anim.gif")

    # ---------------- 像素画：默认 JSON ----------------
    print("\n== qx2d pixel：默认输出 JSON ==")
    pr = pixel_tool.run(ref, out("pixel"))
    check("pixel ok", bool(pr["ok"]), json.dumps(pr["errors"]["Q_to_grid"], ensure_ascii=False))
    check("pixel 仅输出 json", pr["emitted"] == ["json"], str(pr["emitted"]))
    check("pixel 写出数据文件", os.path.isfile(pr["files"]["data_json"]))
    check("pixel 未写 Lua", "baseline_lua" not in pr["files"])
    check("pixel 逻辑网格 16x16 / cell 8", pr["grid"]["logical"] == [16, 16] and pr["grid"]["cell"] == 8)
    check("pixel Q->栅格 零误差", pr["errors"]["Q_to_grid"]["different_pixels"] == 0)
    check("pixel JSON round-trip 记录一致", pr["verify"]["json_round_trip"]["records_diff"] == 0)
    check("pixel JSON round-trip 像素一致", pr["verify"]["json_round_trip"]["pixels"]["different_pixels"] == 0)
    check("pixel 控件数 < 像素数", pr["controls"]["records"] < pr["controls"]["covered_pixels"],
          "records=%d covered=%d" % (pr["controls"]["records"], pr["controls"]["covered_pixels"]))
    with open(pr["files"]["data_json"], encoding="utf-8") as f:
        data = json.load(f)
    check("pixel 数据契约字段齐全",
          data["schema"] == "qx2d.pixel" and data["schema_version"] == 1
          and len(data["records"]) == pr["controls"]["records"]
          and len(data["palette"]) == pr["palette"]["size"])
    check("pixel 数据含 CONFIG 与画布", "image_prefab_id" in data["config"] and data["canvas"]["width"] == 16)

    # ---------------- 像素画：Lua 可选 ----------------
    print("\n== qx2d pixel：--emit both ==")
    pb2 = pixel_tool.run(ref, out("pixel_both"), emit="both")
    check("both 输出两类产物", sorted(pb2["emitted"]) == ["json", "lua"], str(pb2["emitted"]))
    check("both 写出 Lua", os.path.isfile(pb2["files"]["baseline_lua"]) and os.path.isfile(pb2["files"]["compact_lua"]))
    lua_rt = pb2["verify"]["lua_round_trip"]
    check("Lua round-trip 通过", lua_rt["checked"] and lua_rt["records_diff"] == 0
          and lua_rt["pixels"]["different_pixels"] == 0 and lua_rt["cursor_at_end"] is True
          and lua_rt["lua_static"]["bracket_balanced"] is True)
    smaller = "compact" if lua_rt["compact_bytes"] < lua_rt["baseline_bytes"] else "baseline"
    check("推荐版本与字节数一致", lua_rt["recommended"] == smaller,
          "compact=%d baseline=%d" % (lua_rt["compact_bytes"], lua_rt["baseline_bytes"]))

    print("\n== qx2d pixel：Lua 5.1 ==")
    p51 = pixel_tool.run(ref, out("pixel_51"), emit="lua", lua_version="5.1")
    with open(p51["files"]["compact_lua"], encoding="utf-8") as f:
        text51 = f.read()
    check("5.1 用 math.floor 且无整除运算符", "math.floor" in text51 and " // " not in text51)
    p53 = pixel_tool.run(ref, out("pixel_53"), emit="lua", lua_version="5.3")
    with open(p53["files"]["compact_lua"], encoding="utf-8") as f:
        text53 = f.read()
    check("5.3 用整除运算符", "a // b" in text53)

    # ---------------- 像素画：新参数 ----------------
    print("\n== qx2d pixel：新参数 ==")
    ptrim = pixel_tool.run(ref, out("pixel_trim"), trim=True)
    check("trim 后画布变小", ptrim["grid"]["logical"][0] < 16 or ptrim["grid"]["logical"][1] < 16,
          str(ptrim["grid"]["logical"]))
    check("trim 后仍然 ok", bool(ptrim["ok"]))
    pruns = pixel_tool.run(ref, out("pixel_runs"), merge_strategy="runs")
    check("merge-strategy=runs 可用", bool(pruns["ok"]))
    check("runs 记录数不少于 both", pruns["controls"]["records"] >= pr["controls"]["records"],
          "runs=%d both=%d" % (pruns["controls"]["records"], pr["controls"]["records"]))
    ptau = pixel_tool.run(ref, out("pixel_tau"), tau=3)
    check("tau=3 误差 <= 3", ptau["errors"]["T_to_Q"]["maximum_channel_error"] <= 3)
    palpha = pixel_tool.run(ref, out("pixel_alpha"), alpha_threshold=128)
    check("alpha-threshold 可用", bool(palpha["ok"]))
    pcap = pixel_tool.run(ref, out("pixel_cap"), max_controls=1)
    check("控件超限时 ok=false 且不谎报", pcap["ok"] is False and pcap["controls"]["over_budget"] is True)
    pname = pixel_tool.run(ref, out("pixel_name"), out_name="mySprite", lua_name="myScript", emit="both")
    check("out-name / lua-name 生效",
          pname["files"]["data_json"].endswith("mySprite.json")
          and pname["files"]["baseline_lua"].endswith("myScript.lua"))
    pnoprev = pixel_tool.run(ref, out("pixel_noprev"), preview=False)
    check("--no-preview 不产预览图", "preview_png" not in pnoprev["files"])
    pflat = pixel_tool.run(ref, out("pixel_flat"), json_indent=0)
    with open(pflat["files"]["data_json"], encoding="utf-8") as f:
        check("json-indent=0 输出单行", len(f.read().splitlines()) == 1)

    print("\n== qx2d pixel：大图压缩 ==")
    pbig = pixel_tool.run(big, out("pixel_big"), emit="both")
    check("大图 ok", bool(pbig["ok"]))
    check("大图逻辑网格 32x32 / cell 4", pbig["grid"]["logical"] == [32, 32] and pbig["grid"]["cell"] == 4)
    check("大图记录数可观", pbig["controls"]["records"] > 200, "records=%d" % pbig["controls"]["records"])
    check("大图压缩确实更小",
          pbig["verify"]["lua_round_trip"]["compact_bytes"] < pbig["verify"]["lua_round_trip"]["baseline_bytes"],
          "compact=%d baseline=%d" % (pbig["verify"]["lua_round_trip"]["compact_bytes"],
                                      pbig["verify"]["lua_round_trip"]["baseline_bytes"]))

    # ---------------- 帧动画 ----------------
    print("\n== qx2d frames：默认输出 JSON ==")
    fr = frames_tool.run(frames_dir, out("frames_dir"), fps=12)
    check("frames ok", bool(fr["ok"]), json.dumps(fr["verify"], ensure_ascii=False))
    check("frames 仅输出 json", fr["emitted"] == ["json"], str(fr["emitted"]))
    check("frames 逻辑网格 16x16 / cell 6", fr["grid"]["logical"] == [16, 16] and fr["grid"]["cell"] == 6)
    check("frames 帧数 8", fr["timeline"]["frame_count"] == 8)
    check("frames 逐帧零误差", fr["errors"]["Q_to_grid"]["different_pixels"] == 0)
    check("frames JSON round-trip 一致",
          fr["verify"]["json_round_trip"]["frames_with_pixel_diff"] == 0
          and fr["verify"]["json_round_trip"]["slot_multiset_diff"] == 0)
    check("frames 池容量覆盖峰值", fr["timeline"]["pool_size"] >= fr["timeline"]["peak_visible_controls"])
    check("frames 播放期创建/销毁为 0",
          fr["cost"]["playback_created_controls"] == 0 and fr["cost"]["playback_destroyed_controls"] == 0)
    with open(fr["files"]["data_json"], encoding="utf-8") as f:
        fdata = json.load(f)
    check("frames 数据契约字段齐全",
          fdata["schema"] == "qx2d.frames" and fdata["timeline"]["frame_count"] == 8
          and fdata["pool_size"] == fr["timeline"]["pool_size"])
    check("frames 帧里含 slot 与几何",
          all(set(s) >= {"slot", "colorIndex", "x", "y", "width", "height"} for s in fdata["timeline"]["frames"][0]))

    print("\n== qx2d frames：--emit both / GIF ==")
    fb = frames_tool.run(gif, out("frames_both"), emit="both", lua_version="5.1")
    check("gif + lua ok", bool(fb["ok"]), json.dumps(fb["verify"]["lua_round_trip"], ensure_ascii=False))
    check("gif 用了来源时长", fb["timeline"]["duration_source"] == "来源文件的逐帧时长")
    check("gif 帧数 8", fb["timeline"]["frame_count"] == 8)
    with open(fb["files"]["lua"], encoding="utf-8") as f:
        lua_text = f.read()
    check("Lua 5.1 帧播放器无整除运算符", "math.floor" in lua_text and " // " not in lua_text)

    print("\n== qx2d frames：新参数 ==")
    fsel = frames_tool.run(frames_dir, out("frames_sel"), start=2, end=8, stride=2)
    check("start/end/stride 选帧", fsel["timeline"]["frame_count"] == 4, str(fsel["timeline"]["frame_count"]))
    fmax = frames_tool.run(frames_dir, out("frames_max"), max_frames=3)
    check("max-frames 生效", fmax["timeline"]["frame_count"] == 3)
    fpp = frames_tool.run(frames_dir, out("frames_pp"), pingpong=True)
    check("pingpong 追加反向帧", fpp["timeline"]["frame_count"] == 14, str(fpp["timeline"]["frame_count"]))
    check("pingpong 仍然零误差", fpp["errors"]["Q_to_grid"]["different_pixels"] == 0)
    fdup = frames_tool.run(frames_dup, out("frames_dup"), merge_identical=True)
    check("merge-identical 合并重复帧", fdup["timeline"]["frame_count"] == 2
          and fdup["timeline"]["merged_identical_frames"] == 1,
          "count=%d merged=%d" % (fdup["timeline"]["frame_count"], fdup["timeline"]["merged_identical_frames"]))
    fdur = frames_tool.run(frames_dir, out("frames_dur"), duration_ms="120")
    check("duration-ms 生效", fdur["timeline"]["durations_ms"] == [120] * 8
          and fdur["timeline"]["fps"] == 8, str(fdur["timeline"]["fps"]))
    bad = False
    try:
        frames_tool.run(frames_dir, out("frames_bad"), duration_ms="100,120")
    except ValueError:
        bad = True
    check("duration-ms 个数不符时报错", bad)
    fnotrim = frames_tool.run(frames_dir, out("frames_notrim"), trim=False)
    check("--no-trim 用整幅画布", fnotrim["timeline"]["local_bounds"] == [16, 16],
          str(fnotrim["timeline"]["local_bounds"]))
    fnative = frames_tool.run(frames_dir, out("frames_native"), grid="native")
    check("grid=native 关闭网格推断", fnative["grid"]["cell"] == 1 and fnative["grid"]["logical"] == [96, 96],
          str(fnative["grid"]["logical"]))
    falpha = frames_tool.run(gif, out("frames_alpha"), alpha_threshold=8)
    check("frames alpha-threshold 可用", bool(falpha["ok"]))

    # ---------------- CLI ----------------
    print("\n== CLI 退出码 ==")
    rc_ok = cli.main(["pixel", "--input", ref, "--out", out("cli_ok"), "--quiet"])
    rc_cap = cli.main(["pixel", "--input", ref, "--out", out("cli_cap"), "--max-controls", "1", "--quiet"])
    rc_bad = cli.main(["pixel", "--input", os.path.join(FIX, "nope.png"), "--out", out("cli_bad"), "--quiet"])
    rc_frames = cli.main(["frames", "--input", frames_dir, "--out", out("cli_frames"), "--fps", "18",
                          "--emit", "both", "--quiet"])
    check("CLI 成功 = 0", rc_ok == 0, str(rc_ok))
    check("CLI 未达标 = 2", rc_cap == 2, str(rc_cap))
    check("CLI 输入错误 = 3", rc_bad == 3, str(rc_bad))
    check("CLI frames = 0", rc_frames == 0, str(rc_frames))
    check("CLI 产出 JSON 与 Lua", os.path.isfile(out("cli_frames/anim.json"))
          and os.path.isfile(out("cli_frames/levelScript.lua")))

    # ---------------- 图元拟合 ----------------
    print("\n== qx2d shapes：图元拟合 ==")
    photo = os.path.join(FIX, "photo.png")
    sh = shapes_tool.run(photo, out("shapes"), num_shapes=30, candidates=8, climb=12, emit="both")
    check("shapes ok", bool(sh["ok"]), json.dumps(sh["verify"], ensure_ascii=False))
    check("shapes round-trip 最大通道误差 <= 1", sh["verify"]["json_round_trip"]["max_channel_error"] <= 1,
          str(sh["verify"]["json_round_trip"]["max_channel_error"]))
    check("shapes 图元数与分类一致",
          sh["fit"]["elements"] == 30 and sum(sh["fit"]["by_kind"].values()) == 30,
          str(sh["fit"]["by_kind"]))
    check("shapes Lua 括号配对且元素数一致",
          sh["verify"]["lua_round_trip"]["lua_static"]["bracket_balanced"] is True
          and sh["verify"]["lua_round_trip"]["elements_in_script"] == 30)
    check("shapes PSNR 达到可用水平", sh["fit"]["image_quality"]["psnr"] > 12.0,
          "psnr=%.2f" % sh["fit"]["image_quality"]["psnr"])
    check("shapes 仅输出两个产物", sorted(sh["emitted"]) == ["json", "lua"])
    with open(sh["files"]["data_json"], encoding="utf-8") as f:
        sh_data = json.load(f)
    check("shapes 数据契约字段齐全",
          sh_data["schema"] == "qx2d.shapes" and len(sh_data["elements"]) == 30
          and set(sh_data["elements"][0]) == {"kind", "x", "y", "width", "height", "rotation", "colorIndex", "opacity"})

    sh_rect = shapes_tool.run(photo, out("shapes_rect"), num_shapes=20, candidates=6, climb=8,
                              kinds="rect", allow_rotation=False)
    check("shapes --kinds rect 只出矩形", sh_rect["ok"] and set(sh_rect["fit"]["by_kind"]) == {"rect"},
          str(sh_rect["fit"]["by_kind"]))
    sh_gate = shapes_tool.run(photo, out("shapes_gate"), num_shapes=5, candidates=4, climb=4, min_psnr=60)
    check("shapes --min-psnr 门槛生效", sh_gate["ok"] is False)
    sh_scale = shapes_tool.run(photo, out("shapes_scale"), num_shapes=16, candidates=6, climb=8, fit_scale=0.5)
    with open(sh_scale["files"]["data_json"], encoding="utf-8") as f:
        sh_small = json.load(f)
    check("shapes --fit-scale 输出回源尺寸",
          sh_scale["ok"] and max(e["x"] for e in sh_small["elements"]) <= 120,
          "max_x=%.1f" % max(e["x"] for e in sh_small["elements"]))
    sh_json_only = shapes_tool.run(photo, out("shapes_json"), num_shapes=10, candidates=4, climb=4, emit="json")
    check("shapes 默认/仅 JSON 不写 Lua", sh_json_only["emitted"] == ["json"] and "lua" not in sh_json_only["files"])

    # ---------------- 动画编辑 ----------------
    print("\n== qx2d anim：动画编辑 ==")
    kf = os.path.join(FIX, "keyframes.json")
    an = anim_tool.run(kf, out("anim"), emit="both")
    check("anim ok", bool(an["ok"]), json.dumps(an["verify"], ensure_ascii=False))
    check("anim 帧数 15", an["timeline"]["frame_count"] == 15, str(an["timeline"]["frame_count"]))
    check("anim JSON 独立求值完全一致",
          an["verify"]["json_round_trip"]["slot_diff"] == 0
          and an["verify"]["json_round_trip"]["max_value_diff"] == 0)
    check("anim Lua 逐帧状态一致",
          an["verify"]["lua_round_trip"]["slot_diff"] == 0
          and an["verify"]["lua_round_trip"]["cursor_at_end"] is True
          and an["verify"]["lua_round_trip"]["lua_static"]["bracket_balanced"] is True)
    an_ops = anim_tool.run(kf, out("anim_ops"), speed=2, offset_x=15, offset_y=-5, rotate=15,
                           repeat=2, ease="Linear", emit="both")
    check("anim 变速+平移+旋转+重复+缓动覆盖",
          an_ops["ok"] and an_ops["timeline"]["frame_count"] == 16
          and an_ops["verify"]["json_round_trip"]["slot_diff"] == 0
          and an_ops["verify"]["lua_round_trip"]["slot_diff"] == 0,
          "frames=%d" % an_ops["timeline"]["frame_count"])
    an_range = anim_tool.run(kf, out("anim_range"), start_ms=300, end_ms=900, reverse=True)
    check("anim 裁剪+反向", an_range["ok"] and an_range["timeline"]["frame_count"] == 8
          and an_range["verify"]["json_round_trip"]["slot_diff"] == 0,
          "frames=%d" % an_range["timeline"]["frame_count"])
    an_dur = anim_tool.run(kf, out("anim_dur"), duration_ms=600, fps=20)
    check("anim 时长重定标 + 改帧率", an_dur["ok"] and an_dur["timeline"]["fps"] == 20
          and an_dur["timeline"]["duration_ms"] == 600)
    with open(an["files"]["data_json"], encoding="utf-8") as f:
        an_data = json.load(f)
    check("anim 数据契约含关键帧/时间轴/事件/编辑参数",
          an_data["schema"] == "qx2d.anim" and an_data["keyframes"]["tracks"]
          and an_data["timeline"]["frame_count"] == 15 and len(an_data["events"]) == 1
          and "speed" in an_data["edit"])
    bad_path = out("anim_bad.json")
    with open(bad_path, "w", encoding="utf-8") as f:
        json.dump({"schema": "qx2d.nope"}, f)
    bad = False
    try:
        anim_tool.run(bad_path, out("anim_bad"))
    except ValueError:
        bad = True
    check("anim 非法文档报错", bad)

    # ---------------- MCP ----------------
    print("\n== qx2d MCP ==")
    init = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    check("mcp initialize", init is not None and init["result"]["serverInfo"]["name"] == "qx2d")
    listing = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = {t["name"]: t for t in listing["result"]["tools"]}
    check("mcp 暴露四个工具",
          sorted(tools) == ["anim_edit", "frame_animate", "pixel_reconstruct", "shape_fit"],
          str(sorted(tools)))
    check("mcp 新参数已声明",
          "alpha_threshold" in tools["pixel_reconstruct"]["inputSchema"]["properties"]
          and "merge_identical" in tools["frame_animate"]["inputSchema"]["properties"]
          and "num_shapes" in tools["shape_fit"]["inputSchema"]["properties"]
          and "speed" in tools["anim_edit"]["inputSchema"]["properties"])
    call = handle_message({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "pixel_reconstruct",
                   "arguments": {"input": ref, "out_dir": out("pixel_mcp"), "trim": True, "emit": "json"}},
    })
    payload = json.loads(call["result"]["content"][0]["text"])
    check("mcp 调用 pixel_reconstruct（含新参数）",
          payload["ok"] is True and payload["grid"]["trimmed"] is True and call["result"]["isError"] is False)
    call2 = handle_message({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "frame_animate",
                   "arguments": {"input": frames_dir, "out_dir": out("frames_mcp"), "pingpong": True}},
    })
    payload2 = json.loads(call2["result"]["content"][0]["text"])
    check("mcp 调用 frame_animate（含新参数）", payload2["ok"] is True and payload2["timeline"]["frame_count"] == 14)
    missing = handle_message({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                              "params": {"name": "nope", "arguments": {}}})
    check("mcp 未知工具报错", missing["result"]["isError"] is True)
    call3 = handle_message({
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "shape_fit",
                   "arguments": {"input": os.path.join(FIX, "photo.png"), "out_dir": out("shapes_mcp"),
                                 "num_shapes": 12, "candidates": 4, "climb": 4}},
    })
    payload3 = json.loads(call3["result"]["content"][0]["text"])
    check("mcp 调用 shape_fit", payload3["ok"] is True and payload3["fit"]["elements"] == 12)
    call4 = handle_message({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "anim_edit",
                   "arguments": {"input": os.path.join(FIX, "keyframes.json"), "out_dir": out("anim_mcp"),
                                 "speed": 1.5}},
    })
    payload4 = json.loads(call4["result"]["content"][0]["text"])
    check("mcp 调用 anim_edit", payload4["ok"] is True and payload4["verify"]["json_round_trip"]["slot_diff"] == 0)

    print("\n========================")
    if FAILURES:
        print("失败 %d 项：" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
