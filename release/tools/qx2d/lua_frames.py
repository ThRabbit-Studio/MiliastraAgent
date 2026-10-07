"""qx2d.lua_frames —— 生成帧动画的 Lua 播放器（颜色前缀控件池 + 每帧绝对几何）。"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .core import RGBA, build_size_dict, encode_uint

HEADER = """-- levelScript.lua —— 由 tools/qx2d frames 生成的帧动画播放器
-- 参考输入：{source}
-- 输入 SHA-256：{sha}
-- 帧数：{frames}  FPS：{fps}  循环：{loop}  控件池：{pool}
-- 格式：按颜色分组的控件池 + 每帧绝对几何（安全 ASCII 变长整数）
-- 解码规则：每帧依次读各颜色组的 count varint，再读 count 个 q varint；
--           q = localX + LOCAL_W * localY + (LOCAL_W * LOCAL_H) * sizeIndex
-- 数据指纹：{hash}
-- 运行时验证：pending —— 必须在目标环境冒烟验证（控件真的创建、父子显示、帧率）后才能替换正式脚本。
-- 注意：创建控件、取画布尺寸等接口名与常量请按本项目 annotations.lua / readme.md 核对。
"""

CONFIG = """-- ===== CONFIG：以下常量必须替换为本项目 annotations.lua / readme.md 里的真实值 ====="
local IMAGE_PREFAB_ID = {prefab}      -- 图片控件 prefab ID
local IMAGE_SOURCE = {source_enum}    -- Enum.ImageSource
local IMAGE_TYPE = {type_enum}        -- Enum.ImageType
local SQUARE_ASSET_ID = {asset}       -- 方形基础图元 assetID
local TINTABLE = {tintable}           -- 该图元是否支持染色
local BASE_PIXEL_SIZE = {pixel}       -- 设计基准下每个逻辑像素的像素尺寸
local CANVAS_MARGIN = {margin}        -- 画布留白系数
local CREATE_BATCH = {batch}          -- 分批预热时每个 tick 创建的控件数
-- ==============================================================================="""

RUNTIME = r"""local pixelSize, imageWidth, imageHeight = 1, 0, 0

local function IDiv(a, b)
    return a // b
end

local DIGIT = {}
for i = 1, #ALPHABET do
    DIGIT[string.byte(ALPHABET, i)] = i - 1
