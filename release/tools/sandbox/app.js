/**
 * 体外沙箱的浏览器宿主：画布渲染 + 控件树 + 日志 + 报告 + 输入模拟。
 * 依赖 index.html 先加载 vendor/fengari.bundle.js 与 qx-sandbox.js。
 */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };

  var sandbox = null;
  var playing = false;
  var lastReal = 0;
  var renderScale = 1;
  var maxLogLines = 400;
  var lastSample = null;

  // ---------- 界面初始化 ----------
  function initPresets() {
    var select = $("preset");
    QxSandbox.presets.forEach(function (p) {
      var option = document.createElement("option");
      option.value = p.id;
      option.textContent = p.name + "（" + p.width + "×" + p.height + "）";
      select.appendChild(option);
    });
    select.value = "pc-16-9";
    select.addEventListener("change", function () {
      if (sandbox) {
        var preset = QxSandbox.presets.find(function (p) { return p.id === select.value; });
        sandbox.setCanvas(preset.width, preset.height, preset.device);
        resizeCanvas();
        render();
        log("info", "切换画布：" + preset.name);
      }
    });
  }

  function resizeCanvas() {
    var preset = QxSandbox.presets.find(function (p) { return p.id === $("preset").value; });
    var stage = $("stage");
    var maxW = stage.clientWidth - 24;
    var maxH = Math.max(240, window.innerHeight - 220);
    renderScale = Math.min(maxW / preset.width, maxH / preset.height, 1);
    var canvas = $("view");
    canvas.width = Math.round(preset.width * renderScale * devicePixelRatio);
    canvas.height = Math.round(preset.height * renderScale * devicePixelRatio);
    canvas.style.width = Math.round(preset.width * renderScale) + "px";
    canvas.style.height = Math.round(preset.height * renderScale) + "px";
  }

  // ---------- 渲染 ----------
  function render() {
    var canvas = $("view");
    var ctx = canvas.getContext("2d");
    var w = canvas.width, h = canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#101418";
    ctx.fillRect(0, 0, w, h);
    if (!sandbox) return;
    var scaled = renderScale * devicePixelRatio;
    var list = sandbox.controls();
    // 画布边界（虚拟坐标，原点左下）
    var preset = QxSandbox.presets.find(function (p) { return p.id === $("preset").value; });
    ctx.strokeStyle = "#2a323b";
    ctx.lineWidth = 1;
    ctx.strokeRect(0.5, 0.5, preset.width * scaled - 1, preset.height * scaled - 1);

    list.forEach(function (c) {
      var r = c.rect;
      var x = r.x * scaled;
      var y = (preset.height - (r.y + r.h)) * scaled;
      var rw = Math.max(1, r.w * scaled);
      var rh = Math.max(1, r.h * scaled);
      var color = c.imageColor || { r: 255, g: 255, b: 255, a: 255 };
      var alpha = (c.activeInHierarchy && c.visible) ? Math.max(0.08, (color.a === undefined ? 255 : color.a) / 255) : 0.12;
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.fillStyle = "rgb(" + color.r + "," + color.g + "," + color.b + ")";
      ctx.fillRect(x, y, rw, rh);
      ctx.globalAlpha = Math.min(1, alpha + 0.35);
      ctx.strokeStyle = c.activeInHierarchy ? "#7ee787" : "#f0883e";
      ctx.lineWidth = 1;
      ctx.strokeRect(x + 0.5, y + 0.5, rw - 1, rh - 1);
      if (r.w >= 24 && r.h >= 14) {
        ctx.globalAlpha = 0.9;
        ctx.fillStyle = "#0b0f13";
        ctx.font = Math.max(8, Math.min(12, r.h / 3)) + "px ui-monospace, monospace";
        ctx.fillText("#" + c.id + (c.imageId ? " img:" + c.imageId : ""), x + 4, y + Math.min(rh - 4, 12));
      }
      if (c.localRotationZ) {
        ctx.globalAlpha = 0.6;
        ctx.strokeStyle = "#58a6ff";
        ctx.beginPath();
        ctx.moveTo(x + rw / 2, y + rh / 2);
        ctx.lineTo(x + rw / 2 + Math.cos(-c.localRotationZ * Math.PI / 180) * Math.min(rw, rh) / 2,
                   y + rh / 2 + Math.sin(-c.localRotationZ * Math.PI / 180) * Math.min(rw, rh) / 2);
        ctx.stroke();
      }
      ctx.restore();
    });

    // 光标
    if (lastSample) {
      ctx.fillStyle = "#ffd866";
      ctx.beginPath();
      ctx.arc(lastSample.x * scaled, (preset.height - lastSample.y) * scaled, 4, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function renderTree() {
    if (!sandbox) return;
    var list = sandbox.controls();
    var byParent = new Map();
    list.forEach(function (c) {
      var key = c.parentId === null ? "root" : c.parentId;
      if (!byParent.has(key)) byParent.set(key, []);
      byParent.get(key).push(c);
    });
    var html = [];
    (function walk(key, depth) {
      (byParent.get(key) || []).forEach(function (c) {
        var flags = [];
        if (!c.active) flags.push("未激活");
        if (!c.visible) flags.push("不可见");
        if (c.listeners.length) flags.push("监听×" + c.listeners.length);
        html.push(
          '<div class="node" style="padding-left:' + (depth * 12) + 'px">' +
          "<span class='id'>#" + c.id + "</span> " + escapeHtml(c.name) +
          " <span class='dim'>" + Math.round(c.rect.w) + "×" + Math.round(c.rect.h) +
          " @" + Math.round(c.rect.cx) + "," + Math.round(c.rect.cy) + "</span>" +
          (flags.length ? " <span class='warn'>" + flags.join(" / ") + "</span>" : "") +
          "</div>"
        );
        walk(c.id, depth + 1);
      });
    })("root", 0);
    $("tree").innerHTML = html.join("") || "<div class='dim'>还没有控件</div>";
  }

  function log(level, message) {
    var box = $("log");
    var line = document.createElement("div");
    line.className = "log-" + level;
    line.textContent = "[" + level + "] " + message;
    box.appendChild(line);
    while (box.childNodes.length > maxLogLines) box.removeChild(box.firstChild);
    box.scrollTop = box.scrollHeight;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
    });
  }

  function updateStats() {
    if (!sandbox) return;
    var r = sandbox.report();
    $("verdict").textContent = r.verdict === "pass" ? "通过" : (r.verdict === "warn" ? "有警告" : "失败");
    $("verdict").className = "badge " + r.verdict;
    $("stat-frame").textContent = r.frames;
    $("stat-time").textContent = (r.timeMs / 1000).toFixed(1) + "s";
    $("stat-alive").textContent = r.controls.alive;
    $("stat-created").textContent = r.controls.created;
    $("stat-destroyed").textContent = r.controls.destroyed;
    $("stat-never").textContent = r.controls.neverActivated;
    $("stat-setters").textContent = r.calls.maxSettersPerFrame;
    $("stat-errors").textContent = r.errors.length;
  }

  // ---------- 运行控制 ----------
  function ensureSandbox() {
    if (sandbox) return true;
    try {
      var preset = QxSandbox.presets.find(function (p) { return p.id === $("preset").value; });
      sandbox = QxSandbox.createSandbox({
        fengari: window.fengari,
        canvas: { width: preset.width, height: preset.height },
        device: preset.device,
        dt: 1 / 60,
        onLog: function (entry) {
          log(entry.level, "[" + entry.time + "ms] " + entry.message);
        },
      });
      sandbox.installApi();
      log("info", "沙箱已创建：" + preset.name);
      return true;
    } catch (e) {
      log("error", "创建沙箱失败：" + e.message);
      return false;
    }
  }

  function loadScript() {
    var code = $("script").value;
    if (!code.trim()) {
      log("error", "脚本为空");
      return false;
    }
    if (!ensureSandbox()) return false;
    var annotations = $("annotations").value;
    if (annotations.trim()) {
      if (sandbox.loadAnnotations(annotations)) log("info", "接口标注已加载（真实常量优先）");
      else log("error", "接口标注没跑通，继续用沙箱占位常量");
    }
    var ok = sandbox.load(code, "editor.lua");
    log(ok ? "info" : "error", ok ? "脚本编译并执行成功" : "脚本装载失败");
    updateStats();
    return ok;
  }

  function startRun() {
    if (!sandbox && !loadScript()) return;
    try {
      sandbox.start();
      log("info", "已调用 OnInit / OnStart");
    } catch (e) {
      log("error", "启动失败：" + e.message);
    }
    playing = true;
    lastReal = performance.now();
    renderTree();
    updateStats();
    requestAnimationFrame(loop);
  }

  function loop(now) {
    if (!sandbox) return;
    var deltaMs = Math.min(100, now - lastReal);
    lastReal = now;
    if (playing) {
      var steps = Math.max(1, Math.round((deltaMs / 1000) / (1 / 60)));
      sandbox.step(Math.min(steps, 6));
      render();
      updateStats();
      if (sandbox.report().errors.length > 0) {
        playing = false;
        $("btn-play").textContent = "继续";
        log("error", "出现运行期错误，已暂停");
      }
    }
    requestAnimationFrame(loop);
  }

  function stepOnce() {
    if (!sandbox) return;
    playing = false;
    sandbox.step(1);
    render();
    renderTree();
    updateStats();
  }

  function destroyRun() {
    if (!sandbox) return;
    playing = false;
    sandbox.destroy();
    log("info", "已调用 OnDisable / OnDestroy");
    render();
    renderTree();
    updateStats();
    showReport();
  }

  function showReport() {
    if (!sandbox) return;
    var r = sandbox.report();
    $("report").textContent = JSON.stringify(r, null, 1);
    $("unsupported").innerHTML = r.unsupported.length
      ? r.unsupported.map(function (u) { return "<div class='warn'>" + escapeHtml(u.name) + " ×" + u.count + "</div>"; }).join("")
      : "<div class='dim'>没有调用未支持的 API</div>";
  }

  // ---------- 输入模拟 ----------
  function bindStage() {
    var canvas = $("view");
    canvas.addEventListener("click", function (ev) {
      if (!sandbox) return;
      var rect = canvas.getBoundingClientRect();
      var preset = QxSandbox.presets.find(function (p) { return p.id === $("preset").value; });
      var x = (ev.clientX - rect.left) / renderScale;
      var y = preset.height - (ev.clientY - rect.top) / renderScale;
      lastSample = { x: x, y: y };
      var hit = sandbox.click(x, y, ev.button);
      log(hit ? "info" : "warn", "点击 (" + Math.round(x) + "," + Math.round(y) + ")" + (hit ? " → 命中 #" + hit.id : " → 没有命中控件"));
      render();
      renderTree();
      updateStats();
    });
  }

  function bindButtons() {
    $("btn-load").addEventListener("click", loadScript);
    $("btn-start").addEventListener("click", startRun);
    $("btn-step").addEventListener("click", stepOnce);
    $("btn-play").addEventListener("click", function () {
      if (!sandbox) return;
      playing = !playing;
      this.textContent = playing ? "暂停" : "继续";
      lastReal = performance.now();
    });
    $("btn-destroy").addEventListener("click", destroyRun);
    $("btn-report").addEventListener("click", showReport);
    $("btn-fuzz").addEventListener("click", function () {
      if (!sandbox) return;
      var ms = Number($("fuzz-ms").value) || 3000;
      log("info", "开始随机输入轰炸 " + ms + "ms");
      var result = sandbox.fuzz({ durationMs: ms, seed: Number($("fuzz-seed").value) || 20260101 });
      log("info", "轰炸结束：点击 " + result.clicks + " 次，按键 " + result.keys + " 次");
      render();
      renderTree();
      updateStats();
      showReport();
    });
    $("btn-reset").addEventListener("click", function () {
      sandbox = null;
      playing = false;
      $("log").innerHTML = "";
      $("report").textContent = "";
      $("unsupported").innerHTML = "";
      log("info", "沙箱已重置");
      render();
      renderTree();
    });
    $("file").addEventListener("change", function (ev) {
      var file = ev.target.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () {
        $("script").value = reader.result;
        log("info", "已载入文件：" + file.name + "（" + file.size + " 字节）");
      };
      reader.readAsText(file, "utf-8");
    });
    $("file-ann").addEventListener("change", function (ev) {
      var file = ev.target.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () {
        $("annotations").value = reader.result;
        log("info", "已载入接口标注：" + file.name);
      };
      reader.readAsText(file, "utf-8");
    });
    document.addEventListener("dragover", function (ev) { ev.preventDefault(); });
    document.addEventListener("drop", function (ev) {
      ev.preventDefault();
      var file = ev.dataTransfer.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () {
        $("script").value = reader.result;
        log("info", "拖入脚本：" + file.name);
      };
      reader.readAsText(file, "utf-8");
    });
  }

  window.addEventListener("resize", function () {
    if (sandbox) {
      resizeCanvas();
      render();
    }
  });

  document.addEventListener("DOMContentLoaded", function () {
    if (!window.fengari) {
      document.body.insertAdjacentHTML("afterbegin",
        "<div class='fatal'>没有加载到 Lua 虚拟机：请确认 vendor/fengari.bundle.js 与本页在同一目录（可用 node build-vendor.mjs 重新生成）。</div>");
      return;
    }
    initPresets();
    bindButtons();
    bindStage();
    resizeCanvas();
    log("info", "体外沙箱 v" + QxSandbox.version + " 就绪；Lua " + (window.fengari.FENGARI_VERSION || "5.3"));
    log("info", "粘贴或拖入 levelScript.lua，然后点「装载 → 启动」");
    render();
  });
})();
