# Naming and cleaning up: where names can come from, and an order of work

Today 9.7% of main's 112,890 lifted functions have a name: 4.3% exported, 5.4% from vtables of classes with
type info ([CODE_MAP.md](CODE_MAP.md)). The lifted C has no types, no locals and no structure
([LIFTED_CODE.md](LIFTED_CODE.md)). This page lists the sources of more names, how far each can go, and a
proposed order. Nothing here is done yet unless it says so.

## The rule that shapes all of it

The repository may hold **names, addresses, offsets and rules**, never the game's code ([CONTRIBUTING.md](../../CONTRIBUTING.md)).
So cleaning up cannot mean committing cleaned-up C of game functions. It means:

1. a **names database** in the repository (address → name, kind, source, confidence);
2. **layouts** of structures as offsets and field names;
3. **lifter rules** that use 1 and 2 to emit more readable C on each person's PC;
4. and, where the port needs to *own* a part (as with the fighter scripts), a decompiler that produces source
   on the user's PC, checked against the original by a differential test.

## Sources of names

| # | Source | What it can name | Status |
|---|---|---|---|
| 1 | Exported symbols | 4,887 functions, with full C++ signatures | done (`symbols.tsv`) |
| 2 | Type info → vtables | 6,041 virtual functions of 1,146 middleware classes, by class (not by method) | done (`vtables.tsv`) |
| 3 | **`lua_bind` wrappers → module methods** | The modules' virtual methods. 1,271 of the 2,224 wrappers are exactly "load module at accessor + *m*, call vtable slot *s*". That names slot *s* of the module at accessor offset *m* (42 modules, for example `AttackModule` at `+0xa0`, `clear_all` at slot 10) | **extracted** (`modules.tsv`); the remaining 953 wrappers have other shapes (arguments moved first, inlined code) |
| 4 | Module vtables at run time | The *functions* behind 3: read accessor + *m* → module → vtable from a running match. That turns about 1,300 slot names into function names, per concrete class (fighter, weapon and item modules differ) | proposed: a debug-mode dump (`runtime/debug_mode.c` already finds the battle objects) |
| 5 | Known open-source libraries | Lua 5.3, libcurl 7.55.1, zlib, zstd, xxHash, Box2D, boost, libc++. Build each version and match functions by their instruction patterns. Lua alone has hundreds of functions, and its two anchors are known (`0x71038faf00`, `0x71038fae7c`) | proposed. Versions seen in main's strings: `Lua 5.3`, `libcurl/7.55.1` **[measured]** |
| 6 | Thread names and entry points | the body of each named thread (passed to the wrapper `0x71037cccb0`) | partly done ([CODE_MAP.md](CODE_MAP.md)); the bodies still need to be read off the creation calls |
| 7 | Log and assert strings | the function that references a string with a function or file name in it | proposed: a string → referencing-function index (ADRP+ADD pairs, as `recomp.py` already scans) |
| 8 | Call-graph propagation | callers and callees of named functions, for example the `sv_animcmd` commands → the module methods they call | proposed |
| 9 | Community knowledge | names in public, open-source modding projects (`skyline-smash`, `smash-arc`, ARCropolis, `param-labels`), which the runtime already credits | case by case, keeping each project's licence |
| 10 | Our own runtime work | the 198 addresses in [KNOWN_ADDRESSES.md](KNOWN_ADDRESSES.md), today only as comments | proposed: move them into the names database (#1 below) |

Sources 3+4 and 5 are the cheapest large gains: 3+4 names the battle-object modules (the heart of gameplay)
and 5 names whole libraries in one go.

## A proposed order

1. **Names database.** A file such as `tools/names/main_13.0.4.tsv` (address, name, kind, source, confidence)
   that `tools/audit/game_index.py` merges with what it extracts. The runtime's existing addresses go in
   first. Names and addresses only, so it can be committed.
2. **Lifter emits names.** `recomp_eh.py` writes `/* AttackModule::clear_all (slot 10) */` above a function, and
   a comment at call sites (`f_7101fd00c0(c); /* lua_bind AttackModule__clear_all */`). Since the C is
   generated on each PC, this changes nothing in the repository but `tools/`.
3. **Module dump (source 4)** and **library matching (source 5)**. Each adds thousands of names to 1.
4. **Layouts.** Write down what is known as structures with offsets: the match block, `FighterEntry`, the
   resource service, `BattleObjectModuleAccessor` (the 42 module offsets of `modules.tsv`). The lifter can
   then comment memory accesses (`rd64(c->x[0] + 0xa0) /* accessor->attack_module */`).
5. **More readable C, as lifter rules**, each checked with `difftest.py` and a scripted match:
   - fold a compare and its conditional branch into one C condition, without materialising `NZCV`, when no
     other instruction reads the flags (the largest single source of noise);
   - keep registers in locals inside leaf functions, which also helps speed;
   - recognise prologue and epilogue (saved registers) and drop them from the readable form;
   - rebuild loops and ifs from the label graph where it is reducible.
6. **Regions that matter, decompiled for real**, the way the fighter scripts already are (`fighter_sdk/tools`):
   the main loop and the scene system first. They are not mapped yet ([SUBSYSTEMS.md](SUBSYSTEMS.md), *Time
   and the frame*). Then whatever a feature needs (rollback netcode needs the full game state; menus need the
   scene code).

## Open questions worth a first look

- Where is the main loop, and how does a scene (title, menus, match, results) start and end?
- Which functions own the battle objects' update order in a frame (fighters, weapons, items, stage)?
- What is the job system's API (submitting a job, waiting on a group), and which systems use it?
- What runs Lua at run time besides the computer players?
- Which statically linked library versions are exact (zstd, Box2D, boost)?
