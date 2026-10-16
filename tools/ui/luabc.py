"""Lua 5.3 bytecode (.lc) of the game's menu scripts: read, change, write back.

The menus' scripts in data.arc (ui/script/**.lc) are ordinary Lua 5.3 chunks with one difference in the header:
lua_Number is a 32-bit float. Stock luac cannot read them; a luac built with -DLUA_FLOAT_TYPE=1 can. This module
reads a chunk into Python objects, lets a patch insert instructions and constants, and writes it back in the same
format. Nothing of the game is in here: the patches that use it say what they add (tools/ui/recomp_menu.py).

    python tools/ui/luabc.py <file.lc>        list the functions, constants and instructions (as luac -l -l)
"""
import struct
import sys

SIGNATURE = b'\x1bLuaS\x00\x19\x93\r\n\x1a\n'

OPNAMES = ('MOVE LOADK LOADKX LOADBOOL LOADNIL GETUPVAL GETTABUP GETTABLE SETTABUP SETUPVAL SETTABLE NEWTABLE SELF '
           'ADD SUB MUL MOD POW DIV IDIV BAND BOR BXOR SHL SHR UNM BNOT NOT LEN CONCAT JMP EQ LT LE TEST TESTSET CALL '
           'TAILCALL RETURN FORLOOP FORPREP TFORCALL TFORLOOP SETLIST CLOSURE VARARG EXTRAARG').split()
OP = {n: i for i, n in enumerate(OPNAMES)}
BX_OPS = {OP['LOADK'], OP['CLOSURE']}
SBX_OPS = {OP['JMP'], OP['FORLOOP'], OP['FORPREP'], OP['TFORLOOP']}
RK = 256                                    # ISK: an RK operand >= 256 is constant (operand - 256)

T_NIL, T_BOOL, T_FLOAT, T_STR, T_INT, T_LSTR = 0, 1, 3, 4, 0x13, 0x14


class Float(float):
    """a lua_Number constant (kept apart from integers, which Python would otherwise merge)"""


def ABC(op, a, b=0, c=0):
    return OP[op] | a << 6 | c << 14 | b << 23


def ABx(op, a, bx):
    return OP[op] | a << 6 | bx << 14


def AsBx(op, a, sbx):
    return OP[op] | a << 6 | (sbx + 131071) << 14


def decode(i):
    op = i & 63
    a, c, b, bx = i >> 6 & 255, i >> 14 & 511, i >> 23 & 511, i >> 14
    return op, a, b, c, bx, bx - 131071


class Proto:
    def __init__(self):
        self.source = None
        self.line = self.lastline = 0
        self.numparams = self.is_vararg = self.maxstack = 0
        self.code, self.consts, self.upvals, self.protos = [], [], [], []
        self.lineinfo, self.locvars, self.upnames = [], [], []

    def const(self, v):
        """index of constant v (added when missing); str, int or Float"""
        for i, k in enumerate(self.consts):
            if type(k) is type(v) and k == v:
                return i
        self.consts.append(v)
        return len(self.consts) - 1

    def insert(self, at, instrs):
        """insert instructions before index `at`; jumps are corrected. A jump to `at` itself lands on the first
        inserted instruction (the new code runs on every path that reached the old instruction)."""
        n = len(instrs)
        for pc, ins in enumerate(self.code):
            op, a, b, c, bx, sbx = decode(ins)
            if op in SBX_OPS:
                tgt = pc + 1 + sbx
                if pc < at < tgt:
                    sbx += n
                elif tgt <= at <= pc:
                    sbx -= n
                else:
                    continue
                self.code[pc] = op | a << 6 | (sbx + 131071) << 14
        self.code[at:at] = instrs
        if self.lineinfo:
            self.lineinfo[at:at] = [self.lineinfo[max(at - 1, 0)]] * n


class Reader:
    def __init__(self, b):
        self.b, self.o = b, 0

    def take(self, n):
        s = self.b[self.o:self.o + n]
        self.o += n
        return s

    def u8(self):
        return self.take(1)[0]

    def i32(self):
        return struct.unpack('<i', self.take(4))[0]

    def string(self):
        n = self.u8()
        if n == 0xff:
            n = struct.unpack('<Q', self.take(8))[0]
        return None if n == 0 else self.take(n - 1)

    def proto(self, parent_source):
        p = Proto()
        p.source = self.string()
        p.line, p.lastline = self.i32(), self.i32()
        p.numparams, p.is_vararg, p.maxstack = self.u8(), self.u8(), self.u8()
        p.code = list(struct.unpack('<%dI' % (n := self.i32()), self.take(4 * n)))
        for _ in range(self.i32()):
            t = self.u8()
            if t == T_NIL:
                p.consts.append(None)
            elif t == T_BOOL:
                p.consts.append(bool(self.u8()))
            elif t == T_FLOAT:
                p.consts.append(Float(struct.unpack('<f', self.take(4))[0]))
            elif t == T_INT:
                p.consts.append(struct.unpack('<q', self.take(8))[0])
            elif t in (T_STR, T_LSTR):
                p.consts.append(self.string().decode('latin1'))
            else:
                raise ValueError('constant type %d' % t)
        p.upvals = [(self.u8(), self.u8()) for _ in range(self.i32())]
        p.protos = [self.proto(p.source) for _ in range(self.i32())]
        p.lineinfo = [self.i32() for _ in range(self.i32())]
        p.locvars = [(self.string(), self.i32(), self.i32()) for _ in range(self.i32())]
        p.upnames = [self.string() for _ in range(self.i32())]
        return p


