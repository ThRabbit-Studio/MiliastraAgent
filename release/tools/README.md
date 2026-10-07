# qx2d —— 画面工具集（AI 可直接调用）

四个工具，一条命令跑完一件复杂活，不必把成套技能读进上下文。

> **在提示词流程里，这些命令由子代理执行。** 主控不亲自跑工具：它把输入、输出目录、参数与 CONFIG 常量
> 写进任务书，派 **`25_子代理_像素画与帧动画.md`** 这**一个** Agent 执行并核对报告（四类画面活都归它），
> 然后按 `ok` / 退出码验收。本文件是那个子代理的执行手册。

| 工具 | 做什么 | 输入 | 默认产物 |
|---|---|---|---|
| `pixel`（`pixel_reconstruct`） | 像素画 → 图片控件拼图 | 像素风参考图 | `<out>.json`、`<out>.report.json`、`<out>.preview.png` |
| `frames`（`frame_animate`） | 帧序列 / GIF / WebP → 控件池帧动画 | 帧目录或多帧文件 | 同上 |
| `shapes`（`shape_fit`） | 普通图片 → 图元拟合（矩形 / 椭圆 / 三角形） | 照片、立绘、图标 | 同上 |
| `anim`（`anim_edit`） | 关键帧 + 缓动 + 时间轴编辑 → 动画 | 关键帧文档 | 同上 |
| `sandbox`（无 MCP） | 跑 Lua 脚本 + 模拟试玩（不进编辑器） | 生成的 `levelScript.lua` | 结构化报告 JSON |

前四个产出的都是**待接入的脚本 / 数据**；第五个是**验收工具**，用来判断脚本能不能跑、有没有明显写错。
用法见 [`sandbox/README.md`](sandbox/README.md)：网页双击 `sandbox/index.html`，命令行 `node sandbox/run_headless.mjs <脚本.lua>`。

**默认输出 JSON 数据，不生成 Lua。** 需要 Lua 时加 `--emit lua` 或 `--emit both`。
无论输出哪种格式，工具都会用一套**独立实现**的解码器把产物解回来重新栅格化 / 重新求值并比对；
校验不过就返回 `ok=false`（退出码 2），不会产出"看起来成功"的结果。

**怎么选**：

- 像素风、想要逐像素可控 → `pixel`；
- 已有帧序列 / GIF → `frames`；
- 照片、立绘、渐变、抗锯齿边缘（`pixel` 的矩形分解会很难看）→ `shapes`；
- 要调运动、缓动、时长、事件，而不是逐帧图 → `anim`。


---

## 一、前置与位置

- 本目录属于 **release 包**：`release/tools/`。以下命令都在 `release/` 目录下执行。
- Python 3.9+（仓库自带 `python` 即可），Pillow：`python -m pip install pillow`

## 二、命令行调用

```bash
# 像素画 → JSON 数据
python tools/qx2d/cli.py pixel --input ref.png --out out/pixel

# 像素画 → 同时给 JSON 和 Lua
python tools/qx2d/cli.py pixel --input ref.png --out out/pixel --emit both

# 帧动画 → JSON 数据（18 FPS）
python tools/qx2d/cli.py frames --input frames/ --out out/anim --fps 18

# 帧动画 → 往返播放、每帧 80ms、合并重复帧
python tools/qx2d/cli.py frames --input anim.gif --out out/anim --pingpong --duration-ms 80 --merge-identical
```

也可以作为模块调用：`cd tools && python -m qx2d pixel ...`

## 三、参数总表

### 两个工具通用

