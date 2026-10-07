"""qx2d.lua_pixel —— 生成像素画的 Lua（未压缩基线 + 压缩版）。"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .core import RGBA, Rect, encode_delta, encode_uint, build_size_dict

PALETTE_ROW = 8


def _color_expr(color: RGBA, use_rgba: bool) -> str:
    if use_rgba:
        return "Color.FromRGBA(%d, %d, %d, %d)" % color
    return "Color.FromRGB(%d, %d, %d)" % (color[0], color[1], color[2])


def _palette_block(palette: Sequence[RGBA]) -> str:
    use_rgba = any(c[3] != 255 for c in palette)
    lines = []
    for i, color in enumerate(palette, start=1):
        lines.append("    [%d] = %s," % (i, _color_expr(color, use_rgba)))
    return "local PALETTE = {\n" + "\n".join(lines) + "\n}"


def _config_block(opt: Dict[str, object]) -> str:
    return "\n".join(
        [
            "-- ===== CONFIG：以下常量必须替换为本项目 annotations.lua / readme.md 里的真实值 =====",
            "local IMAGE_PREFAB_ID = %s      -- 图片控件 prefab ID" % opt["image_prefab_id"],
            "local IMAGE_SOURCE = %s         -- Enum.ImageSource" % opt["image_source"],
            "local IMAGE_TYPE = %s           -- Enum.ImageType" % opt["image_type"],
            "local SQUARE_ASSET_ID = %s      -- 方形基础图元 assetID" % opt["square_asset_id"],
            "local TINTABLE = %s             -- 该图元是否支持染色" % ("true" if opt["tintable"] else "false"),
            "local BASE_PIXEL_SIZE = %s      -- 设计基准下每个逻辑像素的像素尺寸" % opt["base_pixel_size"],
            "local CANVAS_MARGIN = %s        -- 画布留白系数" % opt["canvas_margin"],
            "-- ===============================================================================",
        ]
    )


def _draw_item_function() -> str:
    return """local pixelSize, imageWidth, imageHeight = 1, 0, 0

local function DrawItem(parent, kind, x, y, w, h, angle, colorIndex)
    local image = game.InstantiateClientUIControl(IMAGE_PREFAB_ID, parent)
    ---@cast image ClientUIImageControl

    image:SetAnchorMin(0.5, 0.5)
    image:SetAnchorMax(0.5, 0.5)
    image:SetPivot(0.5, 0.5)
    image:SetImage(IMAGE_SOURCE, SQUARE_ASSET_ID)
    image.imageType = IMAGE_TYPE

    if TINTABLE then
        image.imageColor = PALETTE[colorIndex]
    end

    image:SetSizeDelta(w * pixelSize, h * pixelSize)

    if angle ~= 0 then
        image:SetLocalRotation(0, 0, angle)
    end

    local px = -imageWidth / 2 + (x + w / 2) * pixelSize
    local py = imageHeight / 2 - (y + h / 2) * pixelSize
    image:SetAnchoredPosition(px, py)

    image:SetActive(true)
    image:SetVisible(true)
end"""


def _on_start_function(source: str = "DRAWS") -> str:
    return """function OnStart()
    local canvasWidth, canvasHeight = game.GetUICanvasSize()

    pixelSize = math.min(
        BASE_PIXEL_SIZE,
        canvasWidth / GRID_WIDTH * CANVAS_MARGIN,
        canvasHeight / GRID_HEIGHT * CANVAS_MARGIN
    )

    imageWidth = GRID_WIDTH * pixelSize
    imageHeight = GRID_HEIGHT * pixelSize

    local parent = script.object
    local draws = %s
    for i = 1, #draws do
        local item = draws[i]
        DrawItem(parent, item[1], item[2], item[3], item[4], item[5], item[6], item[7])
    end
