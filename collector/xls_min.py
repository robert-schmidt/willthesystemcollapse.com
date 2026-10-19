"""Minimal stdlib reader for numeric cells of one sheet in a BIFF8 .xls (OLE2 compound file). Strings are skipped."""
import struct


def ole_stream(buf, want=('Workbook', 'Book')):
    ssz = 1 << struct.unpack_from('<H', buf, 30)[0]
    nfat, dir0, _, _, _, _, difat0, ndifat = struct.unpack_from('<8I', buf, 44)
    sec = lambda i: buf[512 + i * ssz: 512 + (i + 1) * ssz]
    difat = list(struct.unpack_from('<109I', buf, 76))
    d = difat0
    for _ in range(ndifat):                      # extra DIFAT sectors (big files)
        s = struct.unpack(f'<{ssz // 4}I', sec(d)); difat += s[:-1]; d = s[-1]
    fat = []
    for i in difat[:nfat]:
        fat += struct.unpack(f'<{ssz // 4}I', sec(i))

    def chain(i):
        out = []
        while i < 0xFFFFFFFA:
            out.append(sec(i)); i = fat[i]
        return b''.join(out)
    dirs = chain(dir0)
    for o in range(0, len(dirs), 128):
        e = dirs[o:o + 128]
        name = e[:struct.unpack_from('<H', e, 64)[0]].decode('utf-16-le').rstrip('\0')
        if name in want:
            start, size = struct.unpack_from('<II', e, 116)
            return chain(start)[:size]               # Workbook stream is >= 4096 bytes, so never in the mini stream
    raise ValueError('no Workbook stream')


def rk(v):
    x = (v >> 2) if v & 2 else struct.unpack('<d', struct.pack('<Q', (v & 0xFFFFFFFC) << 32))[0]
    if v & 2 and x >= 1 << 29:
        x -= 1 << 30
    return x / 100 if v & 1 else x


def sheet(data, name):
    """{(row, col): float} for numeric cells (NUMBER, RK, MULRK, cached FORMULA numbers) of the named sheet."""
    wb, pos, sheets = ole_stream(data), 0, {}
    while pos + 4 <= len(wb):                       # globals: BOUNDSHEET records give each sheet's BOF offset
        t, n = struct.unpack_from('<HH', wb, pos)
        if t == 0x0085:
            off, cch, flag = struct.unpack_from('<I', wb, pos + 4)[0], wb[pos + 10], wb[pos + 11]
            raw = wb[pos + 12: pos + 12 + cch * (2 if flag & 1 else 1)]
            sheets[raw.decode('utf-16-le' if flag & 1 else 'latin-1')] = off
        if t == 0x000A:                             # EOF of globals
            break
        pos += 4 + n
    pos, out = sheets[name], {}
    while pos + 4 <= len(wb):
        t, n = struct.unpack_from('<HH', wb, pos)
        p = pos + 4
        if t == 0x0203:                             # NUMBER
            r, c = struct.unpack_from('<HH', wb, p); out[r, c] = struct.unpack_from('<d', wb, p + 6)[0]
        elif t == 0x027E:                           # RK
            r, c = struct.unpack_from('<HH', wb, p); out[r, c] = rk(struct.unpack_from('<I', wb, p + 6)[0])
        elif t == 0x00BD:                           # MULRK
            r, c0 = struct.unpack_from('<HH', wb, p)
            for j in range((n - 6) // 6):
                out[r, c0 + j] = rk(struct.unpack_from('<I', wb, p + 6 + 6 * j)[0])
        elif t == 0x0006:                           # FORMULA: cached result is a number unless bytes 12-13 are 0xFFFF
            r, c = struct.unpack_from('<HH', wb, p)
            if wb[p + 12: p + 14] != b'\xff\xff':
                out[r, c] = struct.unpack_from('<d', wb, p + 6)[0]
        elif t == 0x000A:
            break
        pos += 4 + n
    return out
