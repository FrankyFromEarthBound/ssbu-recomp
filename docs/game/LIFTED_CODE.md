# Reading the lifted code

The lifter (`tools/lifter/recomp.py`, wrapped by `recomp_eh.py` for C++ exceptions) translates main's AArch64
machine code instruction by instruction into C. The output is `<build>/work/gen_eh_o2/`: 1,159 files `c_XXXX.c`
holding 112,890 functions, plus `funcs.h` (all prototypes), `table.c` (address → function) and `imports.c`
(the dynamic symbols) **[measured]**. The script modules are lifted the same way into `gen_nro_eh_o2/<module>/`.

This page describes the *form* of that C. The examples below are made up to show the form: they are not
lines of the game.

## One function

```c
void f_7100001000(Ctx *c) {
  c->fn = 0x7100001000ULL;                                   /* for crash reports: the function entered last */
L_7100001000: c->x[31] = (uint64_t)(c->x[31] - 32ULL);      /* sub sp, sp, #32 */
L_7100001004: { uint64_t a = c->x[31] + 16ULL; wr64(a, c->x[29]); wr64(a + 8, c->x[30]); }   /* stp x29, x30 */
L_7100001008: c->x[19] = (uint64_t)(0ULL | c->x[0]);         /* mov x19, x0 */
L_710000100c: (void)(adds64(c, c->x[1], ~(0ULL), 1));       /* cmp x1, #0 */
L_7100001010: if (cond(c, 0)) goto L_7100001020;            /* b.eq */
L_7100001014: c->x[30] = 0x7100001018ULL; f_7100002000(c);  /* bl */
L_7100001018: { uint64_t t = c->x[8]; c->x[30] = 0x710000101cULL; call_indirect(c, t); }   /* blr x8 */
L_710000101c: { f_7100003000(c); return; }                  /* b (a tail call) */
L_7100001020: return;                                       /* ret */
}
```

- **One statement per instruction.** Each has a label `L_<address>`, so any branch target exists. Nothing is
  merged or reordered at lift time: clang's optimiser (`-O1` by default, `RECOMP_OPT`) removes what is dead.
- **`Ctx`** (`runtime/runtime.h`) is the whole register file: `x[0..30]`, `x[31]` = SP, `v[0..31]` (128-bit
  SIMD/FP as a union), the flags `N Z C V` as separate words, `tpidr` (thread pointer), the exclusive monitor
  for `LDXR`/`STXR`, and diagnostics (`fn`, `in_import`). Every guest thread and every guest fiber has its own.
- **Memory**: `rd8..rd64`, `wr8..wr64`, `rdv`/`wrv` read and write the real address, because guest memory is
  mapped where the game expects it ([BINARIES.md](BINARIES.md)). The guest stack is guest memory too, at
  `x[31]`.
- **Flags**: `adds64/adds32` (and the subtract forms) compute `NZCV` and `cond(c, n)` tests a condition code.
  That is the main reason the C is long: a compare is a call that sets four fields.
- **Floating point and SIMD**: `neon.h` and `fpexact.h`. Operations whose results must be bit-exact with the
  console (estimates such as `FRECPE`/`FRSQRTE`, rounding modes, NaN propagation) are emulated exactly;
  `-ffp-contract=off` stops clang from fusing a multiply and an add.
- **Calls**:

  | Instruction | Becomes |
  |---|---|
  | `BL target` | `c->x[30] = <return address>; f_<target>(c);` A real C call: the host stack mirrors the guest's call chain. |
  | `BLR xN` / `BR xN` to a function | `call_indirect(c, addr)` (`runtime/hostrt.c`). It looks the address up in `fn_table` (main), the run-time host functions, the import slots, the loaded modules (`nnro.c`) or the lifted SDK. |
  | `B target` outside the function | `{ f_<target>(c); return; }`, a tail call. |
  | `BR` inside the function (switch tables) | a jump through `L_dispatch`, a `switch (jt)` over every label of the function |
  | `RET` | `return;` |
  | `SVC` | `guest_svc`, a trap: the game never calls the kernel directly |

  A word the lifter cannot translate becomes `guest_unimpl(...)`, which stops the game with its address.
  There are 184,580 such statements **[measured]**: 184,131 are the zero words that pad between functions,
  442 are the word `0xe7ffdefe`, which is not an AArch64 instruction (a filler or trap marker
  **[inferred]**), and 7 are data inside the code. None of them is reached in the runs so far.
