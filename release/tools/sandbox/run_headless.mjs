#!/usr/bin/env node
/**
 * 无头跑脚本：不进编辑器、不开浏览器，直接把客户端 Lua 脚本跑起来并出一份报告。
 *
 *     node run_headless.mjs <脚本.lua> [选项]
 *
 * 选项：
 *   --preset <id>        设备预设（默认 pc-16-9，见 presets）
 *   --canvas <WxH>       直接指定画布尺寸
 *   --ms <毫秒>          模拟时长（默认 3000）
 *   --frames <帧数>      按帧数模拟（优先于 --ms）
 *   --fuzz <毫秒>        追加随机输入轰炸
 *   --fuzz-seed <n>      轰炸随机种子
 *   --click <x,y>        在指定位置点一下（可重复）
 *   --play <文件.json>   按脚本化输入序列试玩：[{at,action,...}]
 *   --annotations <文件> 先加载接口标注文件，让真实常量覆盖沙箱占位值
 *   --out <文件.json>    把报告写到文件
 *   --json               只输出 JSON
 *
 * 退出码：0 通过（或有警告）/ 2 失败（脚本报错）/ 3 用法或文件问题
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const fengari = require(path.join(here, "vendor", "fengari.bundle.js"));
const QxSandbox = require(path.join(here, "qx-sandbox.js"));

function usage(message) {
  if (message) console.error("错误：" + message);
  console.error("用法：node run_headless.mjs <脚本.lua> [--preset pc-16-9] [--ms 3000] [--fuzz 3000] [--json]");
  process.exit(3);
}

const argv = process.argv.slice(2);
if (!argv.length) usage("缺少脚本路径");
const scriptPath = argv[0];
if (!fs.existsSync(scriptPath)) usage("找不到脚本：" + scriptPath);

const opts = { preset: "pc-16-9", canvas: null, ms: 3000, frames: 0, fuzz: 0, fuzzSeed: 20260101, clicks: [], play: null, annotations: null, out: null, json: false };
for (let i = 1; i < argv.length; i++) {
  const a = argv[i];
  const next = () => argv[++i];
  if (a === "--preset") opts.preset = next();
  else if (a === "--canvas") opts.canvas = next();
  else if (a === "--ms") opts.ms = Number(next());
  else if (a === "--frames") opts.frames = Number(next());
  else if (a === "--fuzz") opts.fuzz = Number(next());
  else if (a === "--fuzz-seed") opts.fuzzSeed = Number(next());
  else if (a === "--click") { const [x, y] = String(next()).split(",").map(Number); opts.clicks.push({ x, y }); }
  else if (a === "--play") opts.play = next();
  else if (a === "--annotations") opts.annotations = next();
  else if (a === "--out") opts.out = next();
  else if (a === "--json") opts.json = true;
  else usage("未知选项：" + a);
}

const preset = QxSandbox.presets.find((p) => p.id === opts.preset) || QxSandbox.presets[0];
let canvas = { width: preset.width, height: preset.height };
if (opts.canvas) {
  const [w, h] = opts.canvas.split("x").map(Number);
  if (!w || !h) usage("--canvas 需要写成 WxH");
  canvas = { width: w, height: h };
}

const sandbox = QxSandbox.createSandbox({
  fengari,
  canvas,
  device: preset.device,
  dt: 1 / 60,
});
sandbox.installApi();

if (opts.annotations) {
  if (!fs.existsSync(opts.annotations)) usage("找不到接口标注文件：" + opts.annotations);
  const ok = sandbox.loadAnnotations(fs.readFileSync(opts.annotations, "utf8"));
  if (!ok) console.error("提示：接口标注文件没跑通，已忽略，继续用沙箱占位常量");
}

const source = fs.readFileSync(scriptPath, "utf8");
if (!sandbox.load(source, path.basename(scriptPath))) {
  const report = sandbox.report({ stage: "load" });
  emit(report);
  process.exit(2);
}
sandbox.start();

if (opts.frames) sandbox.step(opts.frames);
else sandbox.runMs(opts.ms);

for (const c of opts.clicks) sandbox.click(c.x, c.y);

let fuzzResult = null;
if (opts.fuzz > 0) {
  fuzzResult = sandbox.fuzz({ durationMs: opts.fuzz, seed: opts.fuzzSeed });
}

let playResult = null;
if (opts.play) {
  const seq = JSON.parse(fs.readFileSync(opts.play, "utf8"));
  sandbox.play(seq.actions || seq, seq.totalMs);
  playResult = { events: (seq.actions || seq).length, totalMs: seq.totalMs || null };
}

sandbox.destroy();
const report = sandbox.report({ stage: "done", fuzz: fuzzResult, play: playResult, script: path.resolve(scriptPath) });
emit(report);
process.exit(report.verdict === "fail" ? 2 : 0);

function emit(report) {
  if (opts.out) {
    fs.writeFileSync(opts.out, JSON.stringify(report, null, 1), "utf8");
  }
  if (opts.json) {
    console.log(JSON.stringify(report, null, 1));
    return;
  }
  const r = report;
  console.log("=== 体外沙箱报告 ===");
  console.log("脚本：" + report.extra.script);
  console.log("画布：" + r.sandbox.canvas.width + "x" + r.sandbox.canvas.height + "（" + r.sandbox.device + "）");
  console.log("模拟：" + r.frames + " 帧 / " + r.timeMs + " ms");
  console.log("判定：" + r.verdict);
  console.log("控件：存活 " + r.controls.alive + "，创建 " + r.controls.created + "，销毁 " + r.controls.destroyed
    + "，启动后创建 " + r.controls.createdAfterStart + "，建了没用 " + r.controls.neverActivated);
  console.log("调用：每帧最多 " + r.calls.maxSettersPerFrame + " 次 setter，合计 " + r.calls.totalSetterCalls);
  if (r.errors.length) {
    console.log("错误 " + r.errors.length + " 条：");
    r.errors.slice(0, 10).forEach((e) => console.log("  [" + e.time + "ms] " + e.message));
  }
  if (r.unsupported.length) {
    console.log("未支持的 API：");
    r.unsupported.forEach((u) => console.log("  " + u.name + " ×" + u.count));
  }
  if (r.warnings.length) {
    console.log("警告：" + r.warnings.slice(0, 5).join("；"));
  }
  if (opts.out) console.log("报告已写入：" + path.resolve(opts.out));
}
