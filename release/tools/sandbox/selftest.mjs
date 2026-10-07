#!/usr/bin/env node
/**
 * 沙箱自测：验证 Lua 能跑、能抓错、工具生成的脚本能过。
 *
 *     node selftest.mjs                     # 跑内置样例
 *     node selftest.mjs --with-generated ../tests/out/sandbox   # 再跑工具生成的脚本
 *
 * 全部通过退出码 0；任何一项失败退出码 1。
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const fengari = require(path.join(here, "vendor", "fengari.bundle.js"));
const QxSandbox = require(path.join(here, "qx-sandbox.js"));

let failures = 0;
let passes = 0;
function check(name, ok, detail) {
  console.log((ok ? "[PASS] " : "[FAIL] ") + name + (detail ? "  -> " + detail : ""));
  if (ok) passes++; else failures++;
}

function newSandbox(script) {
  const sandbox = QxSandbox.createSandbox({ fengari, canvas: { width: 1600, height: 900 }, device: "Keyboard", dt: 1 / 60 });
  sandbox.installApi();
  const loaded = sandbox.load(script, "selftest.lua");
  return { sandbox, loaded };
}

// ---------- 1. Lua 虚拟机 ----------
const L = fengari.lauxlib.luaL_newstate();
fengari.lualib.luaL_openlibs(L);
const luaOk = fengari.lauxlib.luaL_dostring(L, fengari.to_luastring("return string.format('%d-%.1f', 7, 1.5)"));
check("Lua 虚拟机可用（含 string.format）",
  luaOk === fengari.lua.LUA_OK && fengari.to_jsstring(fengari.lua.lua_tostring(L, -1)) === "7-1.5");

// ---------- 2. 正例：demo.lua ----------
const demo = fs.readFileSync(path.join(here, "samples", "demo.lua"), "utf8");
const d = newSandbox(demo);
check("样例脚本装载成功", d.loaded);
d.sandbox.start();
d.sandbox.runMs(1500);
const panelBefore = d.sandbox.controls()[0];
d.sandbox.click(panelBefore.rect.cx, panelBefore.rect.cy);
d.sandbox.fuzz({ durationMs: 800, seed: 7 });
const demoReport = d.sandbox.report();
d.sandbox.destroy();
check("样例脚本判定通过", demoReport.verdict === "pass", JSON.stringify(demoReport.errors.slice(0, 2)));
check("样例脚本没有未支持 API", demoReport.unsupported.length === 0, JSON.stringify(demoReport.unsupported));
check("样例脚本控件已激活", demoReport.controls.neverActivated === 0);
check("样例脚本每帧 setter 有统计", demoReport.calls.maxSettersPerFrame > 0, String(demoReport.calls.maxSettersPerFrame));
check("点击命中控件并触发了 Lua 回调", demoReport.logTail.some((l) => l.message.includes("被点了")));

// ---------- 3. 反例：broken.lua ----------
const broken = fs.readFileSync(path.join(here, "samples", "broken.lua"), "utf8");
const b = newSandbox(broken);
check("反面脚本装载成功（错误发生在运行期）", b.loaded);
b.sandbox.start();
b.sandbox.runMs(500);
const brokenReport = b.sandbox.report();
check("反面脚本判定失败", brokenReport.verdict === "fail");
check("抓到错误接口名 script:EnableTick",
  brokenReport.unsupported.some((u) => u.name.includes("EnableTick")),
  JSON.stringify(brokenReport.unsupported));
check("抓到「控件建了没激活」", brokenReport.controls.neverActivated >= 1, String(brokenReport.controls.neverActivated));

// ---------- 4. OnDestroy 清理 ----------
const c = newSandbox(demo);
c.sandbox.start();
c.sandbox.runMs(300);
const aliveBefore = c.sandbox.report().controls.alive;
c.sandbox.destroy();
const afterReport = c.sandbox.report();
check("销毁后控件被清理", aliveBefore > 0 && afterReport.controls.alive === 0,
  aliveBefore + " -> " + afterReport.controls.alive);

// ---------- 5. 设备预设 ----------
check("分辨率矩阵预设齐全", QxSandbox.presets.length === 8,
  QxSandbox.presets.map((p) => p.id).join(","));

// ---------- 6. 工具生成的脚本 ----------
const argIndex = process.argv.indexOf("--with-generated");
if (argIndex > 0) {
  const dir = process.argv[argIndex + 1];
  if (!fs.existsSync(dir)) {
    check("工具生成目录存在", false, dir);
  } else {
    const files = [];
    (function walk(dir) {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (entry.name.endsWith(".lua")) files.push(full);
      }
    })(dir);
    check("找到工具生成的脚本", files.length > 0, files.map((f) => path.relative(dir, f)).join(","));
    for (const file of files) {
      const label = path.relative(dir, file).replace(/\\/g, "/");
      const s = newSandbox(fs.readFileSync(file, "utf8"));
      s.sandbox.start();
      s.sandbox.runMs(1500);
      s.sandbox.fuzz({ durationMs: 500, seed: 11 });
      const report = s.sandbox.report();
      s.sandbox.destroy();
      check("生成脚本可运行且通过：" + label, report.verdict === "pass",
        JSON.stringify(report.errors.slice(0, 2)) + " unsupported=" + JSON.stringify(report.unsupported));
      check("生成脚本控件全部激活：" + label, report.controls.neverActivated === 0,
        "neverActivated=" + report.controls.neverActivated);
    }
  }
}

console.log("\nPASS=" + passes + "  FAIL=" + failures);
process.exit(failures ? 1 : 0);
