/**
 * 把 vendor/fengari/src 下的 CommonJS 模块打成单文件浏览器包。
 *
 * 产物：vendor/fengari.bundle.js —— 浏览器 <script> 直接可用（file:// 也能跑），
 * Node 下 require 同一份产物，保证沙箱在两端跑的是同一个 Lua 虚拟机。
 *
 *     node build-vendor.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.join(here, "vendor", "fengari", "src");
const outFile = path.join(here, "vendor", "fengari.bundle.js");

/** 需要打进包里的第三方模块：id -> 源文件 */
const extraModules = {
  "sprintf-js": path.join(here, "vendor", "sprintf-js", "src", "sprintf.js"),
};

/** 宿主环境才有的模块：浏览器里用抛错桩替代（客户端脚本用不到文件/进程/终端） */
const EXTERNALS = ["fs", "path", "os", "child_process", "readline-sync", "tmp"];

const files = fs.readdirSync(srcDir).filter((name) => name.endsWith(".js")).sort();
const parts = [];
const wanted = new Set(files);

/** 只处理真正的代码，不碰注释里的 require（fengari 里有注释掉的 bit32 依赖） */
function requireDeps(code) {
  const out = [];
  const re = /require\(\s*["']([^"']+)["']\s*\)/g;
  let m;
  while ((m = re.exec(code)) !== null) {
    const lineStart = code.lastIndexOf("\n", m.index) + 1;
    const line = code.slice(lineStart, m.index);
    if (line.includes("//")) continue;
    out.push({ raw: m[0], dep: m[1], index: m.index });
  }
  return out;
}

for (const name of files) {
  let code = fs.readFileSync(path.join(srcDir, name), "utf8");
  for (const { dep } of requireDeps(code)) {
    const id = dep.replace(/^\.\//, "");
    if (!wanted.has(id) && !EXTERNALS.includes(id) && !(id in extraModules)) {
      throw new Error(`${name} 依赖了未声明的模块：${dep}`);
    }
  }
  const deps = requireDeps(code).sort((a, b) => b.index - a.index);
  for (const { raw, dep } of deps) {
    code = code.slice(0, code.indexOf(raw)) + `__req(${JSON.stringify(dep.replace(/^\.\//, ""))})` + code.slice(code.indexOf(raw) + raw.length);
  }
  parts.push(`__define(${JSON.stringify(name)}, function (module, exports, __req) {\n${code}\n});`);
}

for (const [id, file] of Object.entries(extraModules)) {
  let code = fs.readFileSync(file, "utf8");
  code = code.replace(/require\(\s*["']\.\/([^"']+)["']\s*\)/g, (_m, dep) => `__req(${JSON.stringify(dep)})`);
  parts.push(`__define(${JSON.stringify(id)}, function (module, exports, __req) {\n${code}\n});`);
  wanted.add(id);
}

const externalsBlock = EXTERNALS.map((id) => `  ${JSON.stringify(id)}: __external(${JSON.stringify(id)}),`).join("\n");

const banner = `/**
 * 本地内置的 Lua 5.3 虚拟机（MIT，见 vendor/fengari/LICENSE）。
 * 由 build-vendor.mjs 从 vendor/fengari/src 打包生成，请勿手工编辑。
 * 生成时间：${new Date().toISOString()}
 */
`;
const out = `${banner}(function (global) {
"use strict";
var __mods = {}, __cache = {};
function __define(id, fn) { __mods[id] = fn; }
function __external(id) {
  var message = "体外沙箱没有实现 " + id + "（客户端脚本用不到文件/进程/终端）";
  function thrower() { throw new Error(message); }
  if (id === "path") {
    return { sep: "/", delimiter: ":", join: function () { return Array.prototype.join.call(arguments, "/"); },
             resolve: function () { return Array.prototype.join.call(arguments, "/"); },
             dirname: function (p) { return String(p).replace(/\\/[^/]*$/, "") || "."; },
             basename: function (p) { return String(p).split("/").pop(); }, extname: function () { return ""; } };
  }
  if (id === "os") {
    return { platform: function () { return "browser"; }, EOL: "\\n", tmpdir: function () { return "/tmp"; } };
  }
  if (id === "readline-sync") {
    // ldblib 在模块加载时就会调用 setDefaultOptions，不能直接抛错
    return { setDefaultOptions: function () {}, question: thrower, prompt: thrower, keyIn: thrower };
  }
  if (id === "tmp") {
    return { fileSync: thrower, dirSync: thrower, tmpNameSync: thrower };
  }
  if (id === "child_process") {
    return { execSync: thrower, spawnSync: thrower, exec: thrower, spawn: thrower };
  }
  return { readFileSync: thrower, writeFileSync: thrower, existsSync: function () { return false; },
           openSync: thrower, closeSync: thrower, readSync: thrower, writeSync: thrower,
           statSync: thrower, mkdirSync: thrower, unlinkSync: thrower, readdirSync: function () { return []; } };
}
var __ext = {
${externalsBlock}
};
function __req(id) {
  if (Object.prototype.hasOwnProperty.call(__ext, id)) return __ext[id];
  if (__cache[id]) return __cache[id].exports;
  if (!__mods[id]) throw new Error("模块未打包：" + id);
  var m = { exports: {} };
  __cache[id] = m;
  __mods[id](m, m.exports, __req);
  return m.exports;
}
${parts.join("\n")}
var api = __req("fengari.js");
if (typeof module !== "undefined" && module.exports) { module.exports = api; }
else { global.fengari = api; }
})(typeof globalThis !== "undefined" ? globalThis : this);
`;

fs.writeFileSync(outFile, out, "utf8");
console.log(`打包完成：${path.relative(here, outFile)}  ${(out.length / 1024).toFixed(1)} KB  内置模块 ${wanted.size} 个  外部桩 ${EXTERNALS.length} 个`);
