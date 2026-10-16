# The engine's parts

What the port has learned about each part of the game, and the addresses that anchor it. The linked runtime
sources hold the detail and the proof. Names in `code` are the game's own [symbol] names. Plain-language names
are ours. For the region each part lives in, see [CODE_MAP.md](CODE_MAP.md).

Two families of in-house libraries appear throughout:

- **`nu`**: the engine's utility and graphics layer: thread wrapper, graphics device, allocation hooks.
  `runtime/nnsdk.c` calls it the NintendoWare utility **[inferred]**.
- **`nus3`**: sound **[symbol: thread names]**.

## Time and the frame

- The game's clock is the **display refresh**, not wall time. A thread `VSync Count Thread` (`0x7103810a40`)
  counts the console's vsync signals. At every present (`0x710386fc30`) the game advances its frame counter
  (`0x710593a440`) by the refreshes counted since the last present. So 60 refreshes are one second of game
  time, however long the frames took **[runtime: `runtime/display.c`]**.
- `0x710386fc30` is the frame end of the graphics layer (it acquires the next window texture). The fighter
  SDK's deterministic mode hooks it **[runtime: `fighter_sdk/fighter_host.c`]**.
- The main thread runs the game loop; where its per-frame entry is has **not** been identified **[open]**.

## Threads, jobs and fibers

- Threads are made through `nu`'s wrapper: entry `0x71037cccb0`, created at `0x71037cca64`. Its argument
  is the real body. [CODE_MAP.md](CODE_MAP.md) lists the threads of a run **[measured]**.
- `TaskWorker0..2` run a job system that uses `nn::os` **fibers**. A job's fiber can be resumed by a
  different worker than the one it left. Two freezes in the port came from exactly this
  **[runtime: `runtime/os_threads.c`, `runtime/nncxxrt.c`]**.
- Each job's fiber gets its own register context. A scene makes thousands (about 14,000 in 25 minutes of
  attract demos) **[measured 2026-10-14]**.

## Memory

- The game allocates with its own allocator: `f_710392dce0(align, size)` and `f_710392e590(ptr)`, with a
  `memalign` at `f_710392dc40`. libc++'s `operator new` inside the game goes there too
  **[runtime: `runtime/libcxx.c`, `runtime/nnsdk.c`]**.
- `nu::VirtualAllocHook` / `VirtualFreeHook` are only told about blocks **[runtime]**.
- main's `.bss` alone is 33.6 MB. Large global blocks such as the match block (below) live there
  **[measured]**.

## Files and the archive

- Everything comes from the RomFS, almost all of it from one archive, `data.arc`, read through `nn::fs`
  **[runtime: `runtime/nnfs.c`]**.
- The **resource service** (a singleton at `*(0x7105331f28)`) keeps the archive's tables in memory: file
  paths, path hash to index, file infos, directories. It runs three threads:
  - `ResLoadingThread`: reads;
  - `ResInflateThread`: decompresses, inflate loop at `0x71035444c0`, one zstd stream step per call to
    `0x71039a4040`;
  - `ResUpdateThread`.

  The offsets and the names of the tables follow the open-source `smash-arc` / ARCropolis tools and were
  checked against `0x710353e4e0`, `0x7103540450` and `0x71035444c0` **[runtime: `runtime/loose_files.c`]**.
- Paths are identified by **hash40**: CRC-32 of the path in the low 32 bits, its length above
  (`fighter_sdk/include/fighter_sdk.h`). A path to file-index lookup is at `0x710353e330`. A per-fighter path
  builder at `0x71017e88d0` formats names such as the module file names **[runtime:
  `fighter_sdk/newmodule_host.c`]**.

## Scripts: L2C, the lua2cpp modules, and Lua

- Fighter, weapon and item behaviour is in the 98 `lua2cpp_*.nro` modules ([BINARIES.md](BINARIES.md)). They are
  C++ generated from Lua. Values pass as `lib::L2CValue`, and the script objects are `lib::L2CAgent`
  subclasses **[symbol]**. They call into main through `app::lua_bind::*` (2,224 functions) and the
  `app::sv_*` command families **[symbol]**.
- Modules are loaded by `L2CDynamicModuleManager::thread`. The worker that formats a module's path and loads
  it is at `0x71022b6620` **[runtime: `fighter_sdk/newmodule_host.c`]**.
- Script constants (`FIGHTER_STATUS_KIND_*` ...) are a table of 19,469 values in `lua2cpp_common`, keyed by
  XXH64 of the name (the hash is at `0x710399f0b0`) **[runtime: `fighter_sdk/tools/luaconst.py`]**.