| 参数 | 默认 | 说明 |
|---|---|---|
| `--input` | 必填 | 参考图路径；帧目录 / GIF / WebP 路径 |
| `--out` | 必填 | 输出目录，不存在会创建 |
| `--emit` | `json` | `json` 只出数据；`lua` 只出脚本；`both` 都出 |
| `--out-name` | `pixel` / `anim` | 输出基名：`<out-name>.json`、`<out-name>.report.json`、`<out-name>.preview.png` |
| `--lua-name` | `levelScript` | Lua 产物基名（`<lua-name>.lua`、`<lua-name>.compact.lua`） |
| `--grid` | `auto` | `auto` 推断整数倍像素单元；`native` 原始分辨率；`8` 单元尺寸；`16x16` 逻辑尺寸 |
| `--tau` | `0` | 允许的逐通道色差（τ）。`3` = 轻微色差合并；`0` = 无损 |
| `--max-colors` | `0` | 调色板上限，**有损**，只在用户明确同意时用 |
| `--alpha-threshold` | `0` | `alpha <= N` 按透明处理；抠图半透明杂边可设 `8~128` |
| `--merge-strategy` | `both` | `greedy`（记录更少）/ `runs`（更快）/ `both`（取更少者） |
| `--preview` / `--no-preview` | 开 | 是否生成预览图 |
| `--preview-scale` | `0` | 预览放大倍数，`0` = 自动 |
| `--json-indent` | `1` | JSON 缩进，`0` = 单行紧凑（省体积） |
| `--lua-version` | `5.3` | `5.3` 用 `//`；`5.1` 自动改用 `math.floor` |
| `--image-prefab-id` `--image-source` `--image-type` `--square-asset-id` `--no-tintable` | `0` | 写进产物 `config` 段 / Lua CONFIG 段的编辑器侧常量。**必须按本项目 `annotations.lua` / `readme.md` 填真实值** |
| `--base-pixel-size` `--canvas-margin` | `8` / `0.9` | 画布适配参数 |
| `--json` | 关 | stdout 打印完整报告（默认只打摘要） |
| `--quiet` | 关 | 不打印，只用退出码表示结果 |
| `--version` | — | 版本号 |

### 仅像素画

| 参数 | 默认 | 说明 |
|---|---|---|
| `--max-controls` | `0` | 控件数上限；超出时 `ok=false`、退出码 2（不会自动降级） |
| `--trim` / `--no-trim` | 不裁 | 裁掉四周完全透明的边；画布变成内容边界，`grid.origin` 记录偏移 |

### 仅帧动画

| 参数 | 默认 | 说明 |
|---|---|---|
| `--fps` | `0` | 目标帧率；`0` = 用来源逐帧时长，取不到时 12 |
| `--loop` / `--no-loop` | 循环 | 是否循环播放 |
| `--start` | `1` | 从第几帧开始（1 基准） |
| `--end` | `0` | 到第几帧结束（含）；`0` = 最后一帧 |
| `--stride` | `1` | 每隔几帧取一帧 |
| `--max-frames` | `0` | 最多用多少帧 |
| `--duration-ms` | 空 | 逐帧时长：`"80"` 或 `"80,80,120"`；覆盖来源时长与 `--fps` |
| `--pingpong` | 关 | 往返播放，自动追加反向帧（去掉重复端点） |
| `--merge-identical` | 关 | 合并连续相同帧，时长累加 |
| `--trim` / `--no-trim` | 裁 | 按所有帧的**联合**内容边界裁剪画布（不裁则用整幅画布） |
| `--create-batch` | `60` | Lua 播放器启动预热时每个 tick 创建的控件数 |

### 仅图元拟合

| 参数 | 默认 | 说明 |
|---|---|---|
| `--num-shapes` | `200` | 要拟合的图元数量（越多越像，控件也越多） |
| `--candidates` | `16` | 每轮候选数量 |
| `--climb` | `32` | 每个候选的爬山微调次数（越大越慢越准） |
| `--kinds` | `rect,ellipse,triangle` | 允许的图元类型 |
| `--no-rotation` | 允许旋转 | 禁止图元旋转（形状表达能力下降，但接入更简单） |
| `--min-size` / `--max-size` | 自动 | 图元边长范围（像素） |
| `--spill-penalty` | `4.0` | 图元溢出内容区域的惩罚系数（越大越贴边） |
| `--alpha-min` / `--alpha-max` | `0.15` / `1.0` | 图元透明度范围 |
| `--fit-scale` | `1.0` | 在缩放后的分辨率上拟合（`0.5` 大约快 4 倍，结果仍按源尺寸输出） |
| `--color-bits` | `8` | 调色板量化位深，越小颜色越少（如 `5` 明显减少调色板项） |
| `--min-psnr` | `0` | PSNR 门槛，低于它则 `ok=false`（退出码 2） |
| `--seed` | `12345` | 随机种子，保证可复现 |
| `--rect-asset-id` `--ellipse-asset-id` `--triangle-asset-id` | `0` | CONFIG：三种图元各自的 assetID |

### 仅动画编辑

