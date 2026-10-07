-- 反面示例：故意写错，用来看沙箱会不会抓住
-- 用法：node run_headless.mjs samples/broken.lua
-- 预期：报出 script:EnableTick 未支持；并提示控件建了没激活

local IMAGE_PREFAB_ID = 1

function OnStart()
    local box = game.InstantiateClientUIControl(IMAGE_PREFAB_ID, script.object)
    box.name = "forgot-to-activate"
    box:SetSizeDelta(100, 100)
    box:SetAnchoredPosition(0, 0)
    -- 忘了 SetActive(true)：真机上不会显示
    script:EnableTick(true)   -- 真机接口是 script:EnableUpdate
end

function OnUpdate(dt)
end
