# Mr. Game & Watch (`gamewatch`)

State: 2026-10-15, first version. Where this fighter's code is, how it is organised, and what each part does.
Our own descriptions, with names and addresses only: no damage, angle or frame values. Those are in the source
you generate on your PC (section 5). Claims are marked as in [../README.md](../README.md).

## 1. Where his code is

| Part | Where | Size | What |
|---|---|---|---|
| Script module | `lua2cpp_gamewatch.nro`, loaded at `0x7205400000` | 1 MB of code, about 1,300 functions | Almost everything: the move scripts and the state logic **[measured]** |
| Native code in main | `app::FighterSpecializer_GameWatch::init_panel` at `0x7100a84470` | 112 bytes | The only exported function of his in main **[symbol]** |
| More native code (likely) | between Fox's last export (`0x7100a698f0`) and Gaogaen's first (`0x7100ab86f0`) | 490 functions, 315 KB, none named | The per-fighter code is in alphabetical order of internal names, and "GameWatch" falls between "Fox" and "Gaogaen" **[inferred]** |

He has **no module classes of his own**. He uses the same battle-object modules as every fighter: `AttackModule`,
`ArticleModule`, `WorkModule`, `VisibilityModule` and the rest ([../SUBSYSTEMS.md](../SUBSYSTEMS.md)). What
makes him "him" is the scripts that call those modules, the props (articles) he spawns, and a few
constants. (Some fighters do get a dedicated module, such as `FighterBayonettaFinalModule`. He does not.)

## 2. How the script module is organised

The module exports 11 functions, one per kind of **agent** (script object) it can create **[symbol]**:

