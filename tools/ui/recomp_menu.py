"""The "Graphics" entry of the game's Options menu, made from YOUR dump at setup time.

    python tools/ui/recomp_menu.py [--build build-linux] [--arc <data.arc>]

Reads two files of the Options screen out of data.arc, changes them, and writes the changed copies as loose files
(runtime/loose_files.c serves them in place of the archive's, with SSBU_LOOSE_FILES=1):

    ui/layout/menu/option_top/option_top/layout.arc   option_top.bflyt: the buttons made smaller (four rows) and
                                                       a seventh, a copy of set_btn_05 named set_btn_06, added to the
                                                       group the button selector walks ("selector")
    ui/script/menu/option/option_top.lc                its set-up function also sets up set_btn_06 as button
                                                       BUTTON_ID_RECOMP and writes its label; its main function, when
                                                       that button was chosen, sends "recomp_open" to the runtime
                                                       (runtime/recomp_menu.c) and leaves as if Smash had been chosen

and compiles our page, tools/ui/lua/recomp_settings.lua, as the Smash options screen's script into a folder the
runtime serves only after "recomp_open" (runtime/recomp_menu.c). The compiler is a luac 5.3 built with float numbers
(--luac, default deps/lua53f/luac).

Output: <build>/ui_patch/recomp/ and <build>/ui_patch/@recomp_page/ (archive paths below them); run the game with
SSBU_LOOSE_FILES=1 SSBU_CHARACTERS=<build>/ui_patch.
The game's files are not in this repository; only what we change is described here (CONTRIBUTING.md rule 1).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'tools', 'nro'))
import bflyt          # noqa: E402
import luabc          # noqa: E402
import sarc           # noqa: E402

BUTTON_ID_RECOMP = 64          # the game's own ids on this screen are -1 (none) and 1..7
LABEL = 'Graphics'
LAYOUT = 'ui/layout/menu/option_top/option_top/layout.arc'
SCRIPT = 'ui/script/menu/option/option_top.lc'
PAGE_SCRIPT = 'ui/script/menu/option/option_melee_top.lc'
PAGE_SOURCE = os.path.join(HERE, 'lua', 'recomp_settings.lua')
VALUES_MARK = '@@RECOMP_VALUES@@'

# four rows instead of three. The buttons and the cursor are children of one null pane (anim_Rs_Ls_00, at the
# origin): that pane is scaled, so the cursor, which the selector fits to the chosen button, is scaled with them.
# Positions below are on screen; the children's are divided by the scale. The seventh button goes into the left
# column: the selector moves up and down within a column, so a button between the columns is never reached.
SCALE = 0.8
HOLDER = 'anim_Rs_Ls_00'
COLUMNS = (-400.0, 268.0)
ROWS = (300.0, 105.0, -90.0)
NEW_POS = (COLUMNS[0], -285.0)


def patch_layout(arc_bytes):
    files, align = sarc.read(arc_bytes)
    for f in files:
        if f[0] == 'blyt/option_top.bflyt':
            lay = bflyt.Layout(f[1])
            lay.set_scale(HOLDER, SCALE, SCALE)
            for i in range(6):
                lay.set_pos('set_btn_%02d' % i, COLUMNS[i // 3] / SCALE, ROWS[i % 3] / SCALE)
            lay.copy_pane('set_btn_05', 'set_btn_06')
            lay.set_pos('set_btn_06', NEW_POS[0] / SCALE, NEW_POS[1] / SCALE)
            lay.group_add('selector', 'set_btn_06')
            f[1] = lay.bytes()
            return sarc.write(files, align)
    raise ValueError('option_top.bflyt is not in the layout archive')


def _emit_setup(p):
    """instructions of: selector:setup_button(BUTTON_ID_RECOMP, "set_btn_06")
                        view:get_parts("set_btn_06"):get_pane("set_txt_off_melee"):set_text_string(LABEL)
    R0 is the function's parameter (the layout view), the selector is upvalue 0: as in the calls before them"""
    K, RK = p.const, luabc.RK
    A, Bx = luabc.ABC, luabc.ABx
    return [
        A('GETUPVAL', 2, 0),
        A('SELF', 2, 2, RK + K('setup_button')),
        Bx('LOADK', 4, K(BUTTON_ID_RECOMP)),
        Bx('LOADK', 5, K('set_btn_06')),
        A('CALL', 2, 4, 1),
        A('SELF', 2, 0, RK + K('get_parts')),
        Bx('LOADK', 4, K('set_btn_06')),
        A('CALL', 2, 3, 2),
        A('SELF', 2, 2, RK + K('get_pane')),
        Bx('LOADK', 4, K('set_txt_off_melee')),
        A('CALL', 2, 3, 2),
        A('SELF', 2, 2, RK + K('set_text_string')),
        Bx('LOADK', 4, K(LABEL)),
        A('CALL', 2, 3, 1),
    ]


