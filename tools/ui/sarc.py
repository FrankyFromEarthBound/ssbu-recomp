"""SARC archives (the layout.arc files of the game's menus): read and write.

    python tools/ui/sarc.py <file.arc> [<out folder>]      list the files (and extract them)

The format is Nintendo's plain archive: SARC header, SFAT (name hash, name offset, data range per file), SFNT (names),
then the data. read() keeps the order and each file's alignment so that write() of an unchanged archive gives the
same bytes back.
"""
import os
import struct
import sys

HASH_KEY = 0x65


def name_hash(name, key=HASH_KEY):
    h = 0
    for ch in name.encode():
        h = (h * key + ch) & 0xffffffff
    return h


def _alignment(offset):
    a = 0x2000
    while a > 4 and offset % a:
        a >>= 1
    return a


def read(b):
    """-> list of [name, data, alignment] in archive order, and the data-section alignment"""
    if b[:4] != b'SARC':
        raise ValueError('not a SARC archive')
    hl, bom, size, doff, ver = struct.unpack_from('<HHIIH', b, 4)
    o = hl
    if b[o:o + 4] != b'SFAT':
        raise ValueError('no SFAT')
    fh, n, key = struct.unpack_from('<HHI', b, o + 4)
    ents = [struct.unpack_from('<IIII', b, o + fh + 16 * i) for i in range(n)]
    o += fh + 16 * n
    nt = o + 8
    files = []
    for h, attr, s, e in ents:
        if not attr >> 24:
            raise ValueError('a file without a name (hash %08x)' % h)
        no = nt + (attr & 0xffffff) * 4
        name = b[no:b.index(b'\0', no)].decode()
        files.append([name, b[doff + s:doff + e], _alignment(s)])
    return files, _alignment(doff)


def write(files, data_align=0x1000):
    """files: [name, data, alignment]; entries are sorted by name hash, as the format requires"""
    order = sorted(range(len(files)), key=lambda i: name_hash(files[i][0]))
    names, name_off = b'', {}
    for i in order:
        name_off[i] = len(names) // 4
        nb = files[i][0].encode() + b'\0'
        names += nb + b'\0' * (-len(nb) % 4)
    # data in the given (archive) order, each file at its alignment
    data, ranges = bytearray(), {}
    for i, (name, d, al) in enumerate(files):
        data += b'\0' * (-len(data) % al)
        ranges[i] = (len(data), len(data) + len(d))
        data += d
    sfat = struct.pack('<4sHHI', b'SFAT', 12, len(files), HASH_KEY)
    for i in order:
        s, e = ranges[i]
        sfat += struct.pack('<IIII', name_hash(files[i][0]), 0x01000000 | name_off[i], s, e)
    sfnt = struct.pack('<4sHH', b'SFNT', 8, 0) + names
    head_len = 0x14 + len(sfat) + len(sfnt)
    doff = head_len + (-head_len % data_align)
    total = doff + len(data)
    head = struct.pack('<4sHHIIHH', b'SARC', 0x14, 0xfeff, total, doff, 0x100, 0)
    return head + sfat + sfnt + b'\0' * (doff - head_len) + bytes(data)


if __name__ == '__main__':
    b = open(sys.argv[1], 'rb').read()
    files, da = read(b)
    for name, d, al in files:
        print('%-56s %9d  align 0x%x' % (name, len(d), al))
    if len(sys.argv) > 2:
        for name, d, al in files:
            p = os.path.join(sys.argv[2], name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'wb').write(d)
    print('write-back identical:', write(files, da) == b)