- **Imports**: a call to a PLT stub becomes a function `f_<stub>` whose body is `host_import(c, <symbol
  index>)`. The runtime binds the symbol index to its handler (`runtime/nn*.c`) or to a module's export.

## Boundaries: what a "function" is

`Env.compute_starts` in `recomp.py` takes as function entries:

- every `BL` target;
- every code address stored in data (relative relocations, which covers vtables and function-pointer
  tables);
- every code address built with `ADRP`+`ADD` or `ADR` (callbacks, thread entry points);
- every exported symbol;
- every function start the exception tables (`.eh_frame`) describe;
- and, found while lifting, every branch target that lies outside the function it was found in.

A function runs from its entry to the next entry. When the code before the next entry does not end in a
return or a jump, the lifted function **falls through** into the next one with `f_<next>(c); return;`.

So a lifted function is usually one source function, but not always:

- A function whose middle is also an entry (an address taken for a callback, a shared tail) is cut in two,
  and the first part falls into the second.
- Padding and data between functions belong to the function before them. That is why sizes are measured
  "to the next entry".
- A source function the compiler inlined everywhere does not exist at all.

The sizes **[measured]**: median 96 bytes (24 instructions), 90th percentile 896, 99th 7,184. 2,074 functions
are 4 KB or more and 56 are 64 KB or more. The largest, `f_71007a9cb0`, is 430,976 bytes (107,744
instructions). It is so large that `c_00a3.c` is compiled at `-O0`: clang runs out of memory optimising it.
`build_opt.py` also compiles functions of more than 20,000 statements without optimisation (`optnone`). In
the `-O2` build of 2026-10-15 that applied to 146 functions in 138 files.

## Exceptions: `u_` functions

The game uses C++ exceptions in its middleware (`boost`, text parsers, `nn::nex`). A function that has
landing pads in the exception tables is lifted twice over (`recomp_eh.py`):

- `void u_<entry>(Ctx *c, uint64_t rp)`: `rp == 0` runs it from the top. `rp == <landing pad>` jumps
  straight to that pad (a `switch (rp)` at the top).
- `void f_<entry>(Ctx *c)` calls `u_<entry>(c, 0)`. A function with a `catch` that can return calls it
  through `nro_catch_call`, which gives the unwinder a host frame to come back to.

`table.c` also lists `resume_table_main`: 6,766 landing pads → their `u_` function. The unwinder in
`runtime/nnunwind.c` / `runtime/nncxxrt.c` reads the game's own exception tables (`.eh_frame`, LSDA) and
resumes the right pad. `runtime/nncxxrt.c` documents which handlers swallow exceptions.

## The files

One file per 48 KB of guest code (`BUCKET = 0xC000` in `recomp.py`), so a function's file follows from its
address: `c_%04x.c % ((address - 0x7100000000) // 0xC000)`. For example `f_7100c01c70` is in `c_0100.c`. The
contents of a file stay the same from one lift to the next unless that address range changes.

## What the C does not have (yet)

- **No types.** Every value is a `uint64_t` in a register or a word in memory. Structures appear only as
  offsets (`rd64(c->x[0] + 0x38)`).
- **No local variables.** Registers stay fields of `Ctx`, which every call receives, so clang must keep them
  in memory across calls. That limits its optimiser. A test of `restrict Ctx *` gave nothing (2026-10-15).
  Holding registers in locals helped at `-O1` but did worse than plain `-O2`.
- **No names** except the 4.3% that are exported and the 5.4% that a vtable of a class with type info
  names ([NAMING_PLAN.md](NAMING_PLAN.md)).
- **No structure**: loops and ifs are gotos between labels.

These four are what "cleaning up" means at the code level. [NAMING_PLAN.md](NAMING_PLAN.md) proposes an order.