end
local RADIX = IDiv(#ALPHABET, 2)
local AREA = LOCAL_W * LOCAL_H

local function ReadUInt(data, position)
    local value = 0
    local multiplier = 1

    while true do
        local digit = DIGIT[string.byte(data, position)]
        position = position + 1

        local continued = digit >= RADIX
        if continued then
            digit = digit - RADIX
        end

        value = value + digit * multiplier
        if not continued then
            return value, position
        end

        multiplier = multiplier * RADIX
    end
end

local objects = {}
local slotGroup = {}
local slotColor = {}
local cacheX, cacheY, cacheW, cacheH, cacheVisible = {}, {}, {}, {}, {}
local targetX, targetY, targetW, targetH, targetVisible = {}, {}, {}, {}, {}
local frameDuration, frameStart, frameOffset = {}, {}, {}

local currentIndex = 0
local elapsed = 0
local totalDuration = 0
local pendingCreate = 0
local createdSlots = 0
local ready = false

-- 每个槽位属于哪个颜色组
for gi = 1, #GROUP_POOL do
    local base = GROUP_BASE[gi]
    for i = 0, GROUP_POOL[gi] - 1 do
        local slot = base + i + 1
        slotGroup[slot] = gi
        slotColor[slot] = PALETTE[GROUP_COLOR[gi]]
    end
end

local function CreateSlot(slot)
    local object = game.InstantiateClientUIControl(IMAGE_PREFAB_ID, script.object)
    ---@cast object ClientUIImageControl

    object:SetAnchorMin(0.5, 0.5)
    object:SetAnchorMax(0.5, 0.5)
    object:SetPivot(0.5, 0.5)
    object:SetImage(IMAGE_SOURCE, SQUARE_ASSET_ID)
    object.imageType = IMAGE_TYPE

    if TINTABLE then
        object.imageColor = slotColor[slot]
    end

    object:SetActive(true)
    object:SetVisible(false)
    objects[slot] = object
    cacheVisible[slot] = false
end

-- 启动时扫描一遍帧流，记录每帧的起始位置与时长
local function BuildIndex()
    local position = 1
    for f = 1, FRAME_COUNT do
        frameOffset[f] = position
        for gi = 1, #GROUP_POOL do
            local count
            count, position = ReadUInt(FRAME_DATA, position)
            for _ = 1, count do
                local ignored
                ignored, position = ReadUInt(FRAME_DATA, position)
            end
        end
    end

    local dpos = 1
    local start = 0
    for f = 1, FRAME_COUNT do
        local duration
        duration, dpos = ReadUInt(DURATION_DATA, dpos)
        frameDuration[f] = duration
        frameStart[f] = start
        start = start + duration
    end
    totalDuration = start
end

local function TargetIndex(t)
    local index = currentIndex
    if index < 1 then
        index = 1
    end
    while index > 1 and t < frameStart[index] do
        index = index - 1
    end
    while index < FRAME_COUNT and t >= frameStart[index + 1] do
        index = index + 1
    end
    return index
end

-- 解码目标帧，只对发生变化的属性调用 setter
local function ApplyFrame(index)
    local position = frameOffset[index]

    for id = 1, POOL_SIZE do
        targetVisible[id] = false
    end

    for gi = 1, #GROUP_POOL do
        local count
        count, position = ReadUInt(FRAME_DATA, position)
        local base = GROUP_BASE[gi]

        for i = 0, count - 1 do
            local q
            q, position = ReadUInt(FRAME_DATA, position)

            local sizeIndex = IDiv(q, AREA)
            local cell = q % AREA
            local lx = cell % LOCAL_W
            local ly = IDiv(cell, LOCAL_W)

            local id = base + i + 1
            targetVisible[id] = true
            targetX[id] = lx + ORIGIN_X
            targetY[id] = ly + ORIGIN_Y
            targetW[id] = SIZE_W[sizeIndex + 1]
            targetH[id] = SIZE_H[sizeIndex + 1]
        end
    end

    for id = 1, POOL_SIZE do
        local object = objects[id]
        if object ~= nil then
            local visible = targetVisible[id] == true
            if visible ~= cacheVisible[id] then
                object:SetVisible(visible)
                cacheVisible[id] = visible
            end

            if visible then
                local w = targetW[id]
                local h = targetH[id]
                if w ~= cacheW[id] or h ~= cacheH[id] then
                    object:SetSizeDelta(w * pixelSize, h * pixelSize)
                    cacheW[id] = w
                    cacheH[id] = h
                end

                local px = -imageWidth / 2 + (targetX[id] + w / 2) * pixelSize
                local py = imageHeight / 2 - (targetY[id] + h / 2) * pixelSize
                if px ~= cacheX[id] or py ~= cacheY[id] then
                    object:SetAnchoredPosition(px, py)
                    cacheX[id] = px
                    cacheY[id] = py
                end
            end
        end
    end

    currentIndex = index
end

function OnStart()
    local canvasWidth, canvasHeight = game.GetUICanvasSize()

    pixelSize = math.min(
        BASE_PIXEL_SIZE,
        canvasWidth / LOCAL_W * CANVAS_MARGIN,
        canvasHeight / LOCAL_H * CANVAS_MARGIN
    )

    imageWidth = LOCAL_W * pixelSize
    imageHeight = LOCAL_H * pixelSize

    BuildIndex()

    pendingCreate = POOL_SIZE
    createdSlots = 0
    ready = false

    script:EnableUpdate(true)
end

function OnUpdate(dt)
    -- 分批预热：池建完之前不显示任何控件，避免露出半成品画面
    if pendingCreate > 0 then
        local batch = CREATE_BATCH
        while batch > 0 and pendingCreate > 0 do
            createdSlots = createdSlots + 1
            CreateSlot(createdSlots)
            pendingCreate = pendingCreate - 1
            batch = batch - 1
        end
        if pendingCreate == 0 then
            ready = true
            ApplyFrame(1)
        end
        return
    end

    if not ready then
        return
    end

    elapsed = elapsed + dt
    local t = elapsed * 1000

    if totalDuration <= 0 then
        return
    end

    if LOOP then
        t = t % totalDuration
    elseif t >= totalDuration then
        t = totalDuration - 1
    end

    local index = TargetIndex(t)
    if index ~= currentIndex then
        ApplyFrame(index)
    end
end"""


def encode_frame_stream(
    frames: Sequence[Sequence[Dict[str, int]]],
    groups: Sequence[Dict[str, int]],
    size_index: Dict[Tuple[int, int], int],
    origin: Tuple[int, int],
    local_w: int,
    local_h: int,
) -> str:
    """每帧：逐组写 (count, q...)，q 为局部坐标与尺寸索引合并后的整数。"""
    area = local_w * local_h
    ox, oy = origin
    out: List[str] = []
    for frame in frames:
        by_group: List[List[Dict[str, int]]] = [[] for _ in groups]
        order = {g["colorIndex"]: i for i, g in enumerate(groups)}
        for slot in frame:
            by_group[order[slot["colorIndex"]]].append(slot)
        for gi, _g in enumerate(groups):
            items = sorted(by_group[gi], key=lambda s: (s["y"], s["x"]))
            if len(items) > groups[gi]["pool"]:
                raise ValueError("某一帧的矩形数超过该颜色组池大小")
            out.append(encode_uint(len(items)))
            for item in items:
                lx = item["x"] - ox
                ly = item["y"] - oy
                q = lx + local_w * ly + area * size_index[(item["width"], item["height"])]
                out.append(encode_uint(q))
    return "".join(out)


def emit_frames_lua(plan: Dict[str, object]) -> str:
    from .lua_pixel import _palette_block, _lua_safe_alphabet

    palette: List[RGBA] = plan["palette"]  # type: ignore[assignment]
    groups: List[Dict[str, int]] = plan["groups"]  # type: ignore[assignment]
    sizes: List[Tuple[int, int]] = plan["sizes"]  # type: ignore[assignment]
    origin = plan["origin"]  # type: ignore[misc]
    local_w, local_h = plan["local"]  # type: ignore[misc]
    options: Dict[str, object] = plan["options"]  # type: ignore[assignment]

    parts: List[str] = []
    parts.append(
        HEADER.format(
            source=plan["source"],
            sha=plan["source_sha256"],
            frames=len(plan["frames"]),  # type: ignore[arg-type]
            fps=plan["fps"],
            loop="是" if plan["loop"] else "否",
            pool=plan["pool_size"],
            hash=plan["data_hash"],
        )
    )
    parts.append(
        CONFIG.format(
            prefab=options["image_prefab_id"],
            source_enum=options["image_source"],
            type_enum=options["image_type"],
            asset=options["square_asset_id"],
            tintable="true" if options["tintable"] else "false",
            pixel=options["base_pixel_size"],
            margin=options["canvas_margin"],
            batch=options["create_batch"],
        )
    )
    parts.append(_palette_block(palette))
    parts.append("local SIZE_W = {%s}" % ", ".join(str(s[0]) for s in sizes))
    parts.append("local SIZE_H = {%s}" % ", ".join(str(s[1]) for s in sizes))
    parts.append("local GROUP_COLOR = {%s}" % ", ".join(str(g["colorIndex"]) for g in groups))
    parts.append("local GROUP_POOL = {%s}" % ", ".join(str(g["pool"]) for g in groups))
    parts.append("local GROUP_BASE = {%s}" % ", ".join(str(g["base"]) for g in groups))
    parts.append("local POOL_SIZE = %d" % plan["pool_size"])
    parts.append("local FRAME_COUNT = %d" % len(plan["frames"]))  # type: ignore[arg-type]
    parts.append("local FPS = %s" % plan["fps"])
    parts.append("local LOOP = %s" % ("true" if plan["loop"] else "false"))
    parts.append("local ORIGIN_X = %d" % origin[0])
    parts.append("local ORIGIN_Y = %d" % origin[1])
    parts.append("local LOCAL_W = %d" % local_w)
    parts.append("local LOCAL_H = %d" % local_h)
    parts.append('local ALPHABET = "%s"' % _lua_safe_alphabet())
    parts.append("")
    parts.append('local DURATION_DATA = "%s"' % plan["duration_data"])
    parts.append('local FRAME_DATA = "%s"' % plan["frame_data"])
    parts.append("")
    runtime = RUNTIME
    if str(options.get("lua_version", "5.3")).startswith("5.1"):
        runtime = runtime.replace(
            "local function IDiv(a, b)\n    return a // b\nend",
            "local function IDiv(a, b)\n    return math.floor(a / b)\nend",
        )
    parts.append(runtime)
    parts.append("")
    return "\n".join(parts)