| 参数 | 默认 | 说明 |
|---|---|---|
| `--speed` | `1` | 整体速度倍率（`2` = 快一倍，时间轴等比压缩） |
| `--start-ms` / `--end-ms` | `0` / `0` | 裁剪区间（毫秒），端点状态会被补成关键帧 |
| `--reverse` | 关 | 时间反向 |
| `--ease` | 空 | 把全部关键帧的缓动统一改成它（如 `OutCubic`） |
| `--fps` | `0` | 采样帧率，`0` = 用文档里的设置 |
| `--duration-ms` | `0` | 整体时长重定标（毫秒） |
| `--offset-x` `--offset-y` | `0` | 整体平移（画布像素） |
| `--scale` | `1` | 整体缩放（以画布中心为基准） |
| `--rotate` | `0` | 整体旋转（度，顺时针为正） |
| `--repeat` | `1` | 整段时间轴重复次数 |
| `--create-batch` | `60` | Lua 播放器启动预热批大小 |

### 退出码

| 码 | 含义 | 调用方该怎么做 |
|---|---|---|
| `0` | 成功，全部校验通过 | 直接采用产物 |
| `2` | 跑完但**没达标**（逐像素校验未过 / 超出 `--max-controls`） | **不要采用**；看报告 `errors`、`verify` 定位，调参重跑或转人工 |
| `3` | 输入或参数错误（文件不存在、网格不整除、`--duration-ms` 个数不符、画面全透明等） | 修正输入，`error` 字段有原因 |

## 四、JSON 数据契约

### 像素画 `<out-name>.json`

```jsonc
{
  "schema": "qx2d.pixel", "schema_version": 1, "tool": "qx2d", "tool_version": "1.2.0",
  "source":  { "path": "...", "sha256": "...", "width": 128, "height": 128 },
  "grid":    { "cell": 8, "width": 16, "height": 16, "trimmed": false, "evidence": "..." },
  "canvas":  { "width": 16, "height": 16, "origin_x": 0, "origin_y": 0,
               "base_pixel_size": 8, "canvas_margin": 0.9 },
  "config":  { "image_prefab_id": 0, "image_source": 0, "image_type": 0,
               "square_asset_id": 0, "tintable": true, "lua_version": "5.3" },
  "palette": [ { "index": 1, "rgba": [198, 78, 84, 255] } ],
  "records": [ { "kind": 0, "x": 0, "y": 0, "width": 4, "height": 3, "angle": 0, "colorIndex": 1 } ],
  "record_keys": ["kind", "x", "y", "width", "height", "angle", "colorIndex"],
  "stats":   { "records": 31, "unit_controls": 2, "scaled_controls": 29, "covered_pixels": 124 },
  "notes":   [ "..." ]
}
```

### 帧动画 `<out-name>.json`

```jsonc
{
  "schema": "qx2d.frames", "schema_version": 1,
  "source": { "path": "...", "sha256": "...", "frames": 8, "width": 96, "height": 96 },
  "grid":   { "cell": 6, "width": 16, "height": 16, "trimmed": true, "evidence": "..." },
  "canvas": { "origin_x": 0, "origin_y": 2, "local_width": 16, "local_height": 14,
              "base_pixel_size": 8, "canvas_margin": 0.9 },
  "config": { "image_prefab_id": 0, "image_source": 0, "image_type": 0,
              "square_asset_id": 0, "tintable": true, "create_batch": 60, "lua_version": "5.3" },
  "palette": [ { "index": 1, "rgba": [58, 60, 92, 255] } ],
  "sizes":   [ [2, 2], [3, 3], [4, 4], [16, 3] ],
  "groups":  [ { "colorIndex": 1, "pool": 1, "base": 0 } ],
  "pool_size": 5,
  "timeline": {
    "fps": 12, "loop": true, "frame_count": 8, "durations_ms": [83, 83, 83, 83, 83, 83, 83, 83],
    "frames": [ [ { "slot": 0, "colorIndex": 1, "x": 2, "y": 2, "width": 2, "height": 2 } ] ]
  },
  "frame_keys": ["slot", "colorIndex", "x", "y", "width", "height"],
  "stats": { "pool_size": 5, "peak_visible_controls": 4, "frame_count": 8, "merged_identical_frames": 0 },
  "notes": ["..."]
}
```

**自己写 Lua / 做别的实现时按这几条读数据**：

