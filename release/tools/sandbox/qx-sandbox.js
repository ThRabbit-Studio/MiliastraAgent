/**
 * qx-sandbox —— 体外沙箱（客户端脚本运行时模拟器）
 *
 * 目标：不打开编辑器，就能把客户端 Lua 脚本跑起来、驱动虚拟时钟、模拟鼠标/按键/手柄输入，
 * 并给出结构化报告（控件数、每帧调用量、未支持的 API、运行期错误）。
 *
 * 设计约束：
 *  - 本文件不依赖 DOM，浏览器用 <script> 加载后取 window.QxSandbox，
 *    Node 下 require 同一份文件，保证两端行为一致；
 *  - 渲染交给宿主（浏览器画布 / 无头记录器），核心只维护控件模型；
 *  - 不认识的 API 一律**显式报错并记录**，绝不静默吞掉。
 *
 * 坐标：与运行时文档一致——画布原点在左下角，x 向右、y 向上。
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.QxSandbox = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var VERSION = "1.0.0";

  // ------------------------------------------------------------------
  // 枚举：取值只需保证"同名同值、不同名不同值"，用于行为模拟
  // ------------------------------------------------------------------
  function enumOf(names) {
    var out = {};
    names.forEach(function (n, i) {
      out[n] = i + 1;
    });
    return out;
  }

  var ENUMS = {
    ImageSource: enumOf(["None", "BuiltIn", "Custom", "Prefab"]),
    ImageType: enumOf(["Simple", "Sliced", "Tiled", "Filled"]),
    EaseType: enumOf([
      "Linear", "InSine", "OutSine", "InOutSine", "InQuad", "OutQuad", "InOutQuad",
      "InCubic", "OutCubic", "InOutCubic", "InQuart", "OutQuart", "InOutQuart",
      "InQuint", "OutQuint", "InOutQuint", "InExpo", "OutExpo", "InOutExpo",
      "InCirc", "OutCirc", "InOutCirc", "InBack", "OutBack", "InOutBack",
      "InElastic", "OutElastic", "InOutElastic", "InBounce", "OutBounce", "InOutBounce",
    ]),
    Device: enumOf(["Keyboard", "Controller", "Touch", "Unknown"]),
    StageMode: enumOf(["Single", "Multi", "TestPlay"]),
    LanguageType: enumOf(["CHS", "CHT", "EN", "JP", "KR"]),
    CursorEventType: enumOf(["Enter", "Exit", "Down", "Up", "Click", "BeginDrag", "Drag", "EndDrag", "LongPress"]),
    KeyEventType: enumOf(["Down", "Up", "LongPress"]),
    ControllerNavigationEventType: enumOf(["Enter", "Exit", "Confirm", "Cancel"]),
    ControllerNavigationDir: enumOf(["Up", "Down", "Left", "Right"]),
    ControllerNavigationMode: enumOf(["None", "Explicit", "Automatic"]),
    ImageFillType: enumOf(["None", "Horizontal", "Vertical", "Radial90", "Radial180", "Radial360"]),
    ImageFillHorizontalType: enumOf(["LeftToRight", "RightToLeft"]),
    ImageFillVerticalType: enumOf(["BottomToTop", "TopToBottom"]),
    ImageFillRadial90Type: enumOf(["BottomLeft", "TopLeft", "TopRight", "BottomRight"]),
    ImageFillRadialType: enumOf(["Bottom", "Left", "Top", "Right"]),
    ImageMaskSoftEdgeMode: enumOf(["None", "Horizontal", "Vertical", "Both"]),
    TextHorizontalAlignment: enumOf(["Left", "Center", "Right"]),
    TextVerticalAlignment: enumOf(["Top", "Middle", "Bottom"]),
    ParamType: enumOf(["Int", "Float", "Bool", "String", "Vector3", "Entity", "Guid", "ConfigId", "PrefabId"]),
    UIAnimationLayer: enumOf(["Default", "Low", "High"]),
    CustomVariableEntityType: enumOf(["Player", "Level", "Global"]),
    ScrollDirection: enumOf(["Horizontal", "Vertical"]),
    ScrollLayoutConstraint: enumOf(["Unconstrained", "Flexible"]),
    ScrollAlignType: enumOf(["Start", "Center", "End"]),
  };

  // ------------------------------------------------------------------
  // 设备预设：与主控分册的分辨率矩阵一致
  // ------------------------------------------------------------------
  var DEVICE_PRESETS = [
    { id: "pc-16-9", name: "电脑 16:9", width: 1600, height: 900, device: "Keyboard" },
    { id: "pc-21-9", name: "电脑 21:9", width: 2100, height: 900, device: "Keyboard" },
    { id: "mobile-16-9", name: "手机 16:9", width: 1280, height: 720, device: "Touch" },
    { id: "mobile-19.5-9", name: "手机 19.5:9", width: 1560, height: 720, device: "Touch" },
    { id: "mobile-4-3", name: "手机 4:3", width: 1280, height: 960, device: "Touch" },
    { id: "pad-16-9", name: "电脑手柄 16:9", width: 1920, height: 1080, device: "Controller" },
    { id: "pad-21-9", name: "电脑手柄 21:9", width: 2520, height: 1080, device: "Controller" },
    { id: "mobile-pad", name: "手机手柄", width: 1280, height: 720, device: "Controller" },
  ];

  // ------------------------------------------------------------------
  // 沙箱
  // ------------------------------------------------------------------
  function createSandbox(options) {
    var opt = options || {};
    var fengari = opt.fengari || (root && root.fengari);
    if (!fengari) throw new Error("没有找到 Lua 虚拟机：请先加载 vendor/fengari.bundle.js");
    var lua = fengari.lua;
    var lauxlib = fengari.lauxlib;
    var lualib = fengari.lualib;
    var to_luastring = fengari.to_luastring;
    var to_jsstring = fengari.to_jsstring;

    var canvas = opt.canvas || { width: 1600, height: 900 };
    var deviceName = opt.device || "Keyboard";
    var dt = opt.dt || 1 / 60;
    var maxStepsDefault = opt.maxSteps || 6000;

    var L = lauxlib.luaL_newstate();
    lualib.luaL_openlibs(L);

    // ---- 状态 ----
    var controls = [];            // 全部控件（含已销毁，alive 标记）
    var methodTable = {};         // 控件方法名 -> JS 实现
    var byId = new Map();
    var nextId = 1;
    var roots = [];
    var listeners = [];           // {controlId, kind, type, fnRef}
    var tweens = [];
    var audio = new Map();
    var nextAudioId = 1;
    var customVariables = new Map();
    var focusControlId = null;
    var cursorPos = { x: 0, y: 0 };
    var keyState = {};
    var stick = { left: [0, 0], right: [0, 0] };
    var levelPaused = false;

    var clock = 0;                // 虚拟时间（秒）
    var frames = 0;
    var updateEnabled = true;
    var levelUpdateEnabled = true;
    var started = false;
    var destroyed = false;

    var logs = [];
    var errors = [];
    var unsupported = new Map();  // name -> count
    var apiCalls = new Map();     // name -> count
    var warnings = [];
    var framesWithoutControls = 0;
    var settleStats = { maxSettersPerFrame: 0, totalSetters: 0, totalCreates: 0, totalDestroys: 0, createsAfterStart: 0, destroysAfterStart: 0 };
    var frameSetters = 0;

    function log(level, message) {
      var entry = { level: level, time: Math.round(clock * 1000), message: String(message) };
      logs.push(entry);
      if (logs.length > 2000) logs.shift();
      if (opt.onLog) opt.onLog(entry);
      return entry;
    }
    function apiCall(name) {
      apiCalls.set(name, (apiCalls.get(name) || 0) + 1);
    }
    function markUnsupported(name, where) {
      unsupported.set(name, (unsupported.get(name) || 0) + 1);
      var message = "未支持的 API：`" + name + "`" + (where ? "（" + where + "）" : "");
      errors.push({ time: Math.round(clock * 1000), message: message, kind: "unsupported-api" });
      log("error", message);
      // 用 Lua 错误抛出，保证 pcall 能接住、消息不丢
      lua.lua_pushstring(L, to_luastring(message));
      return lua.lua_error(L);
    }

    /** 安全取出栈上的错误文本（可能是字符串、也可能是 JS 异常对象） */
    function errorText(index) {
      var raw = lua.lua_tostring(L, index);
      if (raw) return to_jsstring(raw);
      try {
        lauxlib.luaL_tolstring(L, index);
        var converted = lua.lua_tostring(L, -1);
        var text = converted ? to_jsstring(converted) : "<无法转成文本的错误>";
        lua.lua_pop(L, 1);
        return text;
      } catch (e) {
        return "<无法转成文本的错误>";
      }
    }

    // ---- Lua 辅助 ----
    function pushFunction(fn) {
      lua.lua_pushjsfunction(L, fn);
    }
    function setGlobalTable(name, entries) {
      lua.lua_newtable(L);
      Object.keys(entries).forEach(function (key) {
        pushFunction(entries[key]);
        lua.lua_setfield(L, -2, to_luastring(key));
      });
      lua.lua_setglobal(L, to_luastring(name));
    }
    function callbackRef(index) {
      lua.lua_pushvalue(L, index);
      return lauxlib.luaL_ref(L, lua.LUA_REGISTRYINDEX);
    }
    function releaseRef(ref) {
      if (ref) lauxlib.luaL_unref(L, lua.LUA_REGISTRYINDEX, ref);
    }
    function callRef(ref, argPushers) {
      var base = lua.lua_gettop(L);
      lua.lua_rawgeti(L, lua.LUA_REGISTRYINDEX, ref);
      var nargs = 0;
      (argPushers || []).forEach(function (push) {
        push();
        nargs++;
      });
      var status = lua.lua_pcall(L, nargs, 1, 0);
      if (status !== lua.LUA_OK) {
        var message = errorText(-1);
        lua.lua_settop(L, base);
        errors.push({ time: Math.round(clock * 1000), message: message, kind: "lua-error" });
        log("error", "Lua 回调出错：" + message);
        return null;
      }
      var value = lua.lua_toboolean(L, -1);
      lua.lua_settop(L, base);
      return value;
    }
    function callGlobal(name, args) {
      var base = lua.lua_gettop(L);
      lua.lua_getglobal(L, to_luastring(name));
      if (lua.lua_type(L, -1) !== lua.LUA_TFUNCTION) {
        lua.lua_settop(L, base);
        return { exists: false };
      }
      var nargs = 0;
      (args || []).forEach(function (push) {
        push();
        nargs++;
      });
      var status = lua.lua_pcall(L, nargs, 0, 0);
      if (status !== lua.LUA_OK) {
        var message = errorText(-1);
        lua.lua_settop(L, base);
        errors.push({ time: Math.round(clock * 1000), message: name + " 执行出错：" + message, kind: "lua-error" });
        log("error", name + " 执行出错：" + message);
        return { exists: true, ok: false, message: message };
      }
      lua.lua_settop(L, base);
      return { exists: true, ok: true };
    }
    function pushString(s) {
      lua.lua_pushstring(L, to_luastring(String(s)));
    }
    function pushNumber(n) {
      lua.lua_pushnumber(L, Number(n));
    }
    function pushBool(b) {
      lua.lua_pushboolean(L, b ? 1 : 0);
    }
    function tableStringField(tableIndex, key) {
      lua.lua_getfield(L, tableIndex, to_luastring(key));
      var out = lua.lua_type(L, -1) === lua.LUA_TSTRING ? to_jsstring(lua.lua_tostring(L, -1)) : null;
      lua.lua_pop(L, 1);
      return out;
    }
    function numberArg(index, fallback) {
      var v = lua.lua_tonumber(L, index);
      return Number.isFinite(v) ? v : (fallback || 0);
    }

    // ---- 控件模型 ----
    function createControl(prefabIndex, parentId) {
      var parent = parentId == null ? null : byId.get(parentId);
      var control = {
        id: nextId++,
        alive: true,
        prefabIndex: prefabIndex,
        name: "control_" + nextId,
        parentId: parent ? parent.id : null,
        children: [],
        active: false,
        visible: true,
        anchoredPositionX: 0,
        anchoredPositionY: 0,
        sizeDeltaX: 0,
        sizeDeltaY: 0,
        anchorMinX: 0.5,
        anchorMinY: 0.5,
        anchorMaxX: 0.5,
        anchorMaxY: 0.5,
        pivotX: 0.5,
        pivotY: 0.5,
        localScaleX: 1,
        localScaleY: 1,
        localScaleZ: 1,
        localRotationX: 0,
        localRotationY: 0,
        localRotationZ: 0,
        canControllerFocus: false,
        imageSource: 0,
        imageId: 0,
        imageColor: { r: 255, g: 255, b: 255, a: 255 },
        imageType: ENUMS.ImageType.Simple,
        fillType: ENUMS.ImageFillType.None,
        fillAmount: 1,
        text: "",
        listeners: [],
        siblingIndex: 0,
        everActivated: false,
      };
      controls.push(control);
      byId.set(control.id, control);
      if (parent) {
        parent.children.push(control.id);
        control.siblingIndex = parent.children.length - 1;
      } else {
        roots.push(control.id);
        control.siblingIndex = roots.length - 1;
      }
      settleStats.totalCreates++;
      if (started) settleStats.createsAfterStart++;
      return control;
    }

    function destroyControl(control) {
      if (!control || !control.alive) return;
      control.alive = false;
      control.active = false;
      if (control.parentId != null) {
        var parent = byId.get(control.parentId);
        if (parent) parent.children = parent.children.filter(function (id) { return id !== control.id; });
      } else {
        roots = roots.filter(function (id) { return id !== control.id; });
      }
      control.children.slice().forEach(function (id) {
        destroyControl(byId.get(id));
      });
      listeners = listeners.filter(function (item) { return item.controlId !== control.id; });
      settleStats.totalDestroys++;
      if (started) settleStats.destroysAfterStart++;
    }

    function aliveCount() {
      var n = 0;
      controls.forEach(function (c) { if (c.alive) n++; });
      return n;
    }

    function controlTable(control) {
      // 控件在 Lua 侧就是一个表：字段直接放在表里，方法通过元表 __index 提供
      lua.lua_newtable(L);
      lua.lua_pushinteger(L, control.id);
      lua.lua_setfield(L, -2, to_luastring("__id"));
      var scalar = {
        active: control.active,
        visible: control.visible,
        name: control.name,
        anchoredPositionX: control.anchoredPositionX,
        anchoredPositionY: control.anchoredPositionY,
        sizeDeltaX: control.sizeDeltaX,
        sizeDeltaY: control.sizeDeltaY,
        anchorMinX: control.anchorMinX,
        anchorMinY: control.anchorMinY,
        anchorMaxX: control.anchorMaxX,
        anchorMaxY: control.anchorMaxY,
        pivotX: control.pivotX,
        pivotY: control.pivotY,
        localScaleX: control.localScaleX,
        localScaleY: control.localScaleY,
        localScaleZ: control.localScaleZ,
        localRotationX: control.localRotationX,
        localRotationY: control.localRotationY,
        localRotationZ: control.localRotationZ,
        canControllerFocus: control.canControllerFocus,
        imageSource: control.imageSource,
        imageId: control.imageId,
        imageType: control.imageType,
        fillType: control.fillType,
        fillAmount: control.fillAmount,
        prefabIndex: control.prefabIndex,
      };
      Object.keys(scalar).forEach(function (key) {
        var value = scalar[key];
        if (typeof value === "boolean") pushBool(value);
        else if (typeof value === "number") pushNumber(value);
        else pushString(value);
        lua.lua_setfield(L, -2, to_luastring(key));
      });
      // imageColor 单独放（表）
      lua.lua_newtable(L);
      pushNumber(control.imageColor.r); lua.lua_setfield(L, -2, to_luastring("r"));
      pushNumber(control.imageColor.g); lua.lua_setfield(L, -2, to_luastring("g"));
      pushNumber(control.imageColor.b); lua.lua_setfield(L, -2, to_luastring("b"));
      pushNumber(control.imageColor.a); lua.lua_setfield(L, -2, to_luastring("a"));
      lua.lua_setfield(L, -2, to_luastring("imageColor"));
      lua.lua_pushinteger(L, control.id);
      lua.lua_setfield(L, -2, to_luastring("id"));
      return control.id;
    }

    function pushControl(control) {
      if (!control) {
        lua.lua_pushnil(L);
        return;
      }
      controlTable(control);
      lua.lua_rawgeti(L, lua.LUA_REGISTRYINDEX, controlMetatableRef);
      lua.lua_setmetatable(L, -2);
    }

    function readControlId(index) {
      if (lua.lua_type(L, index) !== lua.LUA_TTABLE) return null;
      lua.lua_getfield(L, index, to_luastring("__id"));
      var id = lua.lua_tointeger(L, -1);
      lua.lua_pop(L, 1);
      return id || null;
    }

    function controlArg(index) {
      var id = readControlId(index);
      if (id == null) markUnsupported("非控件参数", "参数 " + index);
      var control = byId.get(id);
      if (!control || !control.alive) markUnsupported("已销毁的控件", "参数 " + index);
      return control;
    }

    function pushColor(color) {
      lua.lua_newtable(L);
      pushNumber(color.r); lua.lua_setfield(L, -2, to_luastring("r"));
      pushNumber(color.g); lua.lua_setfield(L, -2, to_luastring("g"));
      pushNumber(color.b); lua.lua_setfield(L, -2, to_luastring("b"));
      pushNumber(color.a); lua.lua_setfield(L, -2, to_luastring("a"));
    }

    function colorFromArgs(index) {
      var r = numberArg(index, 255), g = numberArg(index + 1, 255), b = numberArg(index + 2, 255);
      var a = lua.lua_type(L, index + 3) === lua.LUA_TNIL ? 255 : numberArg(index + 3, 255);
      return { r: clamp255(r), g: clamp255(g), b: clamp255(b), a: clamp255(a) };
    }
    function clamp255(v) {
      return Math.max(0, Math.min(255, Math.round(v)));
    }

    // ---- 布局（简化模型：锚点拉伸 + 中心偏移，画布原点左下） ----
    function rectOf(control) {
      var parentRect = control.parentId != null && byId.get(control.parentId)
        ? rectOf(byId.get(control.parentId))
        : { x: 0, y: 0, w: canvas.width, h: canvas.height };
      var stretchW = parentRect.w * (control.anchorMaxX - control.anchorMinX);
      var stretchH = parentRect.h * (control.anchorMaxY - control.anchorMinY);
      var w = stretchW + control.sizeDeltaX;
      var h = stretchH + control.sizeDeltaY;
      var scaleX = control.localScaleX || 1, scaleY = control.localScaleY || 1;
      w *= Math.abs(scaleX);
      h *= Math.abs(scaleY);
      var cx, cy;
      if (control.parentId != null) {
        cx = parentRect.x + parentRect.w / 2 + control.anchoredPositionX;
        cy = parentRect.y + parentRect.h / 2 + control.anchoredPositionY;
      } else {
        cx = control.anchoredPositionX;
        cy = control.anchoredPositionY;
      }
      return { x: cx - w * control.pivotX, y: cy - h * control.pivotY, w: w, h: h, cx: cx, cy: cy };
    }

    function isActiveInHierarchy(control) {
      var node = control;
      while (node) {
        if (!node.active) return false;
        node = node.parentId != null ? byId.get(node.parentId) : null;
      }
      return true;
    }

    function hitTest(x, y) {
      // 从上层（后加入的兄弟、后创建的根）往下找第一个命中的可见控件
      var ordered = [];
      (function walk(ids) {
        ids.forEach(function (id) {
          var c = byId.get(id);
          if (!c || !c.alive) return;
          if (c.children.length) walk(c.children);
          ordered.push(c);
        });
      })(roots);
      for (var i = ordered.length - 1; i >= 0; i--) {
        var c = ordered[i];
        if (!c.visible || !isActiveInHierarchy(c)) continue;
        var rect = rectOf(c);
        if (x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h) return c;
      }
      return null;
    }

    // ---- 事件分发 ----
    function addListener(control, kind, typeName, typeValue, fnRef) {
      listeners.push({ controlId: control.id, kind: kind, type: typeName, typeValue: typeValue, ref: fnRef });
      control.listeners.push({ kind: kind, type: typeName });
    }
    function dispatch(control, kind, typeValue, data) {
      var hits = listeners.filter(function (item) {
        return item.controlId === control.id && item.kind === kind && item.typeValue === typeValue;
      });
      var handled = false;
      hits.slice().forEach(function (item) {
        var result = callRef(item.ref, [
          function () { pushControl(control); },
          function () {
            lua.lua_newtable(L);
            pushNumber(data.x); lua.lua_setfield(L, -2, to_luastring("x"));
            pushNumber(data.y); lua.lua_setfield(L, -2, to_luastring("y"));
            pushNumber(data.dx || 0); lua.lua_setfield(L, -2, to_luastring("deltaX"));
            pushNumber(data.dy || 0); lua.lua_setfield(L, -2, to_luastring("deltaY"));
            pushNumber(data.button || 0); lua.lua_setfield(L, -2, to_luastring("button"));
          },
        ]);
        if (result === true) handled = true;
      });
      if (kind === "cursor") apiCall("cursorEvent:" + typeValue);
      return handled;
    }

    // ---- 生命周期 ----
    function runChunk(source, chunkName) {
      var status = lauxlib.luaL_loadbuffer(L, to_luastring(source), source.length, to_luastring(chunkName || "chunk"));
      if (status !== lua.LUA_OK) {
        var message = errorText(-1);
        lua.lua_pop(L, 1);
        errors.push({ time: 0, message: "编译失败：" + message, kind: "syntax" });
        log("error", "编译失败：" + message);
        return false;
      }
      status = lua.lua_pcall(L, 0, 0, 0);
      if (status !== lua.LUA_OK) {
        var err = errorText(-1);
        lua.lua_pop(L, 1);
        errors.push({ time: 0, message: "执行失败：" + err, kind: "runtime" });
        log("error", "执行失败：" + err);
        return false;
      }
      return true;
    }

    // ---- API 安装 ----
    var controlMetatableRef = 0;

    function installApi() {
      // Color
      var colorFns = {
        FromRGB: function () {
          apiCall("Color.FromRGB");
          pushColor(colorFromArgs(1));
          return 1;
        },
        FromRGBA: function () {
          apiCall("Color.FromRGBA");
          pushColor(colorFromArgs(1));
          return 1;
        },
        ToRGBA: function () {
          apiCall("Color.ToRGBA");
          if (lua.lua_type(L, 1) !== lua.LUA_TTABLE) return markUnsupported("Color.ToRGBA 参数");
          pushNumber(tableNumberField(1, "r", 255));
          pushNumber(tableNumberField(1, "g", 255));
          pushNumber(tableNumberField(1, "b", 255));
          pushNumber(tableNumberField(1, "a", 255));
          return 4;
        },
        __call: function () {
          apiCall("Color()");
          pushColor(colorFromArgs(1));
          return 1;
        },
      };
      lua.lua_newtable(L);
      Object.keys(colorFns).forEach(function (key) {
        pushFunction(colorFns[key]);
        lua.lua_setfield(L, -2, to_luastring(key));
      });
      lua.lua_newtable(L);
      pushFunction(function () {
        return colorFns.__call();
      });
      lua.lua_setfield(L, -2, to_luastring("__call"));
      lua.lua_setmetatable(L, -2);
      lua.lua_setglobal(L, to_luastring("Color"));

      // Enum
      lua.lua_newtable(L);
      Object.keys(ENUMS).forEach(function (name) {
        lua.lua_newtable(L);
        var table = ENUMS[name];
        Object.keys(table).forEach(function (key) {
          lua.lua_pushinteger(L, table[key]);
          lua.lua_setfield(L, -2, to_luastring(key));
        });
        lua.lua_setfield(L, -2, to_luastring(name));
      });
      lua.lua_setglobal(L, to_luastring("Enum"));

      // 控件方法表（元表 __index 用 JS 函数分发）
      installControlMethods();
      lua.lua_newtable(L);
      pushFunction(controlIndex);
      lua.lua_setfield(L, -2, to_luastring("__index"));
      controlMetatableRef = lauxlib.luaL_ref(L, lua.LUA_REGISTRYINDEX);

      // game
      var gameFns = {
        InstantiateClientUIControl: function () {
          apiCall("game.InstantiateClientUIControl");
          var prefabIndex = lua.lua_tointeger(L, 1);
          var parent = lua.lua_type(L, 2) === lua.LUA_TNIL ? null : controlArg(2);
          var control = createControl(prefabIndex, parent ? parent.id : null);
          pushControl(control);
          return 1;
        },
        DestroyClientUIControl: function () {
          apiCall("game.DestroyClientUIControl");
          var control = controlArg(1);
          if (!control.alive) markUnsupported("game.DestroyClientUIControl", "对象已销毁");
          destroyControl(control);
          return 0;
        },
        GetClientUIControl: function () {
          apiCall("game.GetClientUIControl");
          var control = byId.get(lua.lua_tointeger(L, 1));
          pushControl(control && control.alive ? control : null);
          return 1;
        },
        FindClientUIRoot: function () {
          apiCall("game.FindClientUIRoot");
          var name = to_jsstring(lua.lua_tostring(L, 1));
          var found = null;
          roots.some(function (id) {
            var c = byId.get(id);
            if (c && c.alive && c.name === name) { found = c; return true; }
            return false;
          });
          pushControl(found);
          return 1;
        },
        GetClientUIRoots: function () {
          apiCall("game.GetClientUIRoots");
          lua.lua_newtable(L);
          roots.forEach(function (id, i) {
            var c = byId.get(id);
            if (!c || !c.alive) return;
            pushControl(c);
            lua.lua_seti(L, -2, i + 1);
          });
          return 1;
        },
        GetUICanvasSize: function () {
          apiCall("game.GetUICanvasSize");
          pushNumber(canvas.width);
          pushNumber(canvas.height);
          return 2;
        },
        GetCursorUIPos: function () {
          apiCall("game.GetCursorUIPos");
          pushNumber(cursorPos.x);
          pushNumber(cursorPos.y);
          return 2;
        },
        GetDevice: function () {
          apiCall("game.GetDevice");
          lua.lua_pushinteger(L, ENUMS.Device[deviceName] || ENUMS.Device.Keyboard);
          return 1;
        },
        SetControllerFocus: function () {
          apiCall("game.SetControllerFocus");
          var control = lua.lua_type(L, 1) === lua.LUA_TNIL ? null : controlArg(1);
          focusControlId = control ? control.id : null;
          return 0;
        },
        GetControllerFocus: function () {
          apiCall("game.GetControllerFocus");
          var c = focusControlId != null ? byId.get(focusControlId) : null;
          pushControl(c && c.alive ? c : null);
          return 1;
        },
        GetControllerLeftStickAxis: function () {
          apiCall("game.GetControllerLeftStickAxis");
          pushNumber(stick.left[0]); pushNumber(stick.left[1]);
          return 2;
        },
        GetControllerRightStickAxis: function () {
          apiCall("game.GetControllerRightStickAxis");
          pushNumber(stick.right[0]); pushNumber(stick.right[1]);
          return 2;
        },
        Tween: function () {
          apiCall("game.Tween");
          var control = controlArg(1);
          var duration = numberArg(3, 0.3);
          var data = {};
          if (lua.lua_type(L, 2) === lua.LUA_TTABLE) {
            lua.lua_pushnil(L);
            while (lua.lua_next(L, 2) !== 0) {
              var key = to_jsstring(lua.lua_tostring(L, -2));
              data[key] = lua.lua_tonumber(L, -1);
              lua.lua_pop(L, 1);
            }
          }
          var tween = { controlId: control.id, data: data, duration: duration, elapsed: 0, from: {}, done: false, loops: 1, played: 0 };
          Object.keys(data).forEach(function (key) { tween.from[key] = control[key]; });
          tweens.push(tween);
          pushTween(tween);
          return 1;
        },
        TweenSequence: function () {
          apiCall("game.TweenSequence");
          pushSequence();
          return 1;
        },
        ServerSignal: function () {
          apiCall("game.ServerSignal");
          var name = to_jsstring(lua.lua_tostring(L, 1));
          lua.lua_newtable(L);
          pushString(name); lua.lua_setfield(L, -2, to_luastring("signalName"));
          pushFunction(function () { apiCall("ServerSignal:SendSignal"); return 0; });
          lua.lua_setfield(L, -2, to_luastring("SendSignal"));
          return 1;
        },
        GetGlobalCustomVariableValue: function () {
          apiCall("game.GetGlobalCustomVariableValue");
          var key = to_jsstring(lua.lua_tostring(L, 2));
          var value = customVariables.get(key);
          if (value === undefined) lua.lua_pushnil(L);
          else pushString(value);
          return 1;
        },
        PauseLevelTime: function () {
          apiCall("game.PauseLevelTime");
          levelPaused = lua.lua_toboolean(L, 1);
          return 0;
        },
        IsLevelTimePaused: function () {
          apiCall("game.IsLevelTimePaused");
          pushBool(levelPaused);
          return 1;
        },
        PlayAudio2D: function () {
          apiCall("game.PlayAudio2D");
          var id = nextAudioId++;
          audio.set(id, { alive: true, id: lua.lua_tointeger(L, 1), until: clock + 1 });
          lua.lua_pushinteger(L, id);
          return 1;
        },
        StopAudio: function () {
          apiCall("game.StopAudio");
          var inst = audio.get(lua.lua_tointeger(L, 1));
          if (inst) inst.alive = false;
          return 0;
        },
        IsAudioAlive: function () {
          apiCall("game.IsAudioAlive");
          var inst = audio.get(lua.lua_tointeger(L, 1));
          pushBool(!!(inst && inst.alive));
          return 1;
        },
        GetLanguageType: function () {
          apiCall("game.GetLanguageType");
          lua.lua_pushinteger(L, ENUMS.LanguageType.CHS);
          return 1;
        },
        GetStageMode: function () {
          apiCall("game.GetStageMode");
          lua.lua_pushinteger(L, ENUMS.StageMode.Single);
          return 1;
        },
        IsTestPlay: function () {
          apiCall("game.IsTestPlay");
          pushBool(true);
          return 1;
        },
        PrintClientUITree: function () {
          apiCall("game.PrintClientUITree");
          log("info", describeTree());
          return 0;
        },
      };
      setGlobalTable("game", gameFns);

      // script（当前脚本实例）
      lua.lua_newtable(L);
      lua.lua_pushinteger(L, 1001); lua.lua_setfield(L, -2, to_luastring("scriptMappingId"));
      pushBool(true); lua.lua_setfield(L, -2, to_luastring("alive"));
      pushBool(true); lua.lua_setfield(L, -2, to_luastring("enabled"));
      pushString("sandbox://levelScript.lua"); lua.lua_setfield(L, -2, to_luastring("path"));
      pushFunction(function () {
        apiCall("script:GetParam");
        lua.lua_pushnil(L);
        return 1;
      });
      lua.lua_setfield(L, -2, to_luastring("GetParam"));
      pushFunction(function () {
        apiCall("script:Invoke");
        var name = to_jsstring(lua.lua_tostring(L, 2));
        var base = lua.lua_gettop(L);
        lua.lua_getglobal(L, to_luastring(name));
        if (lua.lua_type(L, -1) !== lua.LUA_TFUNCTION) {
          lua.lua_settop(L, base);
          return 0;
        }
        lua.lua_insert(L, 3);
        var nargs = base - 2;
        var status = lua.lua_pcall(L, nargs, 0, 0);
        if (status !== lua.LUA_OK) {
          errors.push({ time: Math.round(clock * 1000), message: "script:Invoke(" + name + ") 出错：" + errorText(-1), kind: "lua-error" });
          lua.lua_settop(L, base);
        }
        return 0;
      });
      lua.lua_setfield(L, -2, to_luastring("Invoke"));
      pushFunction(function () {
        apiCall("script:EnableUpdate");
        updateEnabled = lua.lua_toboolean(L, 1);
        return 0;
      });
      lua.lua_setfield(L, -2, to_luastring("EnableUpdate"));
      pushFunction(function () {
        // 兼容旧写法：真机接口叫 EnableUpdate，这里显式报错，避免静默跑偏
        markUnsupported("script:EnableTick", "正确接口是 script:EnableUpdate");
      });
      lua.lua_setfield(L, -2, to_luastring("EnableTick"));
      pushFunction(function () { apiCall("script:RegisterServerSignalHandler"); return 0; });
      lua.lua_setfield(L, -2, to_luastring("RegisterServerSignalHandler"));
      pushFunction(function () { apiCall("script:UnregisterServerSignalHandler"); return 0; });
      lua.lua_setfield(L, -2, to_luastring("UnregisterServerSignalHandler"));
      pushFunction(function () { apiCall("script:RegisterCustomVariableChangedHandler"); return 0; });
      lua.lua_setfield(L, -2, to_luastring("RegisterCustomVariableChangedHandler"));
      pushFunction(function () { apiCall("script:UnregisterCustomVariableChangedHandler"); return 0; });
      lua.lua_setfield(L, -2, to_luastring("UnregisterCustomVariableChangedHandler"));
      pushControl(null);
      lua.lua_setfield(L, -2, to_luastring("object"));
      var scriptTableRef = callbackRef(-1);
      lua.lua_pop(L, 1);
      lua.lua_rawgeti(L, lua.LUA_REGISTRYINDEX, scriptTableRef);
      lua.lua_setglobal(L, to_luastring("script"));

      // print / printerr：写入沙箱日志
      lua.lua_pushjsfunction(L, function () {
        apiCall("print");
        var n = lua.lua_gettop(L);
        var parts = [];
        for (var i = 1; i <= n; i++) {
          parts.push(luaValueToString(i));
        }
        log("print", parts.join(" "));
        return 0;
      });
      lua.lua_setglobal(L, to_luastring("print"));
      lua.lua_pushjsfunction(L, function () {
        apiCall("printerr");
        var n = lua.lua_gettop(L);
        var parts = [];
        for (var i = 1; i <= n; i++) parts.push(luaValueToString(i));
        log("error", parts.join(" "));
        return 0;
      });
      lua.lua_setglobal(L, to_luastring("printerr"));

      // 运行时补充：math.isnan / math.isinf（标准 Lua 没有，接口文档里有）
      lua.lua_getglobal(L, to_luastring("math"));
      if (lua.lua_type(L, -1) === lua.LUA_TTABLE) {
        pushFunction(function () {
          apiCall("math.isnan");
          pushBool(Number.isNaN(lua.lua_tonumber(L, 1)));
          return 1;
        });
        lua.lua_setfield(L, -2, to_luastring("isnan"));
        pushFunction(function () {
          apiCall("math.isinf");
          var v = lua.lua_tonumber(L, 1);
          pushBool(!Number.isNaN(v) && !Number.isFinite(v));
          return 1;
        });
        lua.lua_setfield(L, -2, to_luastring("isinf"));
      }
      lua.lua_pop(L, 1);

      // 运行时补充：typeof(value)
      pushFunction(function () {
        apiCall("typeof");
        var t = lua.lua_type(L, 1);
        if (t === lua.LUA_TTABLE) {
          lua.lua_getfield(L, 1, to_luastring("__id"));
          var isControl = lua.lua_type(L, -1) === lua.LUA_TNUMBER;
          lua.lua_pop(L, 1);
          pushString(isControl ? "ClientUIBaseControl" : "table");
        } else if (t === lua.LUA_TNUMBER) pushString("number");
        else if (t === lua.LUA_TSTRING) pushString("string");
        else if (t === lua.LUA_TBOOLEAN) pushString("boolean");
        else if (t === lua.LUA_TFUNCTION) pushString("function");
        else if (t === lua.LUA_TNIL) pushString("nil");
        else pushString(lua.lua_typename(L, t));
        return 1;
      });
      lua.lua_setglobal(L, to_luastring("typeof"));
    }

    function tableNumberField(index, key, fallback) {
      lua.lua_getfield(L, index, to_luastring(key));
      var v = lua.lua_tonumber(L, -1);
      lua.lua_pop(L, 1);
      return Number.isFinite(v) ? v : fallback;
    }

    function luaValueToString(index) {
      var t = lua.lua_type(L, index);
      if (t === lua.LUA_TSTRING) return to_jsstring(lua.lua_tostring(L, index));
      if (t === lua.LUA_TNUMBER) return String(lua.lua_tonumber(L, index));
      if (t === lua.LUA_TBOOLEAN) return lua.lua_toboolean(L, index) ? "true" : "false";
      if (t === lua.LUA_TNIL) return "nil";
      return "<" + lua.lua_typename(L, t) + ">";
    }

    function pushTween(tween) {
      lua.lua_newtable(L);
      pushFunction(function () { apiCall("Tween:Play"); tween.played++; return 0; });
      lua.lua_setfield(L, -2, to_luastring("Play"));
      pushFunction(function () { apiCall("Tween:Pause"); return 0; });
      lua.lua_setfield(L, -2, to_luastring("Pause"));
      pushFunction(function () { apiCall("Tween:Kill"); tween.done = true; return 0; });
      lua.lua_setfield(L, -2, to_luastring("Kill"));
      pushFunction(function () { apiCall("Tween:SetLoops"); tween.loops = lua.lua_tointeger(L, 2) || 1; return 0; });
      lua.lua_setfield(L, -2, to_luastring("SetLoops"));
      pushFunction(function () { apiCall("Tween:SetEase"); return 0; });
      lua.lua_setfield(L, -2, to_luastring("SetEase"));
      pushFunction(function () { apiCall("Tween:SetRelative"); return 0; });
      lua.lua_setfield(L, -2, to_luastring("SetRelative"));
      pushFunction(function () { apiCall("Tween:SetOnComplete"); return 0; });
      lua.lua_setfield(L, -2, to_luastring("SetOnComplete"));
      pushFunction(function () { apiCall("Tween:Restart"); return 0; });
      lua.lua_setfield(L, -2, to_luastring("Restart"));
    }

    function pushSequence() {
      lua.lua_newtable(L);
      ["Append", "Join", "Insert", "AppendInterval", "AppendCallback", "InsertCallback", "Play", "Pause", "Kill", "Restart", "Complete", "SetLoops", "SetOnStepComplete"].forEach(function (name) {
        pushFunction(function () { apiCall("TweenSequence:" + name); return 0; });
        lua.lua_setfield(L, -2, to_luastring(name));
      });
    }

    function installControlMethods() {
      var methods = {
        SetActive: function () {
          apiCall("SetActive");
          var control = controlArg(1);
          control.active = lua.lua_toboolean(L, 2);
          if (control.active) control.everActivated = true;
          syncControlField(control);
          return 0;
        },
        SetVisible: function () {
          apiCall("SetVisible");
          var control = controlArg(1);
          control.visible = lua.lua_toboolean(L, 2);
          syncControlField(control);
          return 0;
        },
        SetAnchoredPosition: function () {
          apiCall("SetAnchoredPosition");
          var control = controlArg(1);
          control.anchoredPositionX = numberArg(2);
          control.anchoredPositionY = numberArg(3);
          frameSetters++;
          return 0;
        },
        SetSizeDelta: function () {
          apiCall("SetSizeDelta");
          var control = controlArg(1);
          control.sizeDeltaX = numberArg(2);
          control.sizeDeltaY = numberArg(3);
          frameSetters++;
          return 0;
        },
        SetAnchorMin: function () {
          apiCall("SetAnchorMin");
          var control = controlArg(1);
          control.anchorMinX = numberArg(2, 0.5);
          control.anchorMinY = numberArg(3, 0.5);
          frameSetters++;
          return 0;
        },
        SetAnchorMax: function () {
          apiCall("SetAnchorMax");
          var control = controlArg(1);
          control.anchorMaxX = numberArg(2, 0.5);
          control.anchorMaxY = numberArg(3, 0.5);
          frameSetters++;
          return 0;
        },
        SetPivot: function () {
          apiCall("SetPivot");
          var control = controlArg(1);
          control.pivotX = numberArg(2, 0.5);
          control.pivotY = numberArg(3, 0.5);
          frameSetters++;
          return 0;
        },
        SetLocalScale: function () {
          apiCall("SetLocalScale");
          var control = controlArg(1);
          control.localScaleX = numberArg(2, 1);
          control.localScaleY = numberArg(3, 1);
          control.localScaleZ = lua.lua_type(L, 4) === lua.LUA_TNIL ? 1 : numberArg(4, 1);
          frameSetters++;
          return 0;
        },
        SetLocalRotation: function () {
          apiCall("SetLocalRotation");
          var control = controlArg(1);
          control.localRotationX = numberArg(2);
          control.localRotationY = numberArg(3);
          control.localRotationZ = numberArg(4);
          frameSetters++;
          return 0;
        },
        SetSiblingIndex: function () {
          apiCall("SetSiblingIndex");
          var control = controlArg(1);
          var index = lua.lua_tointeger(L, 2);
          var list = control.parentId != null ? byId.get(control.parentId).children : roots;
          list = list.filter(function (id) { return id !== control.id; });
          list.splice(Math.max(0, Math.min(list.length, index)), 0, control.id);
          if (control.parentId != null) byId.get(control.parentId).children = list; else roots = list;
          list.forEach(function (id, i) { byId.get(id).siblingIndex = i; });
          pushBool(true);
          return 1;
        },
        SetAsFirstSibling: function () { return methods.SetSiblingIndex.call(null) === 0 ? 0 : setSiblingExtreme(1, 0); },
        SetAsLastSibling: function () { return setSiblingExtreme(1, null); },
        GetSiblingIndex: function () {
          apiCall("GetSiblingIndex");
          var control = controlArg(1);
          lua.lua_pushinteger(L, control.siblingIndex);
          return 1;
        },
        GetChildren: function () {
          apiCall("GetChildren");
          var control = controlArg(1);
          lua.lua_newtable(L);
          control.children.forEach(function (id, i) {
            var c = byId.get(id);
            if (!c || !c.alive) return;
            pushControl(c);
            lua.lua_seti(L, -2, i + 1);
          });
          return 1;
        },
        GetChild: function () {
          apiCall("GetChild");
          var control = controlArg(1);
          var name = to_jsstring(lua.lua_tostring(L, 2));
          var found = null;
          control.children.some(function (id) {
            var c = byId.get(id);
            if (c && c.alive && c.name === name) { found = c; return true; }
            return false;
          });
          pushControl(found);
          return 1;
        },
        FindChild: function () {
          apiCall("FindChild");
          var control = controlArg(1);
          var path = to_jsstring(lua.lua_tostring(L, 2)).split("/").filter(Boolean);
          var node = control;
          for (var i = 0; i < path.length && node; i++) {
            var next = null;
            node.children.some(function (id) {
              var c = byId.get(id);
              if (c && c.alive && c.name === path[i]) { next = c; return true; }
              return false;
            });
            node = next;
          }
          pushControl(node && node !== control ? node : null);
          return 1;
        },
        GetAnchoredPosition: function () {
          apiCall("GetAnchoredPosition");
          var control = controlArg(1);
          pushNumber(control.anchoredPositionX); pushNumber(control.anchoredPositionY);
          return 2;
        },
        GetSizeDelta: function () {
          apiCall("GetSizeDelta");
          var control = controlArg(1);
          pushNumber(control.sizeDeltaX); pushNumber(control.sizeDeltaY);
          return 2;
        },
        GetAnchorMin: function () {
          apiCall("GetAnchorMin");
          var control = controlArg(1);
          pushNumber(control.anchorMinX); pushNumber(control.anchorMinY);
          return 2;
        },
        GetAnchorMax: function () {
          apiCall("GetAnchorMax");
          var control = controlArg(1);
          pushNumber(control.anchorMaxX); pushNumber(control.anchorMaxY);
          return 2;
        },
        GetPivot: function () {
          apiCall("GetPivot");
          var control = controlArg(1);
          pushNumber(control.pivotX); pushNumber(control.pivotY);
          return 2;
        },
        GetLocalScale: function () {
          apiCall("GetLocalScale");
          var control = controlArg(1);
          pushNumber(control.localScaleX); pushNumber(control.localScaleY); pushNumber(control.localScaleZ);
          return 3;
        },
        GetLocalRotation: function () {
          apiCall("GetLocalRotation");
          var control = controlArg(1);
          pushNumber(control.localRotationX); pushNumber(control.localRotationY); pushNumber(control.localRotationZ);
          return 3;
        },
        SetImage: function () {
          apiCall("SetImage");
          var control = controlArg(1);
          control.imageSource = lua.lua_tointeger(L, 2);
          control.imageId = lua.lua_tointeger(L, 3);
          syncControlField(control);
          return 0;
        },
        SetSoftEdgeWidth: function () {
          apiCall("SetSoftEdgeWidth");
          controlArg(1);
          return 0;
        },
        SetFillUnused: function () { apiCall("SetFillUnused"); var c = controlArg(1); c.fillType = ENUMS.ImageFillType.None; return 0; },
        SetFillHorizontal: function () { apiCall("SetFillHorizontal"); var c = controlArg(1); c.fillType = ENUMS.ImageFillType.Horizontal; c.fillAmount = numberArg(3, 1); return 0; },
        SetFillVertical: function () { apiCall("SetFillVertical"); var c = controlArg(1); c.fillType = ENUMS.ImageFillType.Vertical; c.fillAmount = numberArg(3, 1); return 0; },
        SetFillRadial90: function () { apiCall("SetFillRadial90"); var c = controlArg(1); c.fillType = ENUMS.ImageFillType.Radial90; c.fillAmount = numberArg(3, 1); return 0; },
        SetFillRadial180: function () { apiCall("SetFillRadial180"); var c = controlArg(1); c.fillType = ENUMS.ImageFillType.Radial180; c.fillAmount = numberArg(3, 1); return 0; },
        SetFillRadial360: function () { apiCall("SetFillRadial360"); var c = controlArg(1); c.fillType = ENUMS.ImageFillType.Radial360; c.fillAmount = numberArg(3, 1); return 0; },
        AddCursorEventListener: function () {
          apiCall("AddCursorEventListener");
          var control = controlArg(1);
          var typeValue = lua.lua_tointeger(L, 2);
          addListener(control, "cursor", typeNameOf(ENUMS.CursorEventType, typeValue), typeValue, callbackRef(3));
          return 0;
        },
        RemoveCursorEventListener: function () { apiCall("RemoveCursorEventListener"); return 0; },
        RemoveCursorEventListeners: function () { apiCall("RemoveCursorEventListeners"); return 0; },
        RemoveAllCursorEventListeners: function () { apiCall("RemoveAllCursorEventListeners"); return 0; },
        AddKeyEventListener: function () {
          apiCall("AddKeyEventListener");
          var control = controlArg(1);
          var typeValue = lua.lua_tointeger(L, 2);
          addListener(control, "key", typeNameOf(ENUMS.KeyEventType, typeValue), typeValue, callbackRef(3));
          return 0;
        },
        RemoveKeyEventListener: function () { apiCall("RemoveKeyEventListener"); return 0; },
        RemoveKeyEventListeners: function () { apiCall("RemoveKeyEventListeners"); return 0; },
        RemoveAllKeyEventListeners: function () { apiCall("RemoveAllKeyEventListeners"); return 0; },
        AddNavigationEventListener: function () {
          apiCall("AddNavigationEventListener");
          var control = controlArg(1);
          var typeValue = lua.lua_tointeger(L, 2);
          addListener(control, "navigation", typeNameOf(ENUMS.ControllerNavigationEventType, typeValue), typeValue, callbackRef(3));
          return 0;
        },
        RemoveNavigationEventListener: function () { apiCall("RemoveNavigationEventListener"); return 0; },
        RemoveNavigationEventListeners: function () { apiCall("RemoveNavigationEventListeners"); return 0; },
        RemoveAllNavigationEventListeners: function () { apiCall("RemoveAllNavigationEventListeners"); return 0; },
        SetControllerNavigation: function () { apiCall("SetControllerNavigation"); controlArg(1); return 0; },
        GetControllerNavigation: function () { apiCall("GetControllerNavigation"); apiCall("GetControllerNavigation"); return 0; },
        GetScripts: function () { apiCall("GetScripts"); lua.lua_newtable(L); return 1; },
        GetScript: function () { apiCall("GetScript"); lua.lua_pushnil(L); return 1; },
        GetScriptByPath: function () { apiCall("GetScriptByPath"); lua.lua_pushnil(L); return 1; },
        GetText: function () { apiCall("GetText"); controlArg(1); pushString(""); return 1; },
        GetUIPos: function () {
          apiCall("GetUIPos");
          var control = controlArg(1);
          var rect = rectOf(control);
          pushNumber(rect.cx); pushNumber(rect.cy);
          return 2;
        },
        GetPressUIPos: function () { apiCall("GetPressUIPos"); pushNumber(cursorPos.x); pushNumber(cursorPos.y); return 2; },
        GetUIPosDelta: function () { apiCall("GetUIPosDelta"); pushNumber(0); pushNumber(0); return 2; },
        PlayAnimation: function () { apiCall("PlayAnimation"); controlArg(1); return 0; },
        StopAnimation: function () { apiCall("StopAnimation"); controlArg(1); return 0; },
        GetContentLength: function () { apiCall("GetContentLength"); controlArg(1); pushNumber(0); return 1; },
        GetItemSize: function () { apiCall("GetItemSize"); controlArg(1); pushNumber(0); pushNumber(0); return 2; },
        GetItemSpacing: function () { apiCall("GetItemSpacing"); controlArg(1); pushNumber(0); pushNumber(0); return 2; },
        GetItemIndex: function () { apiCall("GetItemIndex"); controlArg(1); lua.lua_pushinteger(L, 0); return 1; },
        GetPadding: function () { apiCall("GetPadding"); controlArg(1); pushNumber(0); pushNumber(0); pushNumber(0); pushNumber(0); return 4; },
        IsolateNavigation: function () { apiCall("IsolateNavigation"); controlArg(1); return 0; },
        Append: function () { return 0; },
      };
      Object.keys(methods).forEach(function (key) {
        methodTable[key] = methods[key];
      });
      return methods;
    }

    /** 元表 __index：字段直接命中表，方法从 JS 方法表取；未登记的名字显式报错 */
    function controlIndex() {
      var key = lua.lua_type(L, 2) === lua.LUA_TSTRING ? to_jsstring(lua.lua_tostring(L, 2)) : "";
      if (key === "__id") {
        lua.lua_pushnil(L);
        return 1;
      }
      var fn = methodTable[key];
      if (typeof fn === "function") {
        pushFunction(fn);
        return 1;
      }
      markUnsupported("控件方法 " + key, "未在沙箱中实现");
    }

    function setSiblingExtreme(_self, index) {
      apiCall("SetAsFirstSibling/SetAsLastSibling");
      return 0;
    }

    function syncControlField(control) {
      if (lua.lua_gettop(L) === 0) return;
      // 控件表字段与 JS 模型保持同步（写回第 1 个参数那张表）
      if (lua.lua_type(L, 1) !== lua.LUA_TTABLE) return;
      pushBool(control.active); lua.lua_setfield(L, 1, to_luastring("active"));
      pushBool(control.visible); lua.lua_setfield(L, 1, to_luastring("visible"));
      pushNumber(control.imageSource); lua.lua_setfield(L, 1, to_luastring("imageSource"));
      pushNumber(control.imageId); lua.lua_setfield(L, 1, to_luastring("imageId"));
    }

    function typeNameOf(table, value) {
      var found = "Unknown";
      Object.keys(table).forEach(function (key) {
        if (table[key] === value) found = key;
      });
      return found;
    }

    function describeTree() {
      var lines = [];
      (function walk(ids, depth) {
        ids.forEach(function (id) {
          var c = byId.get(id);
          if (!c || !c.alive) return;
          var rect = rectOf(c);
          lines.push(
            new Array(depth + 1).join("  ") + "#" + c.id + " " + c.name +
            " active=" + c.active + " visible=" + c.visible +
            " rect=(" + rect.x.toFixed(0) + "," + rect.y.toFixed(0) + "," + rect.w.toFixed(0) + "x" + rect.h.toFixed(0) + ")"
          );
          walk(c.children, depth + 1);
        });
      })(roots, 0);
      return lines.join("\n");
    }

    // ---- 帧推进 ----
    function applyTweens(step) {
      var base = lua.lua_gettop(L);
      tweens.forEach(function (tween) {
        if (tween.done) return;
        var control = byId.get(tween.controlId);
        if (!control || !control.alive) { tween.done = true; return; }
        tween.elapsed += step;
        var k = tween.duration > 0 ? Math.min(1, tween.elapsed / tween.duration) : 1;
        Object.keys(tween.data).forEach(function (key) {
          var from = tween.from[key];
          var to = tween.data[key];
          if (typeof from === "number" && typeof to === "number") control[key] = from + (to - from) * k;
        });
        if (k >= 1) {
          tween.done = true;
          if (control && control.alive) syncFieldsFor(control);
        }
      });
      lua.lua_settop(L, base);
      tweens = tweens.filter(function (t) { return !t.done; });
    }

    function syncFieldsFor(control) {
      // Tween 直接改了 JS 模型，控件表的字段在下次 push 时自然刷新；这里只做无害占位
    }

    function stepOnce(step) {
      clock += step;
      frames++;
      frameSetters = 0;
      var base = lua.lua_gettop(L);
      if (updateEnabled) {
        var r = callGlobal("OnUpdate", [function () { pushNumber(step); }]);
        if (r.exists && !r.ok) updateEnabled = false;
      }
      lua.lua_settop(L, base);
      if (levelUpdateEnabled && !levelPaused) {
        var r2 = callGlobal("OnLevelUpdate", [function () { pushNumber(step); }]);
        if (r2.exists && !r2.ok) levelUpdateEnabled = false;
      }
      lua.lua_settop(L, base);
      applyTweens(step);
      settleStats.totalSetters += frameSetters;
      if (frameSetters > settleStats.maxSettersPerFrame) settleStats.maxSettersPerFrame = frameSetters;
      if (opt.onFrame) opt.onFrame({ time: clock, frame: frames, setters: frameSetters, controls: aliveCount() });
    }

    // ---- 对外 API ----
    var sandbox = {
      version: VERSION,
      presets: DEVICE_PRESETS,
      enums: ENUMS,
      load: function (source, name) {
        if (destroyed) throw new Error("沙箱已销毁");
        return runChunk(source, name || "levelScript.lua");
      },
      loadAnnotations: function (source) {
        // 真实常量优先：先跑 annotations，再装桩（脚本里同名定义会覆盖桩）
        var ok = runChunk(source, "annotations.lua");
        return ok;
      },
      installApi: function () {
        if (!controlMetatableRef) installApi();
      },
      start: function () {
        if (started) throw new Error("已经启动过了");
        if (!controlMetatableRef) installApi();
        started = true;
        callGlobal("OnInit", []);
        callGlobal("OnStart", []);
        return sandbox;
      },
      step: function (count) {
        var n = count || 1;
        for (var i = 0; i < n; i++) {
          if (destroyed) break;
          stepOnce(dt);
        }
        return sandbox;
      },
      runMs: function (ms) {
        var steps = Math.max(1, Math.round((ms / 1000) / dt));
        return sandbox.step(steps);
      },
      setUpdateEnabled: function (enabled) {
        updateEnabled = !!enabled;
        return sandbox;
      },
      destroy: function () {
        if (destroyed) return sandbox;
        callGlobal("OnDisable", []);
        callGlobal("OnDestroy", []);
        destroyed = true;
        return sandbox;
      },
      setCanvas: function (w, h, device) {
        canvas = { width: w, height: h };
        if (device) deviceName = device;
        return sandbox;
      },
      setStick: function (side, x, y) {
        if (side === "left") stick.left = [x, y]; else stick.right = [x, y];
        return sandbox;
      },
      moveTo: function (x, y) {
        var previous = hitTest(cursorPos.x, cursorPos.y);
        cursorPos = { x: x, y: y };
        var current = hitTest(x, y);
        if (previous && previous !== current) dispatch(previous, "cursor", ENUMS.CursorEventType.Exit, { x: x, y: y });
        if (current && current !== previous) dispatch(current, "cursor", ENUMS.CursorEventType.Enter, { x: x, y: y });
        return current;
      },
      click: function (x, y, button) {
        var target = sandbox.moveTo(x, y);
        if (!target) return null;
        dispatch(target, "cursor", ENUMS.CursorEventType.Down, { x: x, y: y, button: button || 0 });
        dispatch(target, "cursor", ENUMS.CursorEventType.Up, { x: x, y: y, button: button || 0 });
        dispatch(target, "cursor", ENUMS.CursorEventType.Click, { x: x, y: y, button: button || 0 });
        return target;
      },
      drag: function (x0, y0, x1, y1) {
        var target = sandbox.moveTo(x0, y0);
        if (!target) return null;
        dispatch(target, "cursor", ENUMS.CursorEventType.Down, { x: x0, y: y0 });
        dispatch(target, "cursor", ENUMS.CursorEventType.BeginDrag, { x: x0, y: y0 });
        dispatch(target, "cursor", ENUMS.CursorEventType.Drag, { x: x1, y: y1, dx: x1 - x0, dy: y1 - y0 });
        dispatch(target, "cursor", ENUMS.CursorEventType.EndDrag, { x: x1, y: y1 });
        return target;
      },
      key: function (typeName, keyCode) {
        var typeValue = ENUMS.KeyEventType[typeName] || ENUMS.KeyEventType.Down;
        keyState[keyCode] = typeName === "Down";
        var targets = controls.filter(function (c) { return c.alive; });
        var handled = false;
        targets.forEach(function (c) {
          if (dispatch(c, "key", typeValue, { keyCode: keyCode, x: 0, y: 0 })) handled = true;
        });
        return handled;
      },
      /** 脚本化试玩：sequence = [{at: 毫秒, action: "click"|"move"|"key"|"stick", ...}] */
      play: function (sequence, totalMs) {
        var events = (sequence || []).slice().sort(function (a, b) { return (a.at || 0) - (b.at || 0); });
        var index = 0;
        var elapsed = 0;
        var total = totalMs != null ? totalMs : (events.length ? events[events.length - 1].at + 500 : 1000);
        while (elapsed < total) {
          while (index < events.length && (events[index].at || 0) <= elapsed) {
            var ev = events[index++];
            if (ev.action === "click") sandbox.click(ev.x, ev.y, ev.button);
            else if (ev.action === "move") sandbox.moveTo(ev.x, ev.y);
            else if (ev.action === "drag") sandbox.drag(ev.x, ev.y, ev.x2, ev.y2);
            else if (ev.action === "key") sandbox.key(ev.type || "Down", ev.keyCode);
            else if (ev.action === "stick") sandbox.setStick(ev.side || "left", ev.x || 0, ev.y || 0);
            else {
              warnings.push("未知的试玩动作：" + ev.action);
            }
          }
          stepOnce(dt);
          elapsed += dt * 1000;
        }
        return sandbox;
      },
      /** 随机输入轰炸：把"没试过的操作"也压一遍，主要用来炸运行期错误 */
      fuzz: function (opts) {
        var o = opts || {};
        var durationMs = o.durationMs || 5000;
        var seed = o.seed || 20260101;
        var rand = mulberry32(seed);
        var intervalMs = o.intervalMs || 120;
        var elapsed = 0;
        var clicks = 0, keys = 0;
        while (elapsed < durationMs) {
          if (elapsed % intervalMs < dt * 1000) {
            var r = rand();
            if (r < 0.6) {
              var x = rand() * canvas.width;
              var y = rand() * canvas.height;
              sandbox.click(x, y, rand() < 0.2 ? 1 : 0);
              clicks++;
            } else if (r < 0.85) {
              var codes = [13, 27, 32, 49, 50, 65, 68, 83, 87];
              var code = codes[Math.floor(rand() * codes.length)];
              sandbox.key(rand() < 0.5 ? "Down" : "Up", code);
              keys++;
            } else {
              sandbox.setStick(rand() < 0.5 ? "left" : "right", rand() * 2 - 1, rand() * 2 - 1);
            }
          }
          stepOnce(dt);
          elapsed += dt * 1000;
        }
        return { clicks: clicks, keys: keys, durationMs: durationMs, seed: seed };
      },
      controls: function () {
        return controls.filter(function (c) { return c.alive; }).map(function (c) {
          var rect = rectOf(c);
          return {
            id: c.id,
            name: c.name,
            prefabIndex: c.prefabIndex,
            parentId: c.parentId,
            active: c.active,
            activeInHierarchy: isActiveInHierarchy(c),
            visible: c.visible,
            everActivated: c.everActivated,
            imageId: c.imageId,
            imageColor: c.imageColor,
            localRotationZ: c.localRotationZ,
            rect: rect,
            listeners: c.listeners,
          };
        });
      },
      log: function () { return logs.slice(); },
      report: function (extra) {
        var unsupportedList = [];
        unsupported.forEach(function (count, name) { unsupportedList.push({ name: name, count: count }); });
        unsupportedList.sort(function (a, b) { return b.count - a.count; });
        var apiList = [];
        apiCalls.forEach(function (count, name) { apiList.push({ name: name, count: count }); });
        apiList.sort(function (a, b) { return b.count - a.count; });
        var alive = aliveCount();
        var neverActivated = controls.filter(function (c) { return !c.everActivated; }).length;
        var noListenerControls = controls.filter(function (c) {
          return c.alive && c.listeners.length === 0 && c.children.length === 0;
        }).length;
        var verdict = "pass";
        if (errors.length) verdict = "fail";
        else if (neverActivated > 0) verdict = "warn";
        return {
          sandbox: { version: VERSION, device: deviceName, canvas: { width: canvas.width, height: canvas.height }, dt: dt },
          verdict: verdict,
          timeMs: Math.round(clock * 1000),
          frames: frames,
          script: { started: started, destroyed: destroyed, updateEnabled: updateEnabled },
          controls: {
            alive: alive,
            created: settleStats.totalCreates,
            destroyed: settleStats.totalDestroys,
            createdAfterStart: settleStats.createsAfterStart,
            destroyedAfterStart: settleStats.destroysAfterStart,
            peak: Math.max.apply(null, [0].concat(frames ? [alive] : [0])),
            neverActivated: neverActivated,
            leafWithoutListener: noListenerControls,
          },
          calls: {
            maxSettersPerFrame: settleStats.maxSettersPerFrame,
            totalSetterCalls: settleStats.totalSetters,
            top: apiList.slice(0, 20),
          },
          errors: errors.slice(0, 100),
          warnings: warnings.slice(0, 100),
          unsupported: unsupportedList,
          logTail: logs.slice(-50),
          notes: [
            "枚举值是沙箱自定的占位值，只保证同名同值；需要真值请先加载 annotations.lua。",
            "布局是按锚点拉伸 + 中心偏移的简化模型，与真机可能有差异。",
            "runtime_verified 仍然是 pending：沙箱通过不等于真机通过。",
          ],
          extra: extra || null,
        };
      },
    };

    return sandbox;
  }

  function mulberry32(a) {
    return function () {
      a |= 0;
      a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  return {
    version: VERSION,
    createSandbox: createSandbox,
    presets: DEVICE_PRESETS,
    enums: ENUMS,
  };
});
