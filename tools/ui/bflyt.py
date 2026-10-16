"""BFLYT layouts (version 9, the game's menus): list the pane tree, move panes, copy a pane, add it to a group.

    python tools/ui/bflyt.py <file.bflyt>         the pane tree with positions and sizes, and the groups

A layout is a header and a list of sections. Panes (pan1, pic1, txt1, wnd1, bnd1, prt1, ...) share a 0x4c-byte
header after the section header: flags, origin, alpha, name[24], user data name[8], translation (3 floats), rotation
(3 floats), scale (2 floats), size (2 floats). pas1 / pae1 open and close a pane's children; grs1 / gre1 do the same
for groups. A part pane (prt1) keeps its property overrides inside its own section with offsets relative to the
section, so a copy of the whole section is a valid new pane.
"""
import struct
import sys

PANES = ('pan1', 'pic1', 'txt1', 'wnd1', 'bnd1', 'prt1', 'scr1', 'ali1')


def _cstr(b, o, n):
    s = b[o:o + n]
    return s[:s.index(b'\0')].decode('latin1') if b'\0' in s else s.decode('latin1')


class Layout:
    def __init__(self, b):
        if b[:4] != b'FLYT':
            raise ValueError('not a BFLYT layout')
        self.head = bytearray(b[:struct.unpack_from('<H', b, 6)[0]])
        n = struct.unpack_from('<H', b, 0x10)[0]
        self.sections, o = [], len(self.head)
        for _ in range(n):
            magic, size = struct.unpack_from('<4sI', b, o)
            self.sections.append([magic.decode(), bytearray(b[o:o + size])])
            o += size

    def bytes(self):
        body = b''.join(bytes(s) for _, s in self.sections)
        head = bytearray(self.head)
        struct.pack_into('<I', head, 0xc, len(head) + len(body))
        struct.pack_into('<H', head, 0x10, len(self.sections))
        return bytes(head) + body

    def find(self, name):
        for i, (m, s) in enumerate(self.sections):
            if m in PANES and _cstr(s, 0xc, 24) == name:
                return i
        raise KeyError('no pane %s' % name)

    def pane(self, name):
        return self.sections[self.find(name)][1]

    def get_pos(self, name):
        return struct.unpack_from('<2f', self.pane(name), 0x2c)

    def set_pos(self, name, x, y):
        struct.pack_into('<2f', self.pane(name), 0x2c, x, y)

    def set_scale(self, name, sx, sy):
        struct.pack_into('<2f', self.pane(name), 0x44, sx, sy)

    def copy_pane(self, src, new):
        """a copy of pane `src` (one section: a pane with children is not supported) named `new`, right after it"""
        i = self.find(src)
        if i + 1 < len(self.sections) and self.sections[i + 1][0] == 'pas1':
            raise ValueError('%s has children' % src)
        if len(new.encode()) > 23:
            raise ValueError('pane name too long: %s' % new)
        s = bytearray(self.sections[i][1])
        s[0xc:0xc + 24] = new.encode().ljust(24, b'\0')
        self.sections.insert(i + 1, [self.sections[i][0], s])

    def group_add(self, group, pane):
        for k, (m, s) in enumerate(self.sections):
            if m == 'grp1' and _cstr(s, 8, 34) == group:
                n = struct.unpack_from('<H', s, 0x2a)[0]
                s += pane.encode().ljust(24, b'\0')
                struct.pack_into('<H', s, 0x2a, n + 1)
                struct.pack_into('<I', s, 4, len(s))
                return
        raise KeyError('no group %s' % group)

    def tree(self):
        out, d = [], 0
        for m, s in self.sections:
            if m in ('pae1', 'gre1'):
                d -= 1
            text = ''
            if m in PANES:
                x, y = struct.unpack_from('<2f', s, 0x2c)
                sx, sy, w, h = struct.unpack_from('<4f', s, 0x44)
                text = '%-24s pos (%.0f, %.0f) scale (%.2f, %.2f) size (%.0f, %.0f) %s' % (
                    _cstr(s, 0xc, 24), x, y, sx, sy, w, h, '' if s[8] & 1 else 'hidden')
            elif m == 'grp1':
                n = struct.unpack_from('<H', s, 0x2a)[0]
                text = '%s: %s' % (_cstr(s, 8, 34), ', '.join(_cstr(s, 0x2c + 24 * k, 24) for k in range(n)))
            out.append('%s%s %s' % ('  ' * d, m, text))
            if m in ('pas1', 'grs1'):
                d += 1
        return out


if __name__ == '__main__':
    b = open(sys.argv[1], 'rb').read()
    lay = Layout(b)
    print('\n'.join(lay.tree()))
    assert lay.bytes() == b, 'write-back differs'