end""" % source


def encode_pixel_rows(records: Sequence[Dict[str, int]], size_index: Dict[Tuple[int, int], int]) -> str:
    rows: Dict[int, List[Dict[str, int]]] = {}
    for rec in records:
        rows.setdefault(rec["y"], []).append(rec)
    out: List[str] = []
    prev_y = -1
    for y in sorted(rows):
        line = sorted(rows[y], key=lambda r: r["x"])
        out.append(encode_delta(y - prev_y))
        prev_y = y
        out.append(encode_uint(len(line)))
        right = 0
        for rec in line:
            out.append(encode_uint(rec["x"] - right))
            out.append(encode_uint(size_index[(rec["width"], rec["height"])]))
            out.append(encode_uint(rec["colorIndex"]))
            right = rec["x"] + rec["width"]
    return "".join(out)


def emit_pixel_baseline(plan: Dict[str, object]) -> str:
    records: List[Dict[str, int]] = plan["records"]  # type: ignore[assignment]
    palette: List[RGBA] = plan["palette"]  # type: ignore[assignment]
    grid_w, grid_h = plan["grid"]  # type: ignore[misc]
    rows = []
    for rec in records:
        rows.append(
            "    {%d, %d, %d, %d, %d, %d, %d},"
            % (rec["kind"], rec["x"], rec["y"], rec["width"], rec["height"], rec["angle"], rec["colorIndex"])
        )
    draws = "local DRAWS = {\n" + "\n".join(rows) + "\n}"
    head = [
        "-- levelScript.lua —— 由 tools/qx2d pixel 生成的像素画基线（未压缩记录）",
        "-- 参考图：%s" % plan["source"],
        "-- 参考图 SHA-256：%s" % plan["source_sha256"],
        "-- 网格：%d x %d（单元 %s） 记录数：%d 调色板：%d" % (grid_w, grid_h, plan["cell"], len(records), len(palette)),
        "-- 数据指纹（记录+调色板）：%s" % plan["data_hash"],
        "-- 运行时验证：pending —— 必须在目标环境冒烟验证后才能替换正式脚本",
        "",
        _config_block(plan["options"]),  # type: ignore[arg-type]
        "",
        "local GRID_WIDTH = %d" % grid_w,
        "local GRID_HEIGHT = %d" % grid_h,
        "",
        _palette_block(palette),
        "",
        draws,
        "",
        _draw_item_function(),
        "",
        _on_start_function("DRAWS"),
        "",
    ]
    return "\n".join(head)


def emit_pixel_compact(plan: Dict[str, object]) -> str:
    records: List[Dict[str, int]] = plan["records"]  # type: ignore[assignment]
    palette: List[RGBA] = plan["palette"]  # type: ignore[assignment]
    grid_w, grid_h = plan["grid"]  # type: ignore[misc]
    sizes = build_size_dict([(r["width"], r["height"]) for r in records])
    size_index = {s: i for i, s in enumerate(sizes)}
    stream = encode_pixel_rows(records, size_index)
    size_w = "{" + ", ".join(str(s[0]) for s in sizes) + "}"
    size_h = "{" + ", ".join(str(s[1]) for s in sizes) + "}"

    head = [
        "-- levelScript.lua —— 由 tools/qx2d pixel 生成的像素画（安全 ASCII 压缩记录）",
        "-- 参考图：%s" % plan["source"],
        "-- 参考图 SHA-256：%s" % plan["source_sha256"],
        "-- 网格：%d x %d 记录数：%d 调色板：%d 压缩流字节：%d"
        % (grid_w, grid_h, len(records), len(palette), len(stream)),
        "-- 数据指纹（记录+调色板）：%s" % plan["data_hash"],
        "-- 解码规则：行流 = zigzag(dy) varint, 行内条数 varint, 每条 [gap, sizeIndex, colorIndex] varint",
        "-- 运行时验证：pending —— 必须在目标环境冒烟验证后才能替换正式脚本",
        "",
        _config_block(plan["options"]),  # type: ignore[arg-type]
        "",
        "local GRID_WIDTH = %d" % grid_w,
        "local GRID_HEIGHT = %d" % grid_h,
        "",
        _palette_block(palette),
        "",
        "local SIZE_W = %s" % size_w,
        "local SIZE_H = %s" % size_h,
        'local ALPHABET = "%s"' % _lua_safe_alphabet(),
        "",
        'local ROWS = "%s"' % stream,
        "",
        _decoder_block(str(plan["options"].get("lua_version", "5.3"))),  # type: ignore[union-attr]
        "",
        _draw_item_function(),
        "",
        _on_start_function("BuildDraws()"),
        "",
    ]
    return "\n".join(head)


def _lua_safe_alphabet() -> str:
    from .core import SAFE_ALPHABET

    return SAFE_ALPHABET


def _idiv_block(lua_version: str) -> str:
    """Lua 5.1 没有整除运算符，统一用 IDiv 包一层，两种版本都能跑。"""
    if str(lua_version).startswith("5.1"):
        return "local function IDiv(a, b)\n    return math.floor(a / b)\nend"
    return "local function IDiv(a, b)\n    return a // b\nend"


def _decoder_block(lua_version: str = "5.3") -> str:
    return _idiv_block(lua_version) + "\n\n" + """local DIGIT = {}
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

local function BuildDraws()
    local draws = {}
    local position = 1
    local previousY = -1

    while position <= #ROWS do
        local dy, count
        dy, position = ReadZigZag(ROWS, position)
        local y = previousY + dy
        previousY = y

        count, position = ReadUInt(ROWS, position)
        local right = 0

        for _ = 1, count do
            local gap, sizeIndex, colorIndex
            gap, position = ReadUInt(ROWS, position)
            sizeIndex, position = ReadUInt(ROWS, position)
            colorIndex, position = ReadUInt(ROWS, position)

            local x = right + gap
            local sw = SIZE_W[sizeIndex + 1]
            draws[#draws + 1] = {0, x, y, sw, SIZE_H[sizeIndex + 1], 0, colorIndex}
            right = x + sw
        end
    end

    return draws
end"""
