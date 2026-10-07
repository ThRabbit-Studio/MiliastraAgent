-- 体外沙箱示例：一块会转、会动、能点的面板
-- 用法：node run_headless.mjs samples/demo.lua --fuzz 2000

local IMAGE_PREFAB_ID = 1

local panel = nil
local canvasW, canvasH = 0, 0
local elapsed = 0
local clicks = 0

function OnStart()
    canvasW, canvasH = game.GetUICanvasSize()
    print("画布", canvasW, canvasH)

    panel = game.InstantiateClientUIControl(IMAGE_PREFAB_ID, script.object)
    panel.name = "panel"
    panel:SetAnchorMin(0.5, 0.5)
    panel:SetAnchorMax(0.5, 0.5)
    panel:SetPivot(0.5, 0.5)
    panel:SetSizeDelta(240, 120)
    panel:SetAnchoredPosition(canvasW / 2, canvasH / 2)
    panel:SetImage(Enum.ImageSource.BuiltIn, 100001)
    panel.imageType = Enum.ImageType.Simple
    panel.imageColor = Color.FromRGB(240, 200, 80)
    panel:SetActive(true)
    panel:SetVisible(true)

    panel:AddCursorEventListener(Enum.CursorEventType.Click, function(self, data)
        clicks = clicks + 1
        print("被点了", clicks, "次，位置", data.x, data.y)
        self.imageColor = Color.FromRGB(255, 120, 200)
        return true
    end)

    script:EnableUpdate(true)
end

function OnUpdate(dt)
    elapsed = elapsed + dt
    panel:SetLocalRotation(0, 0, math.sin(elapsed * 2) * 15)
    panel:SetAnchoredPosition(canvasW / 2 + math.sin(elapsed) * 200, canvasH / 2)
    if math.isnan(elapsed) or math.isinf(elapsed) then
        printerr("时间增量异常")
    end
end

function OnDestroy()
    print("收尾：一共被点了", clicks, "次")
    if panel ~= nil then
        game.DestroyClientUIControl(panel)
        panel = nil
    end
end
