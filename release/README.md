# kira2d 提示词包 · release

这是**发布版**：一套可以让 AI 把 2D 关卡从想法做到能交付的提示词，外加两个可直接调用的工具。
和 `with_tools/`（开发版）的区别只有一处：**本包不带技能目录**，帧动画与像素画直接走自带工具。

## 目录

```text
release/
  00_主控与派单.md          ← 入口：主控的职责、门禁、开工与派单规范（先读这个）
  01_项目立项.md   …  07_最终交付.md     七步流程，每步一本分册
  08_…12_参考_*.md                        参考资料（平台玩法、布局锚点、运行期、故障库）
  11_参考_子代理总表.md                   派单前查：这次该派哪几个 Agent、各用什么档位
  20_/21_/23_/24_*.md                     子代理任务书与自绘要点
  25_子代理_像素画与帧动画.md              跑 qx2d 工具的专用任务书（画面类活都派它）
  Lua客户端控件API文档.md                  接口唯一权威
  客户端控件及脚本使用指南.md              编辑器操作唯一权威
  tools/                                  像素画 / 帧动画工具
```

## 工具

两个工具，一条命令跑完一件复杂活，**默认输出 JSON 数据**：

```bash
python tools/qx2d/cli.py pixel  --input 参考图.png --out out/pixel     # 像素画
python tools/qx2d/cli.py frames --input 帧目录/ --out out/anim --fps 18 # 帧动画
python tools/qx2d/cli.py shapes --input 照片.png --out out/shapes      # 图元拟合
python tools/qx2d/cli.py anim   --input 关键帧.json --out out/anim      # 动画编辑
```

- 需要 Lua 时加 `--emit lua`（或 `--emit both`）。
- 退出码就是判断依据：`0` 可用 / `2` 未达标（**不要采用**）/ `3` 输入错误。
- 需要 MCP 时启动 `python tools/qx2d_mcp.py`，暴露 `pixel_reconstruct` 与 `frame_animate`。
- 全部参数、JSON 契约、验收门槛、已知限制见 **`tools/README.md`**。
- 自测：`python tools/tests/run_all.py`（生成确定性素材，覆盖两条流水线与全部参数）。

**工具优先，执行派出去**（`00_主控与派单.md` 5.2 第一条）：主控不亲自跑这两条命令，
把输入、输出目录、参数与 CONFIG 常量写成任务书，派 **`25_子代理_像素画与帧动画.md`** 这一个 Agent 执行
（多张图、多段动画由它连续接），再按报告的 `ok` 与退出码验收。
只有工具覆盖不到的能力——形状拟合、overpaint 分层、接入已有脚本、I/P 帧差分、父节点共同运动、运行时验证——
才转回 `21_自绘实现要点.md` 或人工。

## 环境

- Python 3.9+ 与 Pillow（`python -m pip install pillow`）。
- 千星沙箱里的编辑器操作、容器节点索引、控件模板索引等，仍需用户手动提供（见 `01_项目立项.md` 第三节）。
