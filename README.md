# 千星2D关卡AI开发能力

本仓库收集了千星开发接入AI的方法和相关提示词、工具等，助力奇匠高效开发2D关卡。

## 这个仓库里有什么

| 目录 | 内容 | 怎么用 |
|---|---|---|
| `release/` | 发布版提示词包：七步流程分册 00–25、接口与操作文档、配套工具 | 交给支持 Agent 的 AI 工具，从 `00_主控与派单.md` 开始 |
| `release/tools/` | 画面生成工具（像素画 / 帧动画 / 图元拟合 / 动画编辑）+ MCP 服务 | 命令行或 MCP 调用，默认输出 JSON 数据 |
| `release/tools/sandbox/` | **体外沙箱**：不进编辑器就能跑 Lua 脚本、模拟试玩并出报告 | 双击网页，或命令行 `/node` 运行 |

一句话概括工作方式：**提示词决定"怎么做"，工具负责"生成与校验"，沙箱负责"先跑一遍"。**

## 快速开始

### 一、只想用提示词

把 `release/` 整个目录提供给你的 AI 开发工具（Claude Code / Cursor / 其他支持 Agent 的工具），让它先读
`release/00_主控与派单.md`——那是主控分册，规定了七步流程、门禁、派单规范与验收标准；其余分册按阶段读取。

配套资料（同级目录）里的 **API 文档** 与 **客户端控件及脚本使用指南** 是接口与编辑器操作的唯一权威，
写任何依赖编辑器接口的代码之前都要先确认文档在手。

### 二、用工具生成画面

需要 Python 3.9+ 与 Pillow（`shapes` 还需要 numpy）：

```bash
cd release

# 像素风参考图 → 图片控件拼图
python tools/qx2d/cli.py pixel  --input 参考图.png --out out/pixel

# 帧目录 / GIF / WebP → 控件池帧动画
python tools/qx2d/cli.py frames --input frames/ --out out/anim --fps 18

# 照片 / 立绘 / 图标 → 矩形+椭圆+三角形图元拟合
python tools/qx2d/cli.py shapes --input photo.png --out out/shapes --num-shapes 200

# 关键帧文档 → 变速/裁剪/缓动/平移后的动画
python tools/qx2d/cli.py anim   --input keyframes.json --out out/anim --speed 1.5
```

- **默认输出 JSON 数据契约**（调色板、控件记录、时间轴、CONFIG 常量），需要 Lua 时加 `--emit lua` 或 `--emit both`；
- 每次都会用**独立实现**解码回来重新栅格化 / 重新求值做校验，不通过就返回 `ok=false`；
- 退出码即判断依据：`0` 可用 / `2` 未达标（不要采用）/ `3` 输入或参数错误；
- 完整参数表、四份 JSON 数据契约、已知限制见 [`release/tools/README.md`](release/tools/README.md)。

想让 AI 直接当工具调用，可启动 MCP 服务：

```bash
python release/tools/qx2d_mcp.py     # 暴露 pixel_reconstruct / frame_animate / shape_fit / anim_edit
```

### 三、交付前先过体外沙箱

沙箱内置 Lua 5.3 虚拟机（离线运行、不联网、不上传任何素材），把生成的 `levelScript.lua` 直接跑起来：

**网页版（给人用）**：双击 `release/tools/sandbox/index.html` → 粘入脚本 → 选设备形态 → 装载 → 启动。
画布上点击就是点击事件，另有"随机输入轰炸""单步""销毁"用来压脚本。

**命令行版（给 AI 用）**：

```bash
node release/tools/sandbox/run_headless.mjs out/anim/levelScript.lua --fuzz 3000 --json
```

报告里看三件事：`errors` 必须为空、`unsupported` 必须为空、`controls.neverActivated` 必须为 0
（新建控件默认 `active=false`，忘记 `SetActive(true)` 在真机上根本不显示）。详见
[`release/tools/sandbox/README.md`](release/tools/sandbox/README.md)。

> 沙箱是为了**少跑几趟编辑器**，不是替代真机：它不模拟引擎渲染、遮罩羽化、真实字体与布局细节，
> 报告里的真机状态始终是"待验证"。

## 七步流程

```text
项目立项 → 编策划案 → Demo 初审 → 编写 Lua → Bug 修复 → 版本迭代 → 最终交付
```

每一步都有**进入条件**和**退出产物**（门禁），产物不达标不得进入下一步；已合格的项目从最早缺失的那一步继续。
分册地图、全局约定（多端 + 横屏、分辨率矩阵、用户身份）、派单规范、验收清单都在
[`release/00_主控与派单.md`](release/00_主控与派单.md)。

| 分册 | 什么阶段读 |
|---|---|
| `01` 项目立项 / `02` 编策划案 / `03` Demo 初审 / `04` 编写 Lua / `05` Bug 修复 / `06` 版本迭代 / `07` 最终交付 | 七步流程，每步一本 |
| `08` 平台与玩法 / `09` 布局与锚点 / `10` 输入与运行期 / `12` 故障案例库 | 参考资料，查证据用 |
| `11` 子代理总表 | 派单前查：这次该派哪几个 Agent、各用什么档位 |
| `20` 只读检索 / `23` 语法检查 / `24` 用户沟通 / `25` 画面工具执行 | 子代理任务书 |
| `Lua客户端控件API文档.md` / `客户端控件及脚本使用指南.md` | 接口与编辑器操作的唯一权威 |

## 目录结构

```text
.
├── README.md
├── LICENSE                     GPL-3.0
├── release/
│   ├── 00_主控与派单.md          ← 从这里开始
│   ├── 01 … 07                 七步流程分册
│   ├── 08 … 12                 参考资料（平台玩法 / 布局锚点 / 运行期 / 子代理总表 / 故障案例库）
│   ├── 20 / 21 / 23 / 24 / 25  子代理任务书与自绘要点
│   ├── Lua客户端控件API文档.md
│   ├── 客户端控件及脚本使用指南.md
│   ├── README.md               发布包说明
│   └── tools/
│       ├── qx2d/               生成工具（pixel / frames / shapes / anim）
│       ├── qx2d_mcp.py         MCP 服务
│       ├── sandbox/            体外沙箱（网页 + 命令行 + 内置 Lua 虚拟机）
│       └── tests/              端到端自测与测试素材
```

## 自测

```bash
python release/tools/tests/run_all.py                                    # 88 项：四个生成工具的端到端校验
node release/tools/sandbox/selftest.mjs --with-generated release/tools/tests/out   # 沙箱：能跑、能抓错、生成物能过
```

两套自测都会打印 `PASS/FAIL` 并以退出码表示结果，可作为改动后的回归门槛。

## 第三方组件

`release/tools/sandbox/vendor/` 下内置了两个第三方库，许可证随源码保留：

- **Lua 5.3 虚拟机（JS 实现）** — MIT，见 `vendor/fengari/LICENSE`
- **sprintf-js** — BSD-3-Clause，见 `vendor/sprintf-js/LICENSE`

沙箱与全部工具均为纯本地运行：不联网、不上传素材、不启动浏览器内核。

## 免责声明

- 本文档及其子级下的文档（下文统称“开发能力”）均归属于 **@是兔头呀**。不得二次包装、发布或出售。
- 用户在使用本开发能力时，不得引导 AI 开发侵权、涉黄、涉政等违法奇域。
- 禁止利用本开发能力配置全自动开发奇域系统。
- 如果用户在使用本开发能力时遭受不可控的利益损害，均与本人无关，本人不承担相关责任。
- **用户一旦使用本开发能力，即视为同意本免责声明。**