def load(b):
    """-> (header bytes, number of upvalues of the main function, main Proto)"""
    if not b.startswith(SIGNATURE):
        raise ValueError('not a Lua 5.3 chunk')
    sizes = b[len(SIGNATURE):len(SIGNATURE) + 5]
    if tuple(sizes) != (4, 8, 4, 8, 4):
        raise ValueError('unexpected sizes %r (int, size_t, Instruction, lua_Integer, lua_Number)' % (tuple(sizes),))
    hl = len(SIGNATURE) + 5 + 8 + 4             # + LUAC_INT, LUAC_NUM
    r = Reader(b)
    r.o = hl
    nup = r.u8()
    main = r.proto(None)
    if r.o != len(b):
        raise ValueError('%d bytes after the main function' % (len(b) - r.o))
    return b[:hl], nup, main


def _string(out, s):
    if s is None:
        out.append(b'\0')
        return
    if isinstance(s, str):
        s = s.encode('latin1')
    n = len(s) + 1
    out.append(bytes([n]) if n < 0xff else b'\xff' + struct.pack('<Q', n))
    out.append(s)


def _proto(out, p):
    _string(out, p.source)
    out.append(struct.pack('<iiBBB', p.line, p.lastline, p.numparams, p.is_vararg, p.maxstack))
    out.append(struct.pack('<i%dI' % len(p.code), len(p.code), *p.code))
    out.append(struct.pack('<i', len(p.consts)))
    for k in p.consts:
        if k is None:
            out.append(bytes([T_NIL]))
        elif isinstance(k, bool):
            out.append(bytes([T_BOOL, int(k)]))
        elif isinstance(k, Float):
            out.append(bytes([T_FLOAT]) + struct.pack('<f', k))
        elif isinstance(k, int):
            out.append(bytes([T_INT]) + struct.pack('<q', k))
        else:
            out.append(bytes([T_STR if len(k) <= 40 else T_LSTR]))
            _string(out, k)
    out.append(struct.pack('<i', len(p.upvals)))
    for instack, idx in p.upvals:
        out.append(bytes([instack, idx]))
    out.append(struct.pack('<i', len(p.protos)))
    for q in p.protos:
        _proto(out, q)
    out.append(struct.pack('<i', len(p.lineinfo)) + b''.join(struct.pack('<i', x) for x in p.lineinfo))
    out.append(struct.pack('<i', len(p.locvars)))
    for name, a, b in p.locvars:
        _string(out, name)
        out.append(struct.pack('<ii', a, b))
    out.append(struct.pack('<i', len(p.upnames)))
    for name in p.upnames:
        _string(out, name)


def dump(header, nup, main):
    out = [header, bytes([nup])]
    _proto(out, main)
    return b''.join(out)


def listing(p, path='main', out=None):
    out = [] if out is None else out
    out.append('function %s: %d params, %d slots, %d upvalues, %d constants, %d functions'
               % (path, p.numparams, p.maxstack, len(p.upvals), len(p.consts), len(p.protos)))
    for pc, ins in enumerate(p.code):
        op, a, b, c, bx, sbx = decode(ins)
        name = OPNAMES[op] if op < len(OPNAMES) else '?%d' % op
        if op in BX_OPS:
            args = '%d %d' % (a, bx)
            note = repr(p.consts[bx]) if op == OP['LOADK'] else ''
        elif op in SBX_OPS:
            args, note = '%d %d' % (a, sbx), 'to %d' % (pc + 1 + sbx)
        else:
            args = '%d %d %d' % (a, b, c)
            note = ' '.join(repr(p.consts[x - RK]) for x in (b, c) if x >= RK and x - RK < len(p.consts))
        out.append('  %4d  %-9s %-12s %s' % (pc, name, args, ('; ' + note) if note else ''))
    for i, k in enumerate(p.consts):
        out.append('  K%-3d %r' % (i, k))
    for i, q in enumerate(p.protos):
        listing(q, '%s.%d' % (path, i), out)
    return out


if __name__ == '__main__':
    h, n, m = load(open(sys.argv[1], 'rb').read())
    print('\n'.join(listing(m)))
    assert dump(h, n, m) == open(sys.argv[1], 'rb').read(), 'write-back differs'
