"""What is known about the game's code, collected from your own build into an index (docs/game/README.md).

    python tools/audit/game_index.py [--build build-linux] [--docs]

Reads <build>/binaries/13.0.4/main.elf (made by scripts/setup.py from your dump), the lifted sources in
<build>/work/gen_eh_o2 and this repository's own sources. Writes, into <build>/game_index/ (never committed):

    functions.tsv   every lifted function: address, size in bytes (to the next function), source file, name if any
    symbols.tsv     the names main exports and imports (dynamic symbols), demangled
    vtables.tsv     classes with run-time type information: class, vtable address, slot, function address
    refs.tsv        every game address this repository's sources mention, and where
    modules.tsv     app::lua_bind wrappers that only forward to a module: module, its offset in the
                    BattleObjectModuleAccessor, the vtable slot, the wrapper (names the module's virtual methods)
    summary.md      the counts quoted in docs/game/

--docs also rewrites docs/game/KNOWN_ADDRESSES.md: the addresses OUR sources name (runtime, tools, mods, tests) with
the file and line that mention them and the exported name, if the game has one. It contains names and addresses
only (CONTRIBUTING.md rule 1).
"""
import argparse
import bisect
import collections
import os
import re
import struct
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = 0x7100000000                       # main's load address in the runtime (recomp.py)
FILE_OFF = 0x1000                         # nso2elf: file offset of address 0
R_AARCH64_ABS64, R_AARCH64_GLOB_DAT, R_AARCH64_JUMP_SLOT, R_AARCH64_RELATIVE = 257, 1025, 1026, 1027
SOURCE_DIRS = ('runtime', 'fighter_sdk', 'tools', 'tests', 'mods', 'docs', 'scripts')
ADDR_RE = re.compile(r'\b(?:0x|f_)(71[0-9a-fA-F]{8})\b')