| Agent | What its scripts do |
|---|---|
| `animcmd_game` | gameplay: hitboxes (`ATTACK`), grabs, frame timing, spawning and steering props, flags for the state logic |
| `animcmd_effect` | visual effects |
| `animcmd_sound` | sound effects |
| `animcmd_expression` | rumble, screen shake, which model parts are visible (his flat-sprite poses) |
| `animcmd_*_share` (4) | the same four kinds, shared between agents |
| `status_script` | the state machine: what each status (special moves, the Final Smash's control states) does every frame |
| `ai_mode`, `ai_action` | computer-player behaviour |

Inside the module the animation-command scripts are registered by **hash40 of their name** (`game_attack11`,
`effect_specialhi`, ...). The decompiler recovers the names from the public label list. It found **917
scripts**: 365 expression, 215 game, 205 sound, 131 effect, plus 1 it could not name. 846 have a body of their
own (the others reuse another script's), and **824 of those** came out as readable source **[measured]**.

## 3. His props (articles)

Props are separate battle objects ("weapons") that he generates. Their kinds, from the public constant names
**[symbol: skyline-smash `lua_const.rs`]**, with what they are **[inferred from the names and the scripts that
generate them]**:

| Weapon kind | What it is | Spawned by |
|---|---|---|
| `NORMAL_WEAPON` | the hand-held prop of a normal attack, with a sub-kind per prop: `CHAIR`, `MANHOLE`, `SPRAY`, `TROPICALFISH`, `TURTLE`, `FIRE` | jab (spray), forward tilt (chair), down tilt (manhole), neutral air (tropical fish), the down taunt (spray), ... |
| `FOOD` | Chef's food: separate scripts `sausage`, `steak`, `fish`, `friedprawns` | neutral special |
| `PANEL` | Judge's number panel | side special |
| `OIL` | the Oil Panic bucket's contents when thrown | down special (`speciallwshoot`) |
| `RESCUE`, `PARACHUTE` | up special (Fire): the rescuers and the parachute **[inferred]** | up special |
| `BOMB` | a thrown bomb | forward air |
| `BREATH` | a puff of air | up air |
| `OCTOPUS` | the Final Smash | `final*` |
| `ENTRY` | the on-screen entrance | match start |

The pattern for a prop move is the same in every script:
1. Remove any prop that is still out (`ArticleModule::remove_exist`).
2. Choose the sub-kind (`WorkModule::set_int` on the "normal weapon kind" work variable).
3. Spawn it (`ArticleModule::generate_article`).
4. Play its animation in step with his (`ArticleModule::change_motion`).

The prop does not carry the hitboxes of most normals. His own `ATTACK`s are placed relative to his
model: 122 of 130 on `top`, his root bone, with fixed offsets, and only 2 on `haver`, the bone that holds
the prop **[measured]**. That fits a flat character whose poses are swapped rather than animated **[inferred]**.

## 4. The moves

How each kind of move is built. The internal names are the left column; the counts are hitbox calls in the
`game_` script **[measured]**.

| Move | Scripts | How it is built |
|---|---|---|
| Jab | `attack11`, `attack100`, `attack100end` | Spray prop. The rapid jab `attack100` did **not** decompile: it calls a function local to the module |
| Tilts | `attacks3` (chair, 6 hitbox calls), `attackhi3` (6), `attacklw3` (manhole, 2) | Prop spawned and removed inside the script |
| Smashes | `attacks4` (3), `attackhi4` / `attackhi4r` (2, with `WHOLE_HIT` changes of his body's hit state), `attacklw4` (5) | No prop. Charging is in the common state machine, not here **[inferred]** |
| Aerials | `attackairn` (tropical fish, 15 hitbox calls, a multi-hit **[inferred]**), `attackairf` (spawns and fires `BOMB`), `attackairhi` (spawns `BREATH`), `attackairlw` (2, with a landing script `landingairlw`), `attackairb` | `attackairb` and two landing scripts did not decompile |
| Neutral special: Chef | `specialn`, `specialairn` (and `__1` variants), food scripts `sausage`, `steak`, `fish`, `friedprawns` | His script swings the pan. Each food item is its own small object with one hitbox. Which food comes out is decided outside these scripts **[open]** |
| Side special: Judge | `specials1`..`specials9`, `specialairs1`..`9` | Nine scripts, one per number. Below |
| Up special: Fire | `specialhi`, `specialhiopen`, `specialhiclose` (and air variants) | A hitbox on the launch. The rest only toggle the visible model parts (`VisibilityModule::set_int64`); the fall is the status `SPECIAL_HI_FALL` |
| Down special: Oil Panic | `speciallw`, `speciallwstart`, `speciallwend`, `speciallwshoot` | Catching, waiting, reflecting and shooting are statuses (`SPECIAL_LW_CATCH`, `_WAIT`, `_REFLECT`, `_SHOOT`, ...). `speciallwshoot` shows the `OIL` article, has 3 hitbox calls and calls `AttackModule::set_special_paint` |
| Grabs and throws | `catch`, `catchdash`, `catchturn`, `catchattack`, `throwf/b/hi/lw` | The throws use absolute attacks (`ATTACK_ABS`) on the held fighter, as every fighter's throws do. `thrownf/b/hi/lw` (being thrown *by* him) did not decompile |
| Final Smash: Octopus | `finalstart`, `finalairstart`, `finalhold`, `finalwait`, `finalattack`, `finalend`, ... | Start: camera zoom, cut-in, slows opponents, scales him. `finalattack` grabs with the octopus's legs (patterns `LEG_A_PATTERN_1/2`). He is controlled as an octopus through his own statuses `FINAL_WALK`, `FINAL_RUN`, `FINAL_JUMP`, `FINAL_SWIM_*`, ... (28 of them) |
| Item use | `batswing*`, `clubswing*`, `starrodswing*`, `lipstickswing*`, `firebarswing*`, `deathscytheswing*`, `lgunshoot*`, `scope*`, `fflowershoot*`, `drillshoot*`, `steeldivershoot*`, `genesisfire*` | He has his own animations, and so his own scripts, for most items |

### Judge (side special) in detail

**Pieces** **[symbol]**:
- The number is a **panel kind**, `FIGHTER_GAMEWATCH_PANEL_KIND_1`..`9`, numbered from 0, so panel 9 is kind 8.
- Two per-fighter work variables hold it. `INSTANCE_WORK_ID_INT_SPECIAL_S_KIND` is the number for this use,
  where -1 means not drawn yet. `INSTANCE_WORK_ID_INT_SPECIAL_S_PREV_KIND` is the number of the previous use.
- `INSTANCE_WORK_ID_INT_SPECIAL_S_PANEL_7_INVALID_FRAME` is a countdown that blocks 7.
- The parameters are in the fighter's `param_special_s`:
  - `panel_1`..`panel_9`: one weight per number;
  - `panel_9_up_time`: a time window in seconds;
  - `panel_9_up`: a percentage;
  - `panel_7_invalid_frame` and the `panel_7_tabemono_*` group (number 7's food);
  - `critical_*` (9's zoom), including `critical_1_on_1`.
- Main's `FighterSpecializer_GameWatch::init_panel` prepares the panel object. It calls three module methods
  through their vtables, then an unnamed 3.5 KB function right after it (`f_7100a844e0`) **[measured]**.

**The draw.** One function of the status logic, at `lua2cpp_gamewatch` + `0x50d0`, chooses the number. Read
from its lifted code on 2026-10-15 **[measured: control flow and calls; names from the public labels]**:

1. **Home-Run Contest**: if the mode is `contest_homerun`, the number is set to kind 5 (panel **6**) and
   nothing is drawn.
2. **Already chosen**: if `SPECIAL_S_KIND` is not -1, it is kept. Something set it beforehand; what does
   that is **[open]**.
3. **The panel-9 boost** is tried only if all of these hold:
   - the previous number was **not** 9;
   - a match clock is running and is near its end: `0 < remaining frames <= panel_9_up_time x 60`
     (`sv_information::get_remaining_time_as_frame`);
   - you are **far behind the leader** (`FighterManager::get_top_rank_player(0)`), judged by the rule:

     | Rule | "Far behind" means |
     |---|---|
     | Time (score = KOs − self-destructs, `total_beat_count − suicide_count`) | you are not the leader, and: your score is 0 and the leader's is above 0; **or** your score is positive and the leader's is at least twice yours; **or** your score is negative and the leader's is at least half of yours (you −4, the leader −2 or better) |
     | Stock | you are not the leader, and the leader has at least **twice your stocks** |
     | Stamina (HP) | if the leader has more than one stock, at least twice your stocks. With one stock each, the leader has at least **twice your HP**. If you are ranked first yourself, the highest HP among the other first-ranked players is used |
     | any other rule | never |

   If so, `sv_math::rand(hash40("fighter"), 100)` is rolled. A result below `panel_9_up` makes the
   number **9**.
4. **The normal draw**, if no number was set yet: each number gets its weight `panel_N`, with two exceptions:
   - the **previous number** gets weight 0, so a number never repeats twice in a row;
   - **7** gets weight 0 while `SPECIAL_S_PANEL_7_INVALID_FRAME` is above 0.

   One roll of `sv_math::rand(hash40("fighter"), total weight)` is then taken against the cumulative
   weights.
5. The result goes to `SPECIAL_S_KIND`. The motion for ground and air is chosen from the `special_s_<N>` /
   `special_air_s_<N>` names, and number 7 uses its own variant (`special_s_7_l`) depending on the facing
   direction (`PostureModule::lr`) **[inferred]**.

**What it means for the rumor.** It is real, and narrower than "not in first place". The boost needs a running
clock in its last `panel_9_up_time` seconds and a clear deficit: half the leader's stocks, half its score, or
in stamina half its HP. Being second by a little does not qualify. When it applies, 9 comes from the boost
roll, and otherwise still from the normal draw like any other number. A 9 can never follow a 9.

The weights and the two boost values are game data, so they are not reproduced here. Your own dump has them
in `fighter/gamewatch/param/vl.prc`, under `param_special_s`.

**Still open**: where `PANEL_7_INVALID_FRAME` is set (probably after a 7, from `panel_7_invalid_frame`); what
presets `SPECIAL_S_KIND`; what `PREV_KIND` holds before the first Judge of a match; and how the shared Judge
game function builds each number's hitbox.

## 5. Reading the source on your PC

Everything below is generated from your own dump and stays on your PC. Never commit or share it.

1. The decompiler needs two public label projects in `build-linux/reference/` (or `build/reference/`). Neither
   is fetched by `setup.py` yet:
   ```
   git clone --depth 1 https://github.com/ultimate-research/param-labels   build-linux/reference/param-labels
   git clone --depth 1 https://github.com/ultimate-research/skyline-smash  build-linux/reference/skyline-smash
   ```
2. Create the output folder once (`build-linux/work/fighters/tools_out`), then from
   `build-linux/work/fighter_sdk/tools`:
   ```
   ../../../../deps/venv/bin/python l2c_decomp.py gamewatch
   ```
3. Read `build-linux/work/fighters/gamewatch/acmd/`:
   - `game.cpp` (hitboxes and logic);
   - `effect.cpp`;
   - `sound.cpp`;
   - `expression.cpp`;
   - `unknown.cpp` (the script without a known name).

   Search for `ACMD(gamewatch, game_<move>)` with the internal names of section 4.

The `ATTACK(...)` arguments, in order (the column names of `fighter_sdk/tools/hitcat.py`):
1. `id`, `part`, `bone`;
2. `damage`, `angle`, `kbg` (knockback growth), `fkb` (fixed), `bkb` (base);
3. `size` and two positions (`x y z`, `x2 y2 z2`; `NIL` = a single point);
4. `hitlag`, `sdi`, `setoff`, `facing`, `set_weight`, `shield_damage`, `trip`, `rehit`;
5. the flags `reflectable`, `absorbable`, `flinchless`, `no_hitlag`, `direct`;
6. the masks `situation`, `category`, `part_mask`, then `friendly_fire`;
7. `effect`, `sound_level`, `sound`, `region`.

`fighter_sdk/include/fighter_sdk.h` declares the module calls.

Not decompiled yet: his **status scripts** (per-fighter status decompilation is in progress,
[../../MODDING.md](../../MODDING.md) section 6a). Neither is anything in `lua2cpp_gamewatch` that is not an
animation-command script: CPU behaviour, and the local helper functions the failed scripts call.

## 6. Open questions

- Where the shared Judge function builds each number's hitbox, and the remaining Judge questions above.
- How Chef decides which food to throw.
- What the unnamed native functions around `0x7100a84470` do for him. A first step: find which of them his
  module calls through main's exports or through the `FighterSpecializer` dispatch.
