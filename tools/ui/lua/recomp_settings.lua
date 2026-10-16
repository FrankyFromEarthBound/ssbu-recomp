-- The Graphics page (Options > Graphics): our own script, run by the game on the layout of its Smash options screen
-- (option_melee_top). tools/ui/recomp_menu.py compiles it (Lua 5.3, float numbers) and runtime/recomp_menu.c serves
-- it in place of that screen's script when Graphics was chosen.
--
-- Five rows: each row's title shows "setting: < value >", left / right change the value; B or OK leaves. The
-- changes are sent to the runtime when the page is left (UiSoundManager:play_se_label("recomp_set:...")), which
-- writes them into graphics.ini; they take effect at the next start.

local ui = UiScriptPlayer.require("common/ui_common")

-- replaced by the runtime, same length, with "key=value;..." from graphics.ini
local VALUES = "@@RECOMP_VALUES@@                                                                                                                                                                                                                                                                                                "

local ROWS = {
    { key = "window", title = "Window", choices = { "windowed", "borderless", "fullscreen" },
      names = { "Windowed", "Borderless", "Fullscreen" } },
    { key = "resolution_scale", title = "Resolution", choices = { "0.5", "0.75", "1.0" },
      names = { "50%", "75%", "100%" } },
    { key = "vsync", title = "VSync", choices = { "clock", "on", "off", "mailbox" },
      names = { "Clock", "On", "Off", "Mailbox" } },
    { key = "anisotropic_filtering", title = "Texture filtering", choices = { "game", "2", "4", "8", "16" },
      names = { "Game", "2x", "4x", "8x", "16x" } },
    { key = "frame_time_overlay", title = "Frame time", choices = { "off", "on" }, names = { "Off", "On" } },
}

local FOOTER = 99                 -- the OK button of the frame (set_footer_area)
-- the Smash options' own pictures in a row (rule icons, fighter art), hidden on this page
local ROW_PICTURES = { "btn_pic", "set_pic_icn", "bg_00", "bg_01", "pic_rep_peach", "pic_rep_daisy" }

local selector = nil
local changed = false

local function parse_values()
    local values = {}
    local pos = 1
    while true do
        local eq = string.find(VALUES, "=", pos, true)
        if eq == nil then break end
        local semi = string.find(VALUES, ";", eq, true)
        if semi == nil then break end
        values[string.sub(VALUES, pos, eq - 1)] = string.sub(VALUES, eq + 1, semi - 1)
        pos = semi + 1
    end
    for _, row in ipairs(ROWS) do
        row.index = 1
        local v = values[row.key]
        for i, c in ipairs(row.choices) do
            if c == v or tonumber(c) ~= nil and tonumber(c) == tonumber(v) then row.index = i end
        end
    end
end

local function row_parts(i)
    return layout_view:get_parts(string.format("set_parts_btn_%02d", i - 1))
end

local function hide_pictures(i)
    local parts = row_parts(i)
    for _, name in ipairs(ROW_PICTURES) do
        local pane = parts:get_pane(name)
        if pane:is_valid() then pane:set_visible(false) end
    end
end

local function show_row(i)
    local row = ROWS[i]
    local text = string.format("%s:  < %s >", row.title, row.names[row.index])
    row_parts(i):get_pane("set_txt_title"):set_text_string(text)
end

local function step(i, delta)
    local row = ROWS[i]
    if row == nil then return end
    row.index = (row.index - 1 + delta) % #row.choices + 1
    changed = true
    UiSoundManager:play_se_label("se_system_cursor")
    show_row(i)
end

local function save()
    if not changed then return end
    local parts = {}
    for _, row in ipairs(ROWS) do
        parts[#parts + 1] = row.key .. "=" .. row.choices[row.index]
    end
    UiSoundManager:play_se_label("recomp_set:" .. table.concat(parts, ";"))
end

function main()
    layout_view = layout_root:get_root_view()
    virtual_input = layout_root:get_virtual_input()
    layout_root:set_enable_input(false)
    exit_code = SCENE_EXIT_CODE_NONE
    parse_values()

    selector = LayoutButtonSelector:new()
    local config = LayoutButtonSelectorConfig:new()
    config.is_unique_se = true
    config.decide_se_label_code = "se_system_fixed"
    config.cursor_se_label_code = "se_system_cursor"
    selector:setup(layout_view, "selector_0", config)
    for i = 1, #ROWS do
        selector:setup_button(i, string.format("set_parts_btn_%02d", i - 1))
        selector:get_button(i):set_decidable(false)
    end
    selector:setup_button(FOOTER, "set_footer_area")
    selector:get_button(FOOTER):set_decidable(false)
    for _, name in ipairs({ "set_parts_sub_btns_02", "set_parts_sub_btns_03", "set_parts_sub_btns_04" }) do
        layout_view:get_parts(name):set_visible(false)
    end
    for i = 1, #ROWS do
        hide_pictures(i)
        show_row(i)
    end

    selector:select_button(1)
    selector:set_focus(true)
    selector:set_enable(true)
    layout_view:play_animation("in", 1.0)
    ui.fade_in(false)
    layout_root:set_enable_input(true)

    local last = nil
    while exit_code == SCENE_EXIT_CODE_NONE do
        if selector:is_cancelled() == true then
            ComFrameActor.press_cancel()
        end
        if ComFrameActor.is_cancelled() == true or ComFrameActor.is_decided() == true then
            if ComFrameActor.is_decided() == true then UiSoundManager:play_se_label("se_system_fixed") end
            save()
            exit_code = SCENE_EXIT_CODE_CANCEL
        else
            local id = selector:get_selected_button_id()
            if id ~= last then
                if id == FOOTER then ComFrameActor.select() else ComFrameActor.unselect() end
                last = id
            end
            if virtual_input:is_repeated(BUTTON_EX_LEFT) == true then
                step(id, -1)
            elseif virtual_input:is_repeated(BUTTON_EX_RIGHT) == true then
                step(id, 1)
            end
        end
        coroutine.yield()
    end
    layout_root:set_enable_input(false)
    ui.fade_out(true)
end
