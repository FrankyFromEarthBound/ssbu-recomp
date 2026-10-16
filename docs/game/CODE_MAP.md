# Map of main's code

main's 57.8 MB of code is not shuffled. The linker placed it library by library and, inside the game, area by
area, so the names we have cluster. Together with the addresses the runtime had to understand, they give a
first map. All counts are from `tools/audit/game_index.py` on 13.0.4 **[measured]**. The "what" column says
how it is known.

## By 2 MB region

"Named" counts exported functions; "vtable" counts functions named only by a vtable of a class with type info;
"ours" counts how many places in this repository mention an address in the region.

| Region | Functions | Named | Vtable | Ours | What is there |
|---|---:|---:|---:|---:|---|
| `7100000000` | 8,697 | 5 | 3,514 | 4 | **Network middleware**: `nn::pia` (peer-to-peer sessions), `nn::nex` (game-server client), the `nn::ui2d` layout library, `pead` **[symbol]** |
| `7100200000` | 8,038 | 410 | 1,426 | 19 | the rest of `nn::nex`; then the **computer players**: `app::ai`, `app::ai_param`, `app::ai_<fighter>` **[symbol]** |
| `7100400000` | 5,037 | 11 | 0 | 0 | gameplay utilities (`app::GroundUtility`, `app::KineticUtility`, `app::SlopeModuleSimple`, ...); mostly unnamed |
| `7100600000` | 3,153 | 89 | 0 | 41 | **fighter core**: `app::FighterUtil`, `app::FighterCutInManager`; the fighter-kind tables and `FighterEntry` code the new-fighter work patches (`tools/lifter/patches/kinds_scan.py`, `mods/`, [../../tests/DEBUG_MODE.md](../../tests/DEBUG_MODE.md)) **[runtime]** |
| `7100800000` to `7101400000` | 18,174 | 323 | 0 | 5 | **per-fighter native code**: `app::FighterSpecializer_<fighter>` and `app::Fighter<Name>FinalModule` in alphabetical order of the internal fighter names, from `Brave` to `Yoshi` (51 fighters have one) **[symbol]** |
| `7101400000` | 5,396 | 460 | 0 | 6 | **items and bosses**: `app::item`, `app::boss_private`, `app::target`, stage gimmicks (`app::ground`) **[symbol]** |
| `7101600000` | 3,814 | 623 | 3 | 19 | per-item helpers (`app::pickelobject`, `app::mechakoopa`, ...); also the **character select / match setup** code: player records, stage choice, the per-kind file path builder (`fighter_sdk/newmodule_host.c`, DEBUG_MODE.md) **[symbol] [runtime]** |
| `7101800000` to `7101e00000` | 8,022 | 1 | 0 | 4 | **unnamed**. The roster rows the new-fighter mods touch are here (`mods/mods_n*_src/*_roster*.cpp`) **[runtime]** |
| `7101e00000` to `7102200000` | 9,098 | 2,282 | 0 | 0 | **`app::lua_bind`**: the 2,224 functions script modules call, `Module__function_impl(BattleObjectModuleAccessor*, ...)` **[symbol]** |
| `7102200000` | 3,708 | 445 | 0 | 9 | **the script interface**: `app::sv_animcmd` (animation-command script commands), `app::sv_kinetic_energy`, `app::sv_fighter_util`, `app::sv_math`, `app::sv_battle_object`; the script-module worker that loads `lua2cpp_*.nro` (`0x71022b6620`); the match start (`0x7102311914`) **[symbol] [runtime]** |
| `7102400000` to `7103200000` | 18,833 | 8 | 2 | 2 | **the largest unnamed area** (14.6 MB): a few `std::` functions only. [inferred] menus/modes/scene code, since none of it is exported to scripts and none of the middleware with type info is here |
| `7103200000` | 4,255 | 24 | 3 | 28 | `app::WeaponSpecializer_*` (projectiles) **[symbol]**; system-applet callers: Mii (`0x71032e88a4`), keyboard (`0x7103300584`), web pages (`0x7103341bac`) **[runtime]** |
| `7103400000` | 4,879 | 112 | 8 | 13 | more `WeaponSpecializer_*`, `phx`; the **resource service**: path to file index (`0x710353e330`), the archive's loaders and the inflate thread (`0x71035444c0`), zstd streams (`runtime/host.c`, `runtime/loose_files.c`) **[runtime]** |
| `7103600000` | 4,653 | 94 | 461 | 48 | **engine libraries**: `nu` (graphics device, threads), Box2D, `lib::L2CValue` / `lib::L2CAgent`; the movie player (`0x71036e6000`-`0x71036f9000`, `runtime/nnmovie_mf.c`), album capture, audio output setup (`0x710373ec20`), the common thread entry (`0x71037cccb0`) **[symbol] [runtime]** |
| `7103800000` | 7,133 | 0 | 624 | 56 | `nu`, `boost`; the VSync thread (`0x7103810a40`), frame end and present (`0x710386fc30`), C++ text parsing (`0x7103835e60`..), Lua (`luaD_rawrunprotected` `0x71038faf00`), the memory allocator (`0x710392dce0` / `0x710392e590`), `nus3` audio (`0x7103987890`), xxHash (`0x710399f0b0`), zstd (`0x71039a3e70`) **[runtime]** |