- A real **Lua VM** is in main as well. Its only `setjmp`/`longjmp` pair is Lua's error handling
  (`luaD_rawrunprotected` `0x71038faf00`, `luaD_throw` `0x71038fae7c`) **[runtime: `runtime/nnmisc.c`]**.
  The computer players' parameter functions take a `lua_State *` (`app::ai_param::param_float(lua_State*,
  phx::Hash40)`) **[symbol]**. Which other systems run Lua at run time is **[open]**.
- Script coroutines end by throwing `lua2cpp::L2CExceptionShutdown`, which is one reason C++ exceptions had
  to work through lifted code **[runtime: `runtime/nnunwind.c`]**.

## Battle objects, modules, fighters

- In a match, fighters, weapons (projectiles), items and others are **battle objects**. Each has a
  `BattleObjectModuleAccessor` holding its **modules** **[symbol: the `lua_bind` signatures]**. The
  `lua_bind` names list 147 module and helper classes. The largest are:

  | Module | `lua_bind` functions |
  |---|---:|
  | `AttackModule` | 195 |
  | `GroundModule` | 146 |
  | `ControlModule` | 129 |
  | `LinkModule` | 119 |
  | `MotionModule` | 101 |
  | `EffectModule` | 92 |

  Then `PhysicsModule`, `ItemModule`, `SoundModule`, `WorkModule` (the per-object flag, int and float
  variables), `CameraModule`, `ShieldModule`, `DamageModule`, `KineticModule` and `StatusModule`
  **[symbol]**.
- Per-fighter native code is in `app::FighterSpecializer_<fighter>` (51 fighters have one) and per-weapon
  code in `app::WeaponSpecializer_<fighter><weapon>` **[symbol]**.
- The engine knows 94 fighter kinds. Their fixed tables and `FighterEntry` (kind at `+0x88`, copied at
  `0x7100653240`) are what the new-fighter work extends **[runtime: [../NEW_FIGHTER.md](../NEW_FIGHTER.md),
  `tools/lifter/patches/kinds_scan.py`]**.
- **The match block** is a global at `0x71052c41b0` (accessor `0x7101388880`). It holds the rules, the stage
  block at `+0x420` and eight player records of 0x260 bytes from `+0x580` **[runtime:
  [../../tests/DEBUG_MODE.md](../../tests/DEBUG_MODE.md)]**.
- Computer players are `app::ai*` (about 300 exported functions at `0x7100200000`-`0x7100400000`)
  **[symbol]**.

## Match setup and menus

- When "Ready to fight" is confirmed, `0x7102311914` → `0x710178f498` calls `0x7101788260` once per player
  to turn the select screen's panel state into player records. It then reads the stage choice (three
  entries at `0x71053097c0`, index at `0x7105309968`) and `0x710178ab60` writes the stage block **[runtime:
  `runtime/debug_mode.c`]**.
- Menus are drawn with `nn::ui2d` (layout library, with type info) **[symbol]**. The menu and mode logic
  itself is not mapped **[open]**. It is probably in the large unnamed area at `0x7102400000`-`0x7103200000`
  **[inferred]**.

## Graphics

- The game gets the console's graphics API (NVN) through `nvnBootstrapLoader` and calls it through function
  pointers. The runtime answers with its own NVN on Vulkan **[runtime: `runtime/nvn_stub.c`, `nvn_vk*.c`]**.
- Command lists are built on two `render dispatch` threads; `nu::GraphicsDevice::Update` runs the device
  **[measured: thread names]**.
- Shaders are GPU machine code inside the game's files. They are translated when loaded
  (`runtime/mx_translate.c`).

## Sound

- `nus3::audio update` (`0x7103987890`) mixes 512 frames at a time into `nn::audio` output buffers, at 48 kHz,
  16-bit **[runtime: `runtime/nnaudio.c`]**.
- `nus3::nopus decode` threads decode Opus with `nn::codec::HardwareOpusDecoder`. The game imports only its
  constructor and calls the rest through the object's virtual table **[runtime]**.
- Output volumes per device are set at `0x71022bca24` **[runtime]**.

## Movies

- The movie player is `0x71036e6000`-`0x71036f9000`. It drives `subsdk0`'s `movie::` interface (extractor,
  decoder, buffers) on threads of its own (`MoviePlayerThread`, the two input readers, the decoder event
  handler). The runtime replaces that interface on Media Foundation, or on FFmpeg on
  Linux **[runtime: `runtime/nnmovie_mf.c`, which documents every call with its address]**.

## Input, saves, system services

- Controllers through `nn::hid` (42 imports). GameCube controller rumble at `0x710366a4f0`; rumble in general
  probably runs on `sindou::update` **[runtime: `runtime/nnhid.c`; inferred for the thread]**.
- Save data: the `SaveStorage` thread and `nn::fs` save mounts. The game sizes its journal as
  `journalSize - 0x80000` (`0x71017c15e4`) **[runtime: `runtime/nnmisc.c`]**.
- System applets: software keyboard (`0x7103300584`), web pages and videos (`0x7103341bac`,
  `0x7103341e50`), Mii (`0x71032e88a4`), album screenshots and video (`0x71036e2090`, `0x71036eb1e0`)
  **[runtime: `runtime/nnapplet.c`, `nnmii.c`, `nnalbum.c`]**.
- Random numbers come from `nn::os::GenerateRandomBytes` and `nn::crypto`. `SSBU_FIXED_SEED` makes them
  repeatable, but random choices such as the stage of "Random Battlefield" still differ between runs, so the
  game also takes randomness from timing **[measured 2026-10-15: `tests/seq_8cpu.txt` runs]**.

## Network

The runtime answers as an offline console: no network connection is ever granted, and sockets reach only
loopback.

The game's online stack is in main:

- Nintendo's `nn::nex` game-server client, version 4.6.13. It talks PRUDP over WebSocket with TLS through
  `nnssl` **[symbol: version and class names]**.
- `nn::pia` for peer-to-peer matches **[symbol]**.
- A statically linked libcurl with its own resolver thread **[measured: thread name]**.
- Local wireless play goes through `nn::ldn` **[symbol]**.

Together they are the bottom 4 MB of the image ([CODE_MAP.md](CODE_MAP.md)). How the game uses them (login,
matchmaking, the battle-arena protocol) is **[open]**.
