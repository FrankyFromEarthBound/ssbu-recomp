# Building and running on Linux

A native Linux build: the game becomes an ELF executable and shared libraries, with SDL3 for the window, sound and
controllers, Vulkan for graphics and FFmpeg for the movies. No Wine and no emulator. As on Windows, the repository holds
no game code: everything is generated on your PC from **your own dump** of Super Smash Bros. Ultimate **13.0.4**.

```
git clone <this repository> ssbu-recomp
cd ssbu-recomp
scripts/linux/install_deps.sh                                   # system packages (sudo), Python venv, small source deps
deps/venv/bin/python scripts/setup.py --target linux --dump <your dump folder>     # about 1.5 hours, resumable
./play.sh                                                       # play
```

## Status

Tested on one PC: CachyOS (Arch-based, kernel 7.2), 24 threads, 31 GB RAM, NVIDIA RTX 3080 (proprietary driver), Wayland.

| | |
|---|---|
| Build from the dump | works: game library, 98 script modules, executable; `check` step passes (SDK self-test, pad self-test 49/49, differential test 32,043 / 32,043) |
| Opening movie, title, menus, character and stage select | work |
| A whole Smash match (scripted input, 1v1 vs CPU) | works: steady 60 fps, sound, no audio underruns |
| Keyboard | works (tested); controllers through SDL3 are the same code as on Windows but **not tested with a real pad on Linux** |
| Album movie recording (the game's video capture to the album) | **not implemented** on Linux; the 7 self-test cases for it are accepted as known failures |
| OpenGL renderer | not on Linux: Vulkan only |
| Online / local wireless | not available (as on Windows: the runtime answers as an offline console) |
| Other distributions, AMD / Intel GPUs, X11, Steam Deck | not tested. The package lists for Debian/Ubuntu and Fedora in `install_deps.sh` are best guesses; `setup.py` checks every library and says which one is missing |

## What you need

- **A dump of the game, 13.0.4** (base game with the 13.0.4 update applied), from a console you own. The layout is the
  same as on Windows, see [BUILDING.md, "What you dump from your own console"](BUILDING.md#what-you-dump-from-your-own-console):
  ```
  <dump>/exefs/          main, sdk, rtld, subsdk0, main.npdm
  <dump>/romfs/          data.arc and the rest of the RomFS
  <dump>/sysdata/fonts/  optional, the console's shared fonts (see "Fonts" below)
  ```
  From `.nsp` files and your own keys: `deps/venv/bin/python tools/extract/romfs_extract.py <base.nsp> <update.nsp> <out>`
  and `tools/extract/nca_exefs.py`; they read the keys from `~/.switch/prod.keys`.
- **RAM:** 16 GB or more. The largest generated C files take 3-4 GB each to compile; `setup.py` runs one compiler per
  4 GB of RAM by default (`--jobs N` overrides it). Running out of memory does not slow the build down, it gets
  processes killed, possibly your desktop's.
- **Disk:** about 12 GB for `build-linux/`, plus about 4 GB of shader cache after the first play session, plus the dump
  itself (about 16 GB).
- **GPU:** a Vulkan 1.3 driver. With `VK_EXT_graphics_pipeline_library` (current NVIDIA, Mesa RADV and ANV drivers have
  it) shaders are prepared ahead of time and nothing is compiled during a fight.
- **A distribution with SDL3 and clang 19 or newer:** current Arch and its derivatives, Fedora 42+, Debian 13,
  Ubuntu 25.04+.

## 1. Dependencies

```
scripts/linux/install_deps.sh
```

It installs, using your package manager (pacman, apt or dnf; asks for sudo):
clang, pkg-config, SDL3, FreeType, OpenSSL, FFmpeg, glslang, the Vulkan loader, libGL, Opus, Python, git.
Then, without root, into `deps/` (ignored by git):

- `deps/venv`: a Python virtual environment with numpy, lz4, zstandard, Pillow, unicorn, capstone, pycryptodome
- `deps/Vulkan-Headers`: the Khronos headers, tag v1.4.351 (the version the port was built with)
- `deps/suyu`: a 2 MB sparse checkout of the Eden emulator's Mii service sources; only reference data for a self-test is
  read from it

`--no-system` skips the package manager if you install the packages yourself. On another distribution the script lists
what to install.

The FFmpeg must be able to decode VP9 and Vorbis (the game's movies). Every distribution's normal FFmpeg can;
`setup.py` checks it.

Optional: `zenity` or `kdialog`. The game's on-screen keyboard (naming a Mii, a ruleset, ...) opens a dialog with one of them.

## 2. Build

```
deps/venv/bin/python scripts/setup.py --target linux --dump <your dump folder>
```

Use the venv's Python, as shown. The build goes into `build-linux/`, in eleven steps. The same table as in
[BUILDING.md](BUILDING.md#the-steps) applies, with `.so` libraries instead of DLLs. Measured on the PC above:

| Step | Time |
|---|---|
| deps, stage, convert, modules, tables, lift, sdk | under a minute together |
| compile (1,161 files, 562 MB game library) | 75 min with 5 jobs |
| nro (98 script modules) | 12 min |
| link, check | about 1 min |

The build can be stopped at any time (Ctrl+C, or a crash). Run the same command again and it continues where it
stopped; finished steps are skipped. Every invocation needs `--dump`. Other options: `--status`, `--redo <step>`,
`--from <step>`, `--jobs N`, `--help`.

When something is missing, step `deps` stops at once with what to install. It compiles and runs a small test program
against every library, so a broken package shows there and not an hour later.

## 3. Play

```
./play.sh
```

It works from any folder (a symlink to it in `~/bin` is fine) and takes the options of `scripts/run.py` (`./play.sh --help`).
The game's output shows in the terminal and is kept in `~/.local/share/ssbu-recomp/last_run.log`.

- Saves go to `~/.local/share/ssbu-recomp/userdata` (`--userdata <folder>` for another one).
- `scripts/linux/install_desktop.sh` adds **Super Smash Bros. Ultimate (recomp)** to your application menu; the game's
  output of the last start is then in `~/.local/share/ssbu-recomp/last_run.log`. `--remove` takes it out again.
- **First start:** the window stays black for a minute or two while the game reads its shaders out of `data.arc`. It
  then plays while background threads prepare every shader for your GPU, which takes about 4 minutes. A match started
  during those minutes can stutter. After that the cache in `build-linux/cache/` is used, and a whole match had no
  stalls in the tests. A driver update starts the preparation once more.
- **On a new save**, after the title screen a "Milestones" banner waits for a few presses of A.
- Close the window to quit.

### Controls

Controllers: any pad SDL3 knows (Xbox, PlayStation, Switch Pro / Joy-Con, 8BitDo, GameCube adapters, ...), up to 8
players, hot-plug. The keyboard is one more player:

| Keys | Button |
|---|---|
| Arrow keys | left stick |
| Z  X  C  V | A  B  X  Y (in the positions of a Switch controller) |
| Q  E | L  R |
| A  S | ZL  ZR |
| I  J  K  L | d-pad |
| Enter, Backspace | +  − |

Remapping, player slots, dead zones and rumble: `build-linux/config/controllers.ini`. It is written on the first
start, explains itself, and is re-read within a second of being saved. `build-linux/work/ssbu_native --pad-test` shows
what a pad sends.

Window: `build-linux/config/graphics.ini` (`window = windowed | borderless | fullscreen`, size, vsync). Only
`windowed` was tested on Linux.

## Fonts

The game cannot start without the console's shared system fonts, and those are not part of the game dump. They come
from the console's firmware.

- **With a firmware dump** from your console: `deps/venv/bin/python tools/extract/fw_fonts.py <firmware folder> <dump>/sysdata/fonts`,
  then run setup with the same `--dump`. If you built before, add `--redo convert`.
- **Without one**, setup puts **stand-ins** in `build-linux/sysdata/fonts`: your system's Noto Sans (or DejaVu Sans)
  under the console's file names, with a note file `STAND-IN FONTS.txt`. Menus are readable, but the look differs and
  the button icons the game draws from the console's symbol font are missing. Real fonts replace the stand-ins as
  described above.

## Troubleshooting

- **Something gets killed during `compile` or `nro`** (the desktop freezes, then a program is gone; `journalctl` shows
  `oom`): too many compiler jobs for your RAM. Run again with a lower `--jobs`; finished files are kept. With
  systemd you can also cap the build:
  `systemd-run --user --scope -p MemoryMax=20G deps/venv/bin/python scripts/setup.py --target linux --dump <dump>`.
- **The game exits at once with code 7:** no fonts (see "Fonts"). The log line starts with `nn::pl:`.
- **A black window that never changes:** look at the output for `nvn_vk:` lines. `vulkaninfo --summary` must list your GPU.
- **No sound:** the game plays through SDL3, so PipeWire / PulseAudio must be running. The log says which audio device was opened.
- **To report a problem**, include `~/.local/share/ssbu-recomp/last_run.log` (written by every `./play.sh`). It lists the
  libraries, the GPU and driver, the controllers found, and the error. If no frame was shown for 5 s,
  `build-linux/work/ssbu_native_freeze.txt` has a report of every thread.

## How the port works (for developers)

The runtime is written against the Win32 API. Rather than rewriting it, the Linux build keeps those calls and supplies
them: `runtime/plat/windows.h` and the other headers in `runtime/plat/` declare the subset of Win32 the runtime uses, and
`runtime/plat_posix.c` implements it on POSIX. That covers threads, events, condition variables on futexes,
`VirtualAlloc` with a page map, `GetWriteWatch` on userfaultfd write protection (as Wine does it; kernel 6.7+), files,
`LoadLibrary` (a `.dll` name maps to the `.so`), fibers, waveOut on SDL3 audio, and CryptoAPI on OpenSSL.
`runtime/plat_mf.c` provides the part of Media Foundation the movie player uses, on FFmpeg. Code that really is
Windows-only is under `#ifdef _WIN32`, with Linux branches: the SDL3 window, the Vulkan surface, FreeType glyph
outlines, the time zone from `/etc/localtime`, `/proc` thread reports, and the signal crash handler.

`RECOMP_TARGET=linux` (set by `setup.py --target linux`) switches the build scripts to `-fPIC`, `-fvisibility=hidden`, `.so` outputs, and an
executable that finds its libraries next to itself (`rpath $ORIGIN`). `tests/plat_selftest.c` tests the compatibility
layer by itself:
`clang -I runtime/plat -include runtime/plat/plat_pre.h tests/plat_selftest.c runtime/plat_posix.c $(pkg-config --cflags --libs sdl3 openssl) -lm -o plat_selftest`
(52 checks, 0 failures on the PC above).
