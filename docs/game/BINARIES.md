# The program images

The game ships as an ExeFS (the executables) plus a RomFS (its data, mostly one large archive `data.arc`).
`scripts/setup.py` (steps `stage` and `convert`) turns your dump's ExeFS files into ELF files in
`<build>/binaries/13.0.4/`, using `tools/extract/nso2elf.py`. All sizes are of 13.0.4 **[measured]**.

## The four ExeFS executables

| Image | Loaded at (runtime) | Code | What it is | What the port does with it |
|---|---|---|---|---|
| `main` | `0x7100000000` | 0x39c7e90 bytes (57.8 MB) | The game: engine, gameplay, menus, plus the middleware it links statically (see [CODE_MAP.md](CODE_MAP.md)) | **Lifted whole** to C (`tools/lifter/recomp_eh.py`) and compiled into `ssbu_game_eh_o1.dll` / `.so` |
| `sdk` | `0x75xxxxxxxx` | 0x540950 bytes (5.3 MB) | The console's system libraries (`nn::*`), linked into every title | Mostly **re-implemented** by `runtime/nn*.c`. 1,375 of its functions (from 806 roots, `tools/lifter/sdklift_roots.txt`) are lifted into `sdklift_gen.c` where re-implementing them would only copy them (`tools/lifter/sdklift.py`) |
| `subsdk0` | not loaded | 3.4 MB file | The movie decoder middleware | Not run. `runtime/nnmovie_mf.c` replaces it and cites its offsets as `[subsdk]` |
| `rtld` | not loaded | 7 KB | The console's dynamic loader | Not needed: the runtime loads and links images itself (`runtime/hostrt.c`, `runtime/nnro.c`) |

`main.npdm` (process metadata) is read only for the main thread: stack 0x100000 bytes, priority 44, core 0
(seen in every run log) **[measured]**.

## main's layout

| Section | Address | Size |
|---|---|---|
| `.text` (code) | `0x7100000000` | 0x39c7e90 |
| `.rodata` (constants, strings, exception tables) | `0x71039c8000` | 0x7cf610, plus `.rodata.1` 0xcc8c5c |
| `.dynsym` / `.dynstr` (names, see below) | `0x7104197610` | 7,126 symbols |
| `.data` (initialised globals, vtables after relocation) | `0x7104f16000` | 0x387748, plus `.data.1` 0xa688 |
| `.bss` (zeroed globals) | `0x71052a8ff8` | 0x219d100 (33.6 MB) |

Guest memory is mapped at these real addresses, so a pointer in the game's data is a valid host pointer. That
is why the lifted C can read and write memory directly ([LIFTED_CODE.md](LIFTED_CODE.md)). The relocations
(`R_AARCH64_RELATIVE` and friends) are applied by the runtime at load time.

### Names in main **[symbol]**

`main` is stripped of its static symbols, but it keeps a dynamic symbol table, because the script modules
link against it:

- **4,887 exported functions** (and 31 objects). They are the interface the script modules call:
  `app::lua_bind::*` (2,224), `app::sv_animcmd::*`, `app::ai::*`, the `app::FighterSpecializer_<name>::*`
  helpers, `lib::L2CValue` / `lib::L2CAgent` and more. [CODE_MAP.md](CODE_MAP.md) has the full breakdown.
- **2,205 imports**:
  - 1,045 `lua2cpp::*` functions that main calls in the `lua2cpp_common` module.
  - The system libraries: `nn::os`, `nn::fs`, `nn::hid`, `nn::mii`, `nn::album`, `nn::socket`, `nn::ssl`,
    `nn::account`, `nn::friends`, `nn::ldn` and others.
  - The C library and libc++ (`std::__1`).
  - The `movie::*` interface of `subsdk0`.
  - 8 weak `glslc*` imports: a run-time shader compiler that no retail console has. The runtime traps if one
    is ever called (`runtime/nnnet.c`) **[runtime]**.

  Every import is either answered by the runtime or reported as missing (`tools/audit`).
- **About 1,300 class names from run-time type information.** They belong only to the middleware compiled
  with RTTI: `nn::nex` 458 classes, `nu` 277, `nn::pia` 268, `nn::ui2d` 60, `pead` 25, `boost` 18, `nn::font`
  13, `nn::vfx` 10, and the Box2D classes `b2*`. **The game's own `app::` classes have none** (built without
  RTTI) **[measured]**.

## The script modules (NROs)

98 dynamically loaded modules, all named `lua2cpp_<name>.nro`. Each is loaded with `nn::ro` when it is needed
**[measured]** (`<build>/work/ro_study/nro_map.json`):

- `lua2cpp_common`: what all fighters share (the common status scripts and the state machine). main imports
  1,045 functions from it.
- `lua2cpp_item`: items, the largest at 10.4 MB of code.
- 96 per-fighter modules: one per fighter (`mario`, `kirby`, ..., the Mii fighters and the
  variants `miienemyf/g/s`, and transformations such as `koopag`, Giga Bowser). For example `lua2cpp_kirby`
  is 5.5 MB, `lua2cpp_trail` 4.9 MB.

Together they hold 156 MB of code. The runtime gives each a fixed base from `0x7200000000` to `0x7210965000`
(`runtime/nro_registry.c`, generated), so addresses in them are stable from run to run. Each is lifted on its
own (`tools/nro/recomp_nro.py`, `<build>/work/gen_nro_eh_o2/<module>/`) and built into its own library
(`nro_bin_eh_o2/`).

The name `lua2cpp` says what they are. The fighters' behaviour was written as Lua scripts (status
logic and the per-move "animation command" scripts) and compiled ahead of time to C++, which then calls back
into main through `app::lua_bind::*` and `lib::L2CValue` / `lib::L2CAgent` **[symbol]** **[inferred: the
build process]**. This is the code the fighter SDK decompiles back to source ([../MODDING.md](../MODDING.md)).
