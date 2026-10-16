# The game's code: what we know about it

State: 2026-10-15, first version. These pages collect what is known about the code of *Super Smash Bros.
Ultimate* 13.0.4 as this project sees it: the executable we lift, the C it becomes, the names it carries, and the
parts of the engine our runtime has had to understand. The aim is a map. Later work can then turn the
mechanical translation into something people can read, name and clean up, one region at a time.

What they contain follows [CONTRIBUTING.md](../../CONTRIBUTING.md) rule 1: **names, addresses, sizes, counts and
our own descriptions**. They contain no game code, no lifted or decompiled functions and no data from the
game's files. Anything more detailed is produced on your PC by `tools/audit/game_index.py` from your own dump.

## The pages

| Page | What it answers |
|---|---|
| [BINARIES.md](BINARIES.md) | Which program images there are, where they are loaded, how big they are, which ones we lift |
| [LIFTED_CODE.md](LIFTED_CODE.md) | How to read `gen_eh_o2/c_*.c`: function boundaries, `Ctx`, labels, flags, calls, imports, exceptions |
| [CODE_MAP.md](CODE_MAP.md) | What lives where in main's 58 MB of code, region by region, and how much of it has a name |
| [SUBSYSTEMS.md](SUBSYSTEMS.md) | The engine's parts (threads, jobs, resources, scripts, fighters, audio, movies, rendering, network) with the addresses that anchor each |
| [NAMING_PLAN.md](NAMING_PLAN.md) | Where more names can come from, and a proposed order for cleaning up |
| [fighters/](fighters/README.md) | One page per fighter: where its code is and how its moves are built (first: Mr. Game & Watch) |
| [KNOWN_ADDRESSES.md](KNOWN_ADDRESSES.md) | Generated: every game address our own sources mention, with a link to each place |

## How a claim is marked

Every fact here comes from one of these. The page says which:

- **[symbol]**: the game's own exported or imported name (dynamic symbol table of `main`), or a C++ class name
  from its run-time type information. This is certain.
- **[measured]**: counted by `tools/audit/game_index.py` or seen in a run of the port (the run log is named).
  It is certain for 13.0.4 as lifted here.
- **[runtime]**: established while writing the runtime. The linked source file explains how, usually with a
  test.
- **[inferred]**: our reading of the code or of a name. It is plausible but not tested. Treat it as a lead,
  not a fact.
- **[open]**: not known yet. These are the questions worth working on.

## Conventions

- Addresses are where the runtime maps them. `main` starts at `0x7100000000`, so an "offset" of `0x3810a40`
  in other tools is `0x7103810a40` here. The script modules (NROs) have their own bases (see
  [BINARIES.md](BINARIES.md)).
- `f_<address>` is the lifted C function whose entry is that address. `u_<address>` is the same function's
  resumable form for C++ exceptions ([LIFTED_CODE.md](LIFTED_CODE.md)).
- "Function" means a lifted function: from one entry point to the next. That is not always one function of
  the original source ([LIFTED_CODE.md](LIFTED_CODE.md), *Boundaries*).
- In the runtime's comments, `[game] 0x...` is an address in `main`, `[sdk] 0x...` is an offset in the `sdk`
  image, and `[subsdk]` is an offset in `subsdk0`.

## Making the index on your PC

```
deps/venv/bin/python tools/audit/game_index.py            # build-linux, else build
deps/venv/bin/python tools/audit/game_index.py --docs     # also regenerate KNOWN_ADDRESSES.md
```

It needs a finished `scripts/setup.py` build (it reads `binaries/13.0.4/main.elf` and `work/gen_eh_o2/`) and
writes into `<build>/game_index/`, which is never committed:

| File | Rows |
|---|---|
| `functions.tsv` | every lifted function: address, size, source file, name (exported or vtable class) |
| `symbols.tsv` | every dynamic symbol of main, demangled, defined or imported |
| `vtables.tsv` | every vtable of a class with type info: class, vtable, slot, function |
| `refs.tsv` | every game address this repository mentions, with file, line and the line's text |
| `modules.tsv` | the `lua_bind` wrappers that forward to a module's virtual method: module, accessor offset, vtable slot, method name |
| `summary.md` | the counts these pages quote |

To find a function: `grep 7103987890 build-linux/game_index/functions.tsv` gives its size, its file in
`gen_eh_o2/` and its name if it has one. Then `grep -n "void f_7103987890" build-linux/work/gen_eh_o2/<file>`.