1. `palette[].index` 是 1 基准，`records[].colorIndex` / `frames[].colorIndex` 指向它；`records` 的数组顺序就是 painter order。
2. 坐标原点在画布左上角，x 向右、y 向下，覆盖区间 `x <= X < x+width`；帧动画 `frames[].x/y` 已经是**源坐标**（= 局部坐标 + `canvas.origin_x/origin_y`）。
3. 像素画：一条 `kind=0` 记录 = 一个可独立设置宽高的实心矩形控件，位置与尺寸都乘上运行时算出的 `pixelSize`。
4. 帧动画：同一帧里同色矩形按 `slot` 升序填入该颜色组控件池的**前缀**，没出现的槽位视为隐藏；`pool_size = Σ groups[].pool`。
5. `config` 里的常量必须替换成本项目 `annotations.lua` / `readme.md` 的真实值。

### 图元拟合 `<out-name>.json`

```jsonc
{
  "schema": "qx2d.shapes", "schema_version": 1,
  "source":  { "path": "...", "sha256": "...", "width": 96, "height": 96 },
  "canvas":  { "width": 96, "height": 96, "fit_scale": 1.0, "background": "transparent" },
  "config":  { "rect_asset_id": 0, "ellipse_asset_id": 0, "triangle_asset_id": 0, "tintable": true },
  "palette": [ { "index": 1, "rgba": [40, 90, 200, 255] } ],
  "elements": [ { "kind": 1, "x": 48.0, "y": 40.0, "width": 36.0, "height": 24.0,
                  "rotation": 15.0, "colorIndex": 1, "opacity": 0.9 } ],
  "element_keys": ["kind", "x", "y", "width", "height", "rotation", "colorIndex", "opacity"],
  "stats": { "elements": 30, "by_kind": { "rect": 18, "ellipse": 8, "triangle": 4 },
             "image_quality": { "psnr": 29.3, "mae": 5.0 } }
}
```

读法：`kind` `0/1/2` = 矩形 / 椭圆 / 等腰三角形；`x`/`y` 是**中心**；数组顺序就是绘制顺序（先画底层）；
`rotation` 顺时针为正、单位度；三角形轴心取质心 `(0.5, 1/3)`，其余形状取中心；最终颜色 alpha = `round(palette.a * opacity)`。
`stats.image_quality.psnr` 是还原质量，图元越少越低——够不够用由调用方判断（也可以直接传 `--min-psnr` 设门槛）。

### 动画 `<out-name>.json` 与关键帧文档

动画工具的**输入**是 `qx2d.keyframes` 文档：

```jsonc
{
  "schema": "qx2d.keyframes", "fps": 12, "duration_ms": 1200, "loop": true,
  "canvas": { "width": 320, "height": 180 },
  "palette": [ [240, 200, 80, 255], [90, 160, 240, 255] ],
  "targets": [ { "name": "panel", "x": 60, "y": 90, "width": 80, "height": 40, "colorIndex": 1 } ],
  "tracks": [
    { "target": "panel", "field": "x",
      "keys": [ { "time": 0, "value": 60, "ease": "OutCubic" }, { "time": 1200, "value": 260 } ] }
  ],
  "events": [ { "time": 600, "name": "BadgeShown", "target": "panel", "params": "hello" } ]
}
```

- 可动字段：`x` `y` `width` `height` `rotation` `opacity` `visible` `colorIndex`；没写轨道的字段用 target 上的基值。
- 缓动名：`Linear`、`Step`，以及 `In` / `Out` / `InOut` × `Sine` / `Quad` / `Cubic` / `Quart` / `Quint` / `Expo` / `Circ` / `Back` / `Elastic` / `Bounce`。
- `visible` / `colorIndex` 按阶跃处理；`interpolation: "step"` 等价于 `ease: "Step"`。
- 事件 `params` 是**字符串**，原样传给接入方，不解析、不当代码执行。

**输出** `qx2d.anim`：`keyframes`（编辑后的规范化文档，可以直接回灌给 `anim` 继续编辑）、
`timeline.frames`（按 fps 采样出的逐帧绝对状态）、`events`（随编辑一起改时间）、
`edit`（本次用到的编辑参数，用于复现与独立校验）。每帧每个 slot 的字段：
`slot / visible / x / y / width / height / rotation / colorIndex / opacity`。

## 五、报告 `<out-name>.report.json`

同一份报告也作为调用结果返回。关键字段：