def _emit_choice(p, idreg, env):
    """before `result = <id>`: if id == BUTTON_ID_RECOMP then UiSoundManager:play_se_label("recomp_open");
    id = BUTTON_ID_MELEE end   (R idreg+1 .. idreg+3 are free there: the function's last values are below them)"""
    K, RK = p.const, luabc.RK
    A, Bx = luabc.ABC, luabc.ABx
    t = idreg + 1
    p.maxstack = max(p.maxstack, t + 3)
    return [
        A('EQ', 0, idreg, RK + K(BUTTON_ID_RECOMP)),       # equal: skip the jump
        luabc.AsBx('JMP', 0, 5),
        A('GETTABUP', t, env, RK + K('UiSoundManager')),
        A('SELF', t, t, RK + K('play_se_label')),
        Bx('LOADK', t + 2, K('recomp_open')),
        A('CALL', t, 3, 1),
        A('GETTABUP', idreg, env, RK + K('BUTTON_ID_MELEE')),
    ]


def patch_choice(p):
    """the main function: the one that does `result = <decided id>`"""
    for pc, ins in enumerate(p.code):
        op, a, b, c, bx, _ = luabc.decode(ins)
        if op == luabc.OP['SETTABUP'] and b >= luabc.RK and p.consts[b - luabc.RK] == 'result' and c < luabc.RK:
            p.insert(pc, _emit_choice(p, c, a))
            return True
    return False


def patch_script(lc_bytes):
    head, nup, main = luabc.load(lc_bytes)
    if not any(patch_choice(p) for p in main.protos):
        raise ValueError('no `result = <register>` in option_top.lc')
    for p in main.protos:
        # the set-up function: the one that passes "set_btn_05" to setup_button; insert after that call
        for pc, ins in enumerate(p.code):
            op, a, b, c, bx, _ = luabc.decode(ins)
            if op == luabc.OP['LOADK'] and p.consts[bx] == 'set_btn_05':
                call = next(k for k in range(pc + 1, len(p.code)) if luabc.decode(p.code[k])[0] == luabc.OP['CALL'])
                if p.maxstack < 6:
                    raise ValueError('set-up function has %d slots, 6 needed' % p.maxstack)
                p.insert(call + 1, _emit_setup(p))
                if len(p.consts) > 255:
                    raise ValueError('too many constants for RK operands')
                return luabc.dump(head, nup, main)
    raise ValueError('no setup_button call with "set_btn_05" in option_top.lc')


def compile_page(luac):
    src = open(PAGE_SOURCE).read()
    if VALUES_MARK not in src:
        raise ValueError('%s has no %s placeholder' % (PAGE_SOURCE, VALUES_MARK))
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'page.lc')
        subprocess.run([luac, '-s', '-o', out, PAGE_SOURCE], check=True)
        data = open(out, 'rb').read()
    luabc.load(data)                       # same format as the game's (float numbers)
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--build', default=None, help='build folder (default build-linux, else build)')
    ap.add_argument('--arc', default=None, help='data.arc (default: the dump scripts/setup.py recorded)')
    ap.add_argument('--luac', default=os.path.join(REPO, 'deps', 'lua53f', 'luac'), help='luac 5.3 built with -DLUA_FLOAT_TYPE=1')
    args = ap.parse_args()
    build = args.build or next((os.path.join(REPO, b) for b in ('build-linux', 'build') if os.path.isdir(os.path.join(REPO, b))), None)
    if not build:
        sys.exit('no build folder: run scripts/setup.py first')
    arc_path = args.arc
    if not arc_path:
        state = json.load(open(os.path.join(build, 'state.json')))
        arc_path = os.path.join(state['dump']['romfs'], 'data.arc')
    os.environ['SSBU_ARC'] = arc_path
    import arc_extract
    arc = arc_extract.Arc(arc_path)
    out_root = os.path.join(build, 'ui_patch', 'recomp')
    for path, fn in ((LAYOUT, patch_layout), (SCRIPT, patch_script)):
        data = arc.get(path)
        if data is None:
            sys.exit('%s is not in %s' % (path, arc_path))
        new = fn(data)
        out = os.path.join(out_root, path)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, 'wb').write(new)
        print('%s: %d -> %d bytes' % (path, len(data), len(new)))
    page = compile_page(args.luac)
    out = os.path.join(build, 'ui_patch', '@recomp_page', PAGE_SCRIPT)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, 'wb').write(page)
    print('%s: our page, %d bytes' % (PAGE_SCRIPT, len(page)))
    print('written under %s; run with SSBU_LOOSE_FILES=1 SSBU_CHARACTERS=%s' % (os.path.dirname(out_root), os.path.dirname(out_root)))


if __name__ == '__main__':
    main()