class Elf:
    def __init__(self, path):
        with open(path, 'rb') as f:
            self.d = f.read()
        d = self.d
        shoff, = struct.unpack_from('<Q', d, 0x28)
        shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x3a)
        secs = [struct.unpack_from('<IIQQQQIIQQ', d, shoff + i * shentsize) for i in range(shnum)]
        names = secs[shstrndx][4]
        self.sections = {}
        for s in secs:
            end = d.index(b'\0', names + s[0])
            self.sections[d[names + s[0]:end].decode()] = {'addr': s[3], 'off': s[4], 'size': s[5], 'entsize': s[9]}

    def cstr(self, addr, limit=256):
        o = addr + FILE_OFF
        if o < 0 or o >= len(self.d):
            return None
        end = self.d.find(b'\0', o, o + limit)
        return self.d[o:end] if end > o else None

    def u64(self, addr):
        return struct.unpack_from('<Q', self.d, addr + FILE_OFF)[0]

    def dynsyms(self):
        sy, st = self.sections['.dynsym'], self.sections['.dynstr']
        out = []
        for i in range(sy['size'] // 24):
            name, info, other, shndx, value, size = struct.unpack_from('<IBBHQQ', self.d, sy['off'] + i * 24)
            if not name:
                continue
            end = self.d.index(b'\0', st['off'] + name)
            out.append({'name': self.d[st['off'] + name:end].decode('latin-1'), 'type': info & 15, 'defined': shndx != 0,
                        'value': value, 'size': size, 'index': i})
        return out

    def relocs(self):
        """{place: (type, symbol index, addend)} from DT_RELA and DT_JMPREL."""
        dy = self.sections['.dynamic']
        tags = {}
        for i in range(dy['size'] // 16):
            tag, val = struct.unpack_from('<qQ', self.d, dy['off'] + i * 16)
            if tag == 0:
                break
            tags[tag] = val
        out = {}
        for start, size in ((tags.get(7), tags.get(8)), (tags.get(23), tags.get(2))):
            if start is None or not size:
                continue
            for i in range(size // 24):
                place, info, addend = struct.unpack_from('<QQq', self.d, start + FILE_OFF + i * 24)
                out[place] = (info & 0xffffffff, info >> 32, addend)
        return out


def demangle(names):
    try:
        r = subprocess.run(['c++filt'], input='\n'.join(names), capture_output=True, text=True, check=True)
        out = r.stdout.split('\n')
        if len(out) >= len(names):
            return out[:len(names)]
    except (OSError, subprocess.CalledProcessError):
        pass
    return list(names)


def lifted_functions(gen):
    """[(address, source file)] of the lifted functions, from the generated sources."""
    out = []
    fre = re.compile(r'^void f_([0-9a-f]+)\(Ctx \*c\) \{', re.M)
    for n in sorted(os.listdir(gen)):
        if not re.match(r'c_[0-9a-f]+\.c$', n):
            continue
        with open(os.path.join(gen, n), encoding='latin-1') as f:
            for m in fre.finditer(f.read()):
                out.append((int(m.group(1), 16), n))
    out.sort()
    return out


def rtti_vtables(elf, rel, text_end):
    """Classes that have run-time type information: [(class, vtable, [function addresses])]. A type-info object is
    {vtable of its kind, pointer to the mangled name}; a vtable is {offset to top, pointer to the type info, functions}."""
    names = {}                                   # address of a type-info object -> mangled name
    for place, (t, _, addend) in rel.items():
        if t != R_AARCH64_RELATIVE:
            continue
        s = elf.cstr(addend, 200)
        if s and re.fullmatch(rb'N?[0-9]+[A-Za-z_][A-Za-z0-9_]*(?:[0-9]+[A-Za-z_][A-Za-z0-9_]*)*E?', s) and (place - 8) in rel:
            names[place - 8] = s.decode()
    out = []
    for place, (t, _, addend) in rel.items():
        if t == R_AARCH64_RELATIVE and addend in names:
            v = place - 8                            # the vtable's address point is place + 8
            if v in rel or v + FILE_OFF >= len(elf.d) or elf.u64(v) > 0x100000 and elf.u64(v) < (1 << 63):
                continue                             # slot 0 must be an offset to top: no pointer, small or negative
            fns, p = [], place + 8
            while p in rel and rel[p][0] == R_AARCH64_RELATIVE and 0 <= rel[p][2] < text_end:
                fns.append(rel[p][2])
                p += 8
            if fns:
                out.append((names[addend], v, fns))
    return out


def module_methods(elf, funcs):
    """lua_bind wrappers of the form  ldr x0, [x0, #m]; ldr x8, [x0]; ldr xN, [x8, #s]; br xN : (name, m, s, address)."""
    out = []
    for x, name in funcs:
        if not name.startswith('app::lua_bind::'):
            continue
        w = [struct.unpack_from('<I', elf.d, x - BASE + FILE_OFF + k)[0] for k in (0, 4, 8, 12)]
        if (w[0] & 0xffc003ff) == 0xf9400000 and w[1] == 0xf9400008 and (w[2] & 0xffc003e0) == 0xf9400100 and \
                (w[3] & 0xfffffc1f) == 0xd61f0000 and (w[2] & 31) == (w[3] >> 5) & 31:
            out.append((name, ((w[0] >> 10) & 0xfff) * 8, ((w[2] >> 10) & 0xfff) * 8, x))
    return out


def source_refs():
    """{address: [(file, line, text)]}: the game addresses this repository's own sources mention."""
    out = collections.defaultdict(list)
    files = subprocess.run(['git', 'ls-files'] + list(SOURCE_DIRS), cwd=REPO, capture_output=True, text=True).stdout.split()
    for rel in files:
        if rel.endswith(('.png', '.bin')) or rel.startswith('docs/game/'):
            continue
        try:
            with open(os.path.join(REPO, rel), encoding='utf-8', errors='replace') as f:
                for ln, line in enumerate(f, 1):
                    for m in ADDR_RE.finditer(line):
                        out[int(m.group(1), 16)].append((rel, ln, line.strip()))
        except OSError:
            pass
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', help='build folder (default build-linux if it exists, else build)')
    ap.add_argument('--docs', action='store_true', help='also rewrite docs/game/KNOWN_ADDRESSES.md')
    a = ap.parse_args()
    build = a.build or next((os.path.join(REPO, b) for b in ('build-linux', 'build') if os.path.isdir(os.path.join(REPO, b, 'work', 'gen_eh_o2'))), None)
    if not build:
        sys.exit('no build with lifted sources (build*/work/gen_eh_o2): run scripts/setup.py first')
    elf = Elf(os.path.join(build, 'binaries', '13.0.4', 'main.elf'))
    out = os.path.join(build, 'game_index')
    os.makedirs(out, exist_ok=True)
    text = elf.sections['.text']
    text_end = text['addr'] + text['size']

    syms = elf.dynsyms()
    dem = demangle([s['name'] for s in syms])
    for s, d in zip(syms, dem):
        s['demangled'] = d
    named = {BASE + s['value']: s['demangled'] for s in syms if s['defined'] and s['type'] == 2}
    with open(os.path.join(out, 'symbols.tsv'), 'w') as f:
        f.write('address\tsize\tkind\tdefined\tname\n')
        for s in sorted(syms, key=lambda s: (not s['defined'], s['value'], s['name'])):
            f.write('%s\t%d\t%s\t%s\t%s\n' % ('%x' % (BASE + s['value']) if s['defined'] else '-', s['size'],
                                             {1: 'object', 2: 'function'}.get(s['type'], str(s['type'])), 'yes' if s['defined'] else 'import', s['demangled']))

    rel = elf.relocs()
    vt = rtti_vtables(elf, rel, text_end)
    vt_dem = demangle(['_ZTS' + n for n in (c for c, _, _ in vt)])
    vfn = {}
    with open(os.path.join(out, 'vtables.tsv'), 'w') as f:
        f.write('class\tvtable\tslot\tfunction\n')
        for (c, v, fns), d in sorted(zip(vt, vt_dem), key=lambda x: x[0][1]):
            cls = d.replace('typeinfo name for ', '')
            for i, fn in enumerate(fns):
                f.write('%s\t%x\t%d\t%x\n' % (cls, BASE + v, i, BASE + fn))
                vfn.setdefault(BASE + fn, cls)

    gen = os.path.join(build, 'work', 'gen_eh_o2')
    funcs = lifted_functions(gen)
    addrs = [x for x, _ in funcs]
    with open(os.path.join(out, 'functions.tsv'), 'w') as f:
        f.write('address\tsize\tfile\tname\n')
        for i, (x, src) in enumerate(funcs):
            size = (addrs[i + 1] if i + 1 < len(addrs) else BASE + text_end) - x
            name = named.get(x) or ('(virtual function of %s)' % vfn[x] if x in vfn else '')
            f.write('%x\t%d\t%s\t%s\n' % (x, size, src, name))

    mm = module_methods(elf, sorted(named.items()))
    with open(os.path.join(out, 'modules.tsv'), 'w') as f:
        f.write('module\taccessor_offset\tvtable_slot\tmethod (lua_bind name)\twrapper\n')
        for name, m, slot, x in sorted(mm, key=lambda t: (t[1], t[2])):
            short = re.sub(r'\(.*', '', name).replace('app::lua_bind::', '')
            mod, _, meth = short.partition('__')
            f.write('%s\t0x%x\t%d\t%s\t%x\n' % (mod, m, slot // 8, re.sub(r'_impl$', '', meth), x))

    refs = source_refs()
    with open(os.path.join(out, 'refs.tsv'), 'w') as f:
        f.write('address\tfile\tline\ttext\n')
        for x in sorted(refs):
            for r, ln, t in refs[x]:
                f.write('%x\t%s\t%d\t%s\n' % (x, r, ln, t.replace('\t', ' ')[:300]))

    # ---- summary
    sizes = sorted((addrs[i + 1] if i + 1 < len(addrs) else BASE + text_end) - x for i, x in enumerate(addrs))
    def pct(p):
        return sizes[min(len(sizes) - 1, int(len(sizes) * p))]
    ns = collections.Counter()
    for s in syms:
        if s['defined'] and s['type'] == 2:
            n = re.sub(r'[(<].*', '', s['demangled']).split('::')
            ns['::'.join(n[:2]) if len(n) > 2 else n[0]] += 1
    imp = collections.Counter()
    for s in syms:
        if not s['defined']:
            n = re.sub(r'[(<].*', '', s['demangled']).split('::')
            imp['::'.join(n[:2]) if len(n) > 2 else (n[0] if len(n) == 2 else '(C) ' + re.sub(r'[A-Z].*', '', n[0]))] += 1
    classes = collections.Counter()
    for c in vt_dem:
        n = c.replace('typeinfo name for ', '').split('::')
        classes['::'.join(n[:2]) if n[0] in ('nn', 'app', 'lib') and len(n) > 2 else n[0]] += 1
    named_lifted = sum(1 for x in addrs if x in named)
    virt_lifted = sum(1 for x in addrs if x not in named and x in vfn)
    with open(os.path.join(out, 'summary.md'), 'w') as f:
        w = f.write
        w('# Game index summary (%s)\n\n' % os.path.relpath(build, REPO))
        w('| | |\n|---|---|\n')
        for k in ('.text', '.rodata', '.data', '.bss'):
            w('| main %s | 0x%x bytes at 0x%x |\n' % (k, elf.sections[k]['size'], BASE + elf.sections[k]['addr']))
        w('| lifted functions | %d in %d files |\n' % (len(addrs), len(set(s for _, s in funcs))))
        w('| function size (bytes) | median %d, 90%% %d, 99%% %d, largest %d |\n' % (pct(0.5), pct(0.9), pct(0.99), sizes[-1]))
        w('| functions with an exported name | %d (%.1f%%) |\n' % (named_lifted, 100.0 * named_lifted / len(addrs)))
        w('| more functions named by a vtable with type info | %d (%.1f%%) |\n' % (virt_lifted, 100.0 * virt_lifted / len(addrs)))
        w('| dynamic symbols | %d defined (%d functions), %d imported |\n' % (sum(s['defined'] for s in syms), len(named), sum(not s['defined'] for s in syms)))
        w('| classes with type info and a vtable | %d |\n' % len(vt))
        w('| lua_bind wrappers that only forward to a module method | %d of %d, %d modules |\n' % (
            len(mm), sum(1 for n in named.values() if n.startswith('app::lua_bind::')), len(set(m for _, m, _, _ in mm))))
        w('| game addresses named by this repository | %d |\n\n' % len(refs))
        for title, c, k in (('Exported functions by namespace', ns, 30), ('Imports by namespace', imp, 30), ('Classes with type info by namespace', classes, 20)):
            w('## %s\n\n| count | namespace |\n|---:|---|\n' % title)
            for name, v in c.most_common(k):
                w('| %d | `%s` |\n' % (v, name))
            w('\n')
    print(open(os.path.join(out, 'summary.md')).read())
    print('index written to %s' % out)

    if a.docs:
        write_known(refs, named, vfn, addrs, BASE + text_end)


def write_known(refs, named, vfn, addrs, code_end):
    path = os.path.join(REPO, 'docs', 'game', 'KNOWN_ADDRESSES.md')
    lines = ['# Game addresses named by this repository', '',
             'Generated by `python tools/audit/game_index.py --docs`; do not edit by hand. Every address in the game\'s main',
             'executable (loaded at 0x7100000000) that this repository\'s own sources mention, and where. "Name" is the game\'s',
             'exported symbol, or the class whose vtable holds the function, when either exists. An address inside a lifted',
             'function (a call site, a patched instruction) names that function. The meaning is in the linked sources: this list',
             'only says where to look. Addresses are of 13.0.4.', '',
             '| address | name | mentioned in |', '|---|---|---|']
    for x in sorted(refs):
        if x == BASE:
            continue
        i = bisect.bisect_right(addrs, x) - 1
        inside = ' (data)' if x >= code_end else '' if i < 0 or addrs[i] == x else ' (in f_%x)' % addrs[i]
        name = named.get(x) or ('virtual function of %s' % vfn[x] if x in vfn else '')
        where = ', '.join(sorted({'[%s:%d](../../%s#L%d)' % (r, ln, r, ln) for r, ln, _ in refs[x][:6]}))
        if len(refs[x]) > 6:
            where += ' and %d more' % (len(refs[x]) - 6)
        lines.append('| `%x`%s | %s | %s |' % (x, inside, ('`%s`' % name.replace('|', '\\|')) if name else '', where))
    lines.append('')
    with open(path, 'w') as f:
        f.write('\n'.join(lines))
    print('wrote', path)


if __name__ == '__main__':
    main()
