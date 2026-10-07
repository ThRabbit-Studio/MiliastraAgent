"""qx2d.lua_anim —— 把动画时间轴写成 Lua 播放器（逐帧绝对状态 + 属性缓存）。

数据流格式（安全 ASCII 变长整数）：
    每帧、每个槽位：visible(varint)、colorIndex(varint)；可见时再依次
    zigzag(round(x*10))、zigzag(round(y*10))、sizeIndex、zigzag(round(rotation*10))、alpha(0..255)。
    隐藏槽位只写前两个字段，其余字段在 JSON 里保留基值供参考。
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .core import encode_uint, zigzag


def encode_anim_stream(frames: Sequence[Sequence[Dict[str, object]]], size_index: Dict[Tuple[int, int], int]) -> str:
    out: List[str] = []
    for frame in frames:
        for slot in frame:
            visible = 1 if int(slot.get("visible", 1)) else 0
            out.append(encode_uint(visible))
            out.append(encode_uint(int(slot["colorIndex"])))
            if not visible:
                continue
            x = int(round(float(slot["x"]) * 10.0))
            y = int(round(float(slot["y"]) * 10.0))
            rot = int(round(float(slot["rotation"]) * 10.0))
            alpha = int(round(float(slot["opacity"]) * 255.0))
            sizes = size_index[(int(round(float(slot["width"]))), int(round(float(slot["height"]))))]
            out.append(encode_uint(zigzag(x)))
            out.append(encode_uint(zigzag(y)))
            out.append(encode_uint(sizes))
            out.append(encode_uint(zigzag(rot)))
            out.append(encode_uint(alpha))
    return "".join(out)


RUNTIME = r"""local pixelSize, imageWidth, imageHeight = 1, 0, 0

local function IDiv(a, b)
    return a // b
end

local DIGIT = {}
for i = 1, #ALPHABET do
    DIGIT[string.byte(ALPHABET, i)] = i - 1
