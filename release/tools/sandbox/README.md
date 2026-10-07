# 体外沙箱 —— 不进编辑器也能跑脚本、模拟试玩

把客户端 Lua 脚本放进一个**局部模拟的运行时**里跑起来：自带 Lua 5.3 虚拟机、虚拟时钟、控件模型、
鼠标/按键/手柄输入模拟，并输出结构化报告。目的是在真机试玩之前，先把"能不能跑、有没有明显写错、
播放期有没有乱创建控件"这些问题筛掉。

## 怎么用

### 方式一：打开网页（推荐给人用）

双击 `index.html` 即可（纯静态、离线、无需服务器、无需联网）：

1. 把生成的 `levelScript.lua` 粘进左侧文本框（或直接把文件拖进页面）；
2. 选设备形态（对应分辨率矩阵的 8 行）；
3. 点「装载 → 启动」，画布上会实时画出控件；**在画布上点击就是点击事件**；
4. 点「随机输入轰炸」用随机点击/按键把脚本压一遍；
5. 点「销毁」验证 `OnDestroy` 有没有漏清理；点「出报告」导出完整 JSON 报告。

### 方式二：命令行（推荐给 AI 用）

```bash
node run_headless.mjs <脚本.lua> [选项]

# 常见用法
node run_headless.mjs levelScript.lua --preset mobile-16-9 --ms 3000
node run_headless.mjs levelScript.lua --frames 600 --fuzz 3000 --fuzz-seed 7
node run_headless.mjs levelScript.lua --click 800,450 --out report.json --json
node run_headless.mjs levelScript.lua --annotations annotations.lua
```

| 选项 | 说明 |
|---|---|
| `--preset <id>` | 设备预设：`pc-16-9`（默认）、`pc-21-9`、`mobile-16-9`、`mobile-19.5-9`、`mobile-4-3`、`pad-16-9`、`pad-21-9`、`mobile-pad` |
| `--canvas <WxH>` | 直接指定画布尺寸 |
| `--ms <毫秒>` / `--frames <帧数>` | 模拟时长（默认 3000ms）/ 按帧数 |
| `--fuzz <毫秒>` `--fuzz-seed <n>` | 随机输入轰炸时长与种子（可复现） |
| `--click <x,y>` | 在指定位置点一下（可重复） |
| `--play <文件.json>` | 按脚本化序列试玩：`[{at,action,...}]`，action 支持 click / move / drag / key / stick |
| `--annotations <文件>` | 先加载接口标注，让真实常量覆盖沙箱占位值 |
| `--out <文件>` / `--json` | 写报告 / 只输出 JSON |

**退出码**：`0` 通过（或仅有警告）、`2` 失败（脚本报错或用到未支持接口）、`3` 用法或文件问题。

### 自测

```bash
node selftest.mjs --with-generated ../tests/out
```

覆盖：虚拟机可用、正例通过、反例被抓住、OnDestroy 清理、设备预设齐全、工具生成的脚本全部通过。

## 报告里看什么

| 字段 | 含义 | 判定 |
|---|---|---|
| `verdict` | `pass` / `warn` / `fail` | `fail` 一律不要交付 |
| `errors[]` | 运行期错误、未支持接口 | 必须为 0 |
| `unsupported[]` | 调了但沙箱没实现的 API（含正确接口名提示） | 必须为 0；否则说明脚本用了沙箱/真机都没有的写法 |
| `controls.neverActivated` | 建了但**从没 `SetActive(true)`** 的控件 | 必须为 0，否则真机上根本不显示 |
| `controls.createdAfterStart` / `destroyedAfterStart` | 启动后才创建/销毁的控件 | 播放期应当为 0（对应"播放期零创建零销毁"） |
| `calls.maxSettersPerFrame` | 单帧最多调了多少次 setter | 用来对照性能预算 |
| `logTail[]` | print / printerr 输出 | 排查用 |

## 它模拟了什么

- **生命周期**：`OnInit` / `OnStart` / `OnEnable` / `OnUpdate(dt)` / `OnLevelUpdate(dt)` / `OnDisable` / `OnDestroy`；
- **全局 API**：`game.*`（控件创建/销毁/查找、画布尺寸、光标位置、设备、摇杆轴、关卡时停、音效、补间、服务器信号、自定义变量、`PrintClientUITree`）、`Color.*`、`Enum.*`、`script:*`、`typeof`、`math.isnan/isinf`、`print/printerr`；
- **控件**：字段（`active`/`visible`/锚点/中心/尺寸/位置/缩放/旋转/图片字段…）与方法（`SetActive`/`SetVisible`/`SetAnchoredPosition`/`SetSizeDelta`/`SetAnchorMin/Max`/`SetPivot`/`SetLocalRotation`/`SetImage`/填充/光标与按键监听/子控件查询…）；
- **默认值按接口文档**：新建控件 `active=false`，必须显式 `SetActive(true)` 才会显示；
- **输入**：命中测试（后创建、后置顶的优先）、光标进入/离开/按下/抬起/点击/拖拽、按键事件、手柄导航、摇杆轴；
- **虚拟时钟**：固定步长（默认 1/60s），`dt` 完全可控，随机轰炸带种子可复现。

## 它**不**模拟（别拿它当"已经通过真机"）

- 引擎渲染、遮罩/羽化/九宫格、真实字体排版；画布只是矩形与文字的近似；
- 布局模型是"锚点拉伸 + 中心偏移"的简化版，与真机可能有差异；
- 枚举值是沙箱自定的占位值（只保证同名同值）；要真值请用 `--annotations` 传入接口标注；
- 音频、服务器信号、自定义变量只做桩，不会真的收发；
- 未登记的控件方法**直接报错**而不是静默忽略——这是故意的，宁可报错也不要"看着跑了其实没实现"。

**沙箱通过 ≠ 真机通过**：报告里的 `runtime_verified` 仍然是 `pending`，真机试玩与真机结论不可省略。

## 目录

```
sandbox/
  index.html            网页沙箱（双击即用）
  app.js                网页宿主：画布渲染、控件树、日志、报告、输入模拟
  qx-sandbox.js         沙箱核心（API 桩 + 控件模型 + 虚拟时钟 + 输入；浏览器与 Node 共用）
  run_headless.mjs      命令行运行器
  selftest.mjs          自测
  samples/demo.lua      正例
  samples/broken.lua    反例（故意写错，用来看沙箱抓不抓）
  build-vendor.mjs      把 vendor/fengari/src 打成单文件浏览器包
  vendor/               内置 Lua 5.3 虚拟机（MIT）与 sprintf-js（BSD-3），各自保留 LICENSE
```

改了 `vendor/fengari/src` 里的东西后，重新打包：`node build-vendor.mjs`。