| 字段 | 含义 |
|---|---|
| `ok` | 总判定：所有硬门槛都过才为 `true` |
| `parameters` | 本次实际生效的参数（便于复现） |
| `errors.O_to_T` | 采样误差（逻辑网格推断 + 单元取色） |
| `errors.T_to_Q` | 颜色策略误差（`--tau` / `--max-colors`） |
| `errors.Q_to_grid` | 控件铺回栅格与目标点阵的差异，**必须全 0** |
| `verify.json_round_trip` | 把写出的 `.json` 读回来重新渲染的差异，**必须全 0** |
| `verify.lua_round_trip` | 仅 `--emit lua/both` 时存在：独立解 Lua 压缩流的结果、游标检查、括号冒烟检查 |
| `timeline` / `cost` | 帧数、FPS、控件池、播放期创建/销毁、setter 调用估算 |
| `files` | 本轮写出的所有文件路径 |
| `runtime_verified` | 恒为 `pending`：没有在游戏里跑过 |

## 六、MCP 调用（想让 AI 直接当工具用）

```bash
python tools/qx2d_mcp.py        # stdio，换行分隔 JSON-RPC 2.0
```

```json
{ "mcpServers": { "qx2d": { "command": "python", "args": ["<绝对路径>/release/tools/qx2d_mcp.py"] } } }
```

暴露四个工具：`pixel_reconstruct`、`frame_animate`、`shape_fit`、`anim_edit`，
参数与命令行同名（下划线写法：`out_dir`、`num_shapes`、`offset_x`…），`input` / `out_dir` 必填。
返回内容是一段 JSON 文本报告；`isError=true` 等价于 `ok=false`。

## 七、什么时候**不要**用工具、改回派子代理

工具只覆盖可确定性执行的部分，下面这些仍按对应技能分册人工/子代理完成：

- **overpaint 分层**（允许底层大图形被细节覆盖以减少控件数）；
- **接入已有 `levelScript.lua`**（保留原有玩法逻辑，只替换绘制模块）；
- **I/P 帧差分与 Tween 原语**：`anim` 输出的是逐帧绝对状态，不做差分编码，也不直接调引擎补间 API；
- **父节点承载共同运动**（一组图元共享变换时挂到同一个空节点）；
- **运行时验证**：控件真的创建出来、父子显示关系、帧率与调用量实测；
- 工具报 `ok=false`（退出码 2）且调参解决不了时。

## 八、自测

```bash
python tools/tests/run_all.py                  # 四个生成工具的端到端自测
node tools/sandbox/selftest.mjs --with-generated tools/tests/out   # 沙箱自测（含工具产物）
```

生成确定性素材（16×16/8 倍与 32×32/4 倍像素画、8 帧动画目录、GIF、重复帧目录、照片素材、关键帧文档），
覆盖两条流水线的全部新增参数、JSON/Lua 双 round-trip、退出码与 MCP 入口。

**交付前先过沙箱**：`node tools/sandbox/run_headless.mjs <生成的 levelScript.lua> --fuzz 3000`，
`verdict` 不是 `fail` 且 `controls.neverActivated == 0` 再往下走。沙箱已经抓到过真问题
（例如 `script:EnableTick` 应该是 `script:EnableUpdate`、新建控件忘记 `SetActive(true)`）。

## 九、已知限制（诚实清单）

- **没有 Lua 运行时**：生成的 Lua 没有被真正执行过，`runtime_verified` 恒为 `pending`；
  静态括号检查不能替代目标环境的冒烟验证。
- `shapes` 需要 **numpy**（`pixel` / `frames` / `anim` 只需要 Pillow）。图元拟合是有损还原，
  图元数量与画质直接相关，报告里的 PSNR 是唯一硬指标。
- `shapes` 不做 overpaint、不做不规则多边形拟合；`--kinds` 之外的形状没有实现。
- `frames` 只实现"颜色分组前缀池 + 每帧绝对几何"一种格式，未做 I/P 帧差分与候选体积比较。
- `anim` 采样成逐帧绝对状态，不做差分压缩、不直接生成引擎补间调用；事件需要接入方自己分发。
- 像素画的网格推断要求"整数倍放大且单元内同色"；JPEG 噪声图会退回 `native`，需要人工判断或显式传 `--grid`。
- `--max-colors` / `--tau` 是有损的，工具不会自己启用；默认保真优先。
