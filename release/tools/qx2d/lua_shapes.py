"""qx2d.lua_shapes —— 把图元拟合结果写成 Lua 绘制脚本。"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple


def _palette_block(palette: Sequence[Tuple[int, int, int, int]]) -> str:
    lines = []
    for i, color in enumerate(palette, start=1):
        lines.append("    [%d] = {%d, %d, %d, %d}," % (i, color[0], color[1], color[2], color[3]))
    return "local PALETTE = {\n" + "\n".join(lines) + "\n}"


def emit_shapes_lua(plan: Dict[str, object]) -> str:
    palette: List[Tuple[int, int, int, int]] = plan["palette"]  # type: ignore[assignment]
    elements: List[Dict[str, object]] = plan["elements"]  # type: ignore[assignment]
    options: Dict[str, object] = plan["options"]  # type: ignore[assignment]
    canvas = plan["canvas"]  # type: ignore[misc]
    width, height = canvas[0], canvas[1]

    rows = []
    for element in elements:
        rows.append(
            "    {%d, %.3f, %.3f, %.3f, %.3f, %.3f, %d, %.3f},"
            % (
                int(element["kind"]),
                float(element["x"]),
                float(element["y"]),
                float(element["width"]),
                float(element["height"]),
                float(element["rotation"]),
                int(element["colorIndex"]),
                float(element["opacity"]),
            )
        )
    elements_block = (
        "-- {kind, x, y, width, height, rotation, colorIndex, opacity}\n"
        "-- kind: 0=矩形 1=椭圆 2=三角形；x/y 为中心；rotation 顺时针为正（度）\n"
        "local ELEMENTS = {\n" + "\n".join(rows) + "\n}"
    )

    head = "\n".join(
        [
            "-- levelScript.lua —— 由 tools/qx2d shapes 生成的图元绘制脚本",
            "-- 参考图：%s" % plan["source"],
            "-- 参考图 SHA-256：%s" % plan["source_sha256"],
            "-- 画布：%d x %d   图元数：%d   调色板：%d" % (width, height, len(elements), len(palette)),
            "-- 运行时验证：pending —— 必须在目标环境冒烟验证后才能替换正式脚本",
            "",
            "-- ===== CONFIG：以下常量必须替换为本项目 annotations.lua / readme.md 里的真实值 =====",
            "local IMAGE_PREFAB_ID = %s      -- 图片控件 prefab ID" % options["image_prefab_id"],
            "local IMAGE_SOURCE = %s        -- Enum.ImageSource" % options["image_source"],
            "local IMAGE_TYPE = %s          -- Enum.ImageType" % options["image_type"],
            "local RECT_ASSET_ID = %s       -- 矩形图元 assetID" % options["rect_asset_id"],
            "local ELLIPSE_ASSET_ID = %s    -- 椭圆图元 assetID" % options["ellipse_asset_id"],
            "local TRIANGLE_ASSET_ID = %s   -- 三角形图元 assetID" % options["triangle_asset_id"],
            "local TINTABLE = %s            -- 图元是否支持染色" % ("true" if options["tintable"] else "false"),
            "local BASE_PIXEL_SIZE = %s     -- 设计基准下每个画布像素的尺寸" % options["base_pixel_size"],
            "local CANVAS_MARGIN = %s       -- 画布留白系数" % options["canvas_margin"],
            "-- ===============================================================================",
            "",
            "local GRID_WIDTH = %d" % width,
            "local GRID_HEIGHT = %d" % height,
            "",
            _palette_block(palette),
            "",
            "local ASSET_BY_KIND = {",
            "    [0] = RECT_ASSET_ID,",
            "    [1] = ELLIPSE_ASSET_ID,",
            "    [2] = TRIANGLE_ASSET_ID,",
            "}",
            "",
            elements_block,
            "",
            "local pixelSize, imageWidth, imageHeight = 1, 0, 0",
            "local created = {}",
            "",
            "local function DrawElement(parent, kind, x, y, w, h, rotation, colorIndex, opacity)",
            "    local image = game.InstantiateClientUIControl(IMAGE_PREFAB_ID, parent)",
            "    ---@cast image ClientUIImageControl",
            "",
            "    image:SetAnchorMin(0.5, 0.5)",
            "    image:SetAnchorMax(0.5, 0.5)",
            "    image:SetPivot(0.5, 0.5)",
            "    image:SetImage(IMAGE_SOURCE, ASSET_BY_KIND[kind])",
            "    image.imageType = IMAGE_TYPE",
            "",
            "    if TINTABLE then",
            "        local color = PALETTE[colorIndex]",
            "        local alpha = math.floor(color[4] * opacity + 0.5)",
            "        image.imageColor = Color.FromRGBA(color[1], color[2], color[3], alpha)",
            "    end",
            "",
            "    image:SetSizeDelta(w * pixelSize, h * pixelSize)",
            "",
            "    if rotation ~= 0 then",
            "        image:SetLocalRotation(0, 0, rotation)",
            "    end",
            "",
            "    local px = -imageWidth / 2 + x * pixelSize",
            "    local py = imageHeight / 2 - y * pixelSize",
            "    image:SetAnchoredPosition(px, py)",
            "",
            "    image:SetActive(true)",
            "    image:SetVisible(true)",
            "",
            "    created[#created + 1] = image",
            "end",
            "",
            "function OnStart()",
            "    local canvasWidth, canvasHeight = game.GetUICanvasSize()",
            "",
            "    pixelSize = math.min(",
            "        BASE_PIXEL_SIZE,",
            "        canvasWidth / GRID_WIDTH * CANVAS_MARGIN,",
            "        canvasHeight / GRID_HEIGHT * CANVAS_MARGIN",
            "    )",
            "",
            "    imageWidth = GRID_WIDTH * pixelSize",
            "    imageHeight = GRID_HEIGHT * pixelSize",
            "",
            "    local parent = script.object",
            "    for i = 1, #ELEMENTS do",
            "        local item = ELEMENTS[i]",
            "        DrawElement(parent, item[1], item[2], item[3], item[4], item[5], item[6], item[7], item[8])",
            "    end",
            "end",
            "",
            "function OnDestroy()",
            "    for i = #created, 1, -1 do",
            "        game.DestroyClientUIControl(created[i])",
            "    end",
            "    created = {}",
            "end",
            "",
        ]
    )
    return head