end
local RADIX = IDiv(#ALPHABET, 2)

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

local function ReadZigZag(data, position)
    local raw
    raw, position = ReadUInt(data, position)
    if raw % 2 == 0 then
        return IDiv(raw, 2), position
    end
    return -IDiv(raw + 1, 2), position
end

local objects = {}
local cacheX, cacheY, cacheW, cacheH = {}, {}, {}, {}
local cacheVisible, cacheRotation, cacheColor = {}, {}, {}
local frameOffset, frameStart, frameDuration = {}, {}, {}

local currentFrame = 0
local elapsed = 0
local totalDuration = 0
local pendingCreate = 0
local createdSlots = 0
local ready = false

local function BuildIndex()
    local position = 1
    for f = 1, FRAME_COUNT do
        frameOffset[f] = position
        for _ = 1, SLOT_COUNT do
            local visible, ignored
            visible, position = ReadUInt(ANIM_DATA, position)
            ignored, position = ReadUInt(ANIM_DATA, position)
            if visible == 1 then
                ignored, position = ReadZigZag(ANIM_DATA, position)
                ignored, position = ReadZigZag(ANIM_DATA, position)
                ignored, position = ReadUInt(ANIM_DATA, position)
                ignored, position = ReadZigZag(ANIM_DATA, position)
                ignored, position = ReadUInt(ANIM_DATA, position)
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

local function CreateSlot(slot)
    local object = game.InstantiateClientUIControl(IMAGE_PREFAB_ID, script.object)
    ---@cast object ClientUIImageControl

    object:SetAnchorMin(0.5, 0.5)
    object:SetAnchorMax(0.5, 0.5)
    object:SetPivot(0.5, 0.5)
    object:SetImage(IMAGE_SOURCE, SQUARE_ASSET_ID)
    object.imageType = IMAGE_TYPE
    object:SetActive(true)
    object:SetVisible(false)
    objects[slot] = object
    cacheVisible[slot] = false
end

local function ApplyColor(slot, colorIndex, opacity, force)
    local alpha = math.floor(PALETTE[colorIndex][4] * opacity + 0.5)
    if not force and cacheColor[slot] == colorIndex and cacheAlpha[slot] == alpha then
        return
    end
    if TINTABLE then
        local color = PALETTE[colorIndex]
        objects[slot].imageColor = Color.FromRGBA(color[1], color[2], color[3], alpha)
    end
    cacheColor[slot] = colorIndex
    cacheAlpha[slot] = alpha
end

local function ApplyFrame(index)
    local position = frameOffset[index]
    for slot = 1, SLOT_COUNT do
        local object = objects[slot]
        if object ~= nil then
            local visible, colorIndex, sizeIndex, rotation, alpha
            visible, position = ReadUInt(ANIM_DATA, position)
            colorIndex, position = ReadUInt(ANIM_DATA, position)
            if visible == 1 then
                local rawX, rawY, rawRot
                rawX, position = ReadZigZag(ANIM_DATA, position)
                rawY, position = ReadZigZag(ANIM_DATA, position)
                sizeIndex, position = ReadUInt(ANIM_DATA, position)
                rawRot, position = ReadZigZag(ANIM_DATA, position)
                alpha, position = ReadUInt(ANIM_DATA, position)
                local x = rawX / 10
                local y = rawY / 10
                rotation = rawRot / 10

                if cacheVisible[slot] ~= true then
                    object:SetVisible(true)
                    cacheVisible[slot] = true
                end

                local w = SIZE_W[sizeIndex + 1] * pixelSize
                local h = SIZE_H[sizeIndex + 1] * pixelSize
                if cacheW[slot] ~= w or cacheH[slot] ~= h then
                    object:SetSizeDelta(w, h)
                    cacheW[slot] = w
                    cacheH[slot] = h
                end

                local px = -imageWidth / 2 + x * pixelSize
                local py = imageHeight / 2 - y * pixelSize
                if cacheX[slot] ~= px or cacheY[slot] ~= py then
                    object:SetAnchoredPosition(px, py)
                    cacheX[slot] = px
                    cacheY[slot] = py
                end

                if cacheRotation[slot] ~= rotation then
                    object:SetLocalRotation(0, 0, rotation)
                    cacheRotation[slot] = rotation
                end

                ApplyColor(slot, colorIndex, alpha / 255, false)
            else
                if cacheVisible[slot] ~= false then
                    object:SetVisible(false)
                    cacheVisible[slot] = false
                end
            end
        end
    end
    currentFrame = index
end

function OnStart()
    local canvasWidth, canvasHeight = game.GetUICanvasSize()

    pixelSize = math.min(
        BASE_PIXEL_SIZE,
        canvasWidth / GRID_WIDTH * CANVAS_MARGIN,
        canvasHeight / GRID_HEIGHT * CANVAS_MARGIN
    )

    imageWidth = GRID_WIDTH * pixelSize
    imageHeight = GRID_HEIGHT * pixelSize

    cacheAlpha = {}
    BuildIndex()

    pendingCreate = SLOT_COUNT
    createdSlots = 0
    ready = false
    script:EnableUpdate(true)
end

function OnUpdate(dt)
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

    if not ready or totalDuration <= 0 then
        return
    end

    elapsed = elapsed + dt
    local t = elapsed * 1000

    if LOOP then
        t = t % totalDuration
    elseif t >= totalDuration then
        t = totalDuration - 1
    end

    local index = currentFrame
    if index < 1 then
        index = 1
    end
    while index > 1 and t < frameStart[index] do
        index = index - 1
    end
    while index < FRAME_COUNT and t >= frameStart[index + 1] do
        index = index + 1
    end

    if index ~= currentFrame then
        ApplyFrame(index)
    end
end
"""


def _palette_block(palette: Sequence[Tuple[int, int, int, int]]) -> str:
    lines = []
    for i, color in enumerate(palette, start=1):
        lines.append("    [%d] = {%d, %d, %d, %d}," % (i, color[0], color[1], color[2], color[3]))
    return "local PALETTE = {\n" + "\n".join(lines) + "\n}"


def emit_anim_lua(plan: Dict[str, object]) -> str:
    from .core import SAFE_ALPHABET
    from .lua_pixel import _idiv_block

    palette: List[Tuple[int, int, int, int]] = plan["palette"]  # type: ignore[assignment]
    options: Dict[str, object] = plan["options"]  # type: ignore[assignment]
    canvas = plan["canvas"]  # type: ignore[misc]
    frames: List[List[Dict[str, object]]] = plan["frames"]  # type: ignore[assignment]
    sizes: List[Tuple[int, int]] = plan["sizes"]  # type: ignore[assignment]
    targets: List[Dict[str, object]] = plan["targets"]  # type: ignore[assignment]
    size_index = {s: i for i, s in enumerate(sizes)}
    stream = encode_anim_stream(frames, size_index)
    duration_data = "".join(encode_uint(int(d)) for d in plan["durations_ms"])  # type: ignore[arg-type]

    parts = [
        "-- levelScript.lua —— 由 tools/qx2d anim 生成的关键帧动画播放器",
        "-- 来源：%s" % plan["source"],
        "-- 帧数：%d  FPS：%s  循环：%s  槽位：%d" % (len(frames), plan["fps"], plan["loop"], len(targets)),
        "-- 运行时验证：pending —— 必须在目标环境冒烟验证后才能替换正式脚本",
        "",
        "-- ===== CONFIG：以下常量必须替换为本项目 annotations.lua / readme.md 里的真实值 =====",
        "local IMAGE_PREFAB_ID = %s      -- 图片控件 prefab ID" % options["image_prefab_id"],
        "local IMAGE_SOURCE = %s        -- Enum.ImageSource" % options["image_source"],
        "local IMAGE_TYPE = %s          -- Enum.ImageType" % options["image_type"],
        "local SQUARE_ASSET_ID = %s     -- 基础图元 assetID" % options["square_asset_id"],
        "local TINTABLE = %s            -- 图元是否支持染色" % ("true" if options["tintable"] else "false"),
        "local BASE_PIXEL_SIZE = %s     -- 设计基准下每个画布像素的尺寸" % options["base_pixel_size"],
        "local CANVAS_MARGIN = %s       -- 画布留白系数" % options["canvas_margin"],
        "local CREATE_BATCH = %s        -- 分批预热时每个 tick 创建的控件数" % options["create_batch"],
        "-- ===============================================================================",
        "",
        "local GRID_WIDTH = %d" % canvas[0],  # type: ignore[index]
        "local GRID_HEIGHT = %d" % canvas[1],  # type: ignore[index]
        "local SLOT_COUNT = %d" % len(targets),
        "local FRAME_COUNT = %d" % len(frames),
        "local FPS = %s" % plan["fps"],
        "local LOOP = %s" % ("true" if plan["loop"] else "false"),
        "",
        _palette_block(palette),
        "",
        "local SIZE_W = {%s}" % ", ".join(str(s[0]) for s in sizes),
        "local SIZE_H = {%s}" % ", ".join(str(s[1]) for s in sizes),
        "local SLOT_COLOR = {%s}" % ", ".join(str(int(t["colorIndex"])) for t in targets),
        'local ALPHABET = "%s"' % SAFE_ALPHABET,
        "",
        _idiv_block(str(options.get("lua_version", "5.3"))),
        "",
        'local DURATION_DATA = "%s"' % duration_data,
        'local ANIM_DATA = "%s"' % stream,
        "",
        "local cacheAlpha = {}",
        "",
        RUNTIME,
        "",
    ]
    return "\n".join(parts)