How to read it:

- **Exports cluster where scripts call in.** The 2,224 `lua_bind` functions sit together at `0x7101e00000`-`0x7102200000`
  and the `sv_*` script commands follow. They are thin wrappers: median 16 bytes (four instructions), 90% at most 32
  **[measured]**. Each one forwards to a method of a "module" of the battle object **[inferred]**. They are the best starting point
  for naming the modules themselves ([NAMING_PLAN.md](NAMING_PLAN.md)).
- **Type info only exists for middleware.** It names 6,041 functions, almost all at the two ends of the image (network
  middleware at the bottom, `nu`/Box2D/`boost`/UI at the top).
- **The middle is dark.** `0x7101800000`-`0x7101e00000` and `0x7102400000`-`0x7103200000` together hold about
  27,000 functions (21 MB) with almost no names and almost nothing our runtime needed. What we know about the
  game's main loop, scenes and menus will come from here.

## The threads (a run of 2026-10-15)

From the thread list at the end of a run log (`run.py --seq tests/seq_8cpu.txt`: boot, menus, an 8-fighter
match) **[measured]**. Almost every thread is started through one wrapper whose entry is `0x71037cccb0`
(created from `0x71037cca64`) **[inferred: a `nu` thread class]**. The real work function is an argument. The last column is read from the thread names **[inferred]** unless it
names a source.

| Thread | Priority | Core | What it is |
|---|---:|---:|---|
| main thread | 44 | 0 | the game loop (`main.npdm`) |
| `TaskWorker0..2` | 23 (and 8) | 0, 1, 2 | the job system: workers that run the game's jobs as fibers. Two sets exist (stacks 0x80000 and 0x10000) **[runtime: os_threads.c, the fiber fix]** |
| `render dispatch` (2) | 16 | 1, 2 | building GPU command lists |
| `nu::GraphicsDevice::Update` | 9 | 2 | the graphics device |
| `VSync Count Thread` | 1 | any | counts display refreshes (`runtime/display.c`) |
| `ResLoadingThread`, `ResInflateThread`, `ResUpdateThread` | 8, 16, 16 | 1, 2, 2 | the resource service: reading `data.arc`, decompressing, publishing loaded files |
| `L2CDynamicModuleManager::thread` | 23 | any | loads and unloads the `lua2cpp_*` script modules |
| `nus3::audio update` (entry `0x7103987890`) | 1, then 15 | 2 | the sound mixer (`runtime/nnaudio.c`) |
| `nus3::nopus decode` (several, entry `0x710399d610`) | 1 / 15 | 2 | Opus decoding for streamed sound |
| `nuscFileAsyncRequest` | 1 | 2 | sound file requests |
| `sindou::update` | 1 | 2 | rumble ("sindou" = vibration) **[inferred]** |
| `AsyncTonePreparer`, `AudioDeviceChange` | 16, 1 | 1, 2 | sound preparation; output-device changes |
| `MOVIE_INIT`, `MOVIE_PLAYBACK`, `MoviePlayerThread`, `Movie*InputReadThread`, `MovieDecoderEventHandler` | 16 | 0-2 | the movie player (`runtime/nnmovie_mf.c`) |
| `SaveStorage`, `BCATCacheStorage` | 31 | 1 | save data; downloaded content cache |
| `LibcurlResolver` (entry `0x7100027bf0`) | 16 | 0 | DNS for the statically linked libcurl of the network code |
| `<UNKNOWN>` | 1 | 0 | started first, unnamed |
