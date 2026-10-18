"""Minimal, defensive, stdlib-only reader for legacy .xls (BIFF8 in an OLE2 compound file), for untrusted workbooks.

    book = read_xls(path)  ->  {sheet name: {(row, col): value}}   (1-based; numbers are floats, text is str)

Reads cell values only (NUMBER, RK, MULRK, LABELSST, LABEL and the cached result of formulas that are numbers).
No macros, no OLE objects, nothing is executed. Limits: MAX_STREAM bytes for the workbook stream, MAX_CELLS per sheet.
"""
from __future__ import annotations

import struct
from pathlib import Path

MAX_STREAM = 100 * 1024 * 1024
MAX_CELLS = 2_000_000
FREESECT, ENDOFCHAIN = 0xFFFFFFFF, 0xFFFFFFFE


def _ole_stream(data: bytes, want: tuple[str, ...] = ("Workbook", "Book")) -> bytes:
    if data[:8] != b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1":
        raise ValueError("not an OLE2 compound file")
    shift = struct.unpack_from("<H", data, 30)[0]
    mini_shift = struct.unpack_from("<H", data, 32)[0]
    if shift not in (9, 12):
        raise ValueError("unsupported sector size")
    ssz = 1 << shift
    n_fat, dir_start, _, mini_cutoff, minifat_start, n_minifat, difat_start, n_difat = struct.unpack_from("<IIIIIIII", data, 44)
    difat = list(struct.unpack_from("<109I", data, 76))
    sec = difat_start
    for _ in range(n_difat):
        if sec in (FREESECT, ENDOFCHAIN):
            break
        off = (sec + 1) * ssz
        vals = struct.unpack_from(f"<{ssz // 4}I", data, off)
        difat.extend(vals[:-1])
        sec = vals[-1]
    fat: list[int] = []
    for s in difat[:n_fat]:
        if s in (FREESECT, ENDOFCHAIN):
            continue
        off = (s + 1) * ssz
        fat.extend(struct.unpack_from(f"<{ssz // 4}I", data, off))

    def chain(start: int) -> bytes:
        out, seen, s = [], 0, start
        while s not in (ENDOFCHAIN, FREESECT):
            seen += 1
            if seen > len(fat) + 1 or s >= len(fat):
                raise ValueError("corrupt FAT chain")
            off = (s + 1) * ssz
            out.append(data[off:off + ssz])
            s = fat[s]
            if sum(len(x) for x in out) > MAX_STREAM:
                raise ValueError("stream too large")
        return b"".join(out)

    directory = chain(dir_start)
    entries = []
    for i in range(0, len(directory), 128):
        e = directory[i:i + 128]
        if len(e) < 128:
            break
        nlen = struct.unpack_from("<H", e, 64)[0]
        name = e[:max(nlen - 2, 0)].decode("utf-16le", "replace")
        typ = e[66]
        start = struct.unpack_from("<I", e, 116)[0]
        size = struct.unpack_from("<I", e, 120)[0]
        entries.append((name, typ, start, size))
    root = next((e for e in entries if e[1] == 5), None)
    target = next((e for e in entries if e[0] in want and e[1] == 2), None)
    if target is None:
        raise ValueError("no Workbook stream")
    name, _, start, size = target
    if size >= mini_cutoff:
        return chain(start)[:size]
    # mini stream
    mini_ssz = 1 << mini_shift
    mini_container = chain(root[2])
    minifat_raw = chain(minifat_start)
    minifat = list(struct.unpack(f"<{len(minifat_raw) // 4}I", minifat_raw))
    out, s = [], start
    for _ in range(len(minifat) + 1):
        if s in (ENDOFCHAIN, FREESECT):
            break
        out.append(mini_container[s * mini_ssz:(s + 1) * mini_ssz])
        s = minifat[s]
    return b"".join(out)[:size]


def _records(stream: bytes):
    pos, n = 0, len(stream)
    while pos + 4 <= n:
        rid, ln = struct.unpack_from("<HH", stream, pos)
        yield pos, rid, stream[pos + 4:pos + 4 + ln]
        pos += 4 + ln


def _rk(v: int) -> float:
    if v & 2:
        x = float(struct.unpack("<i", struct.pack("<I", v & 0xFFFFFFFC))[0] >> 2)   # signed 30-bit integer
    else:
        x = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0]
    return x / 100.0 if v & 1 else x


def _read_sst(chunks: list[bytes]) -> list[str]:
    """chunks: the SST record body followed by its CONTINUE bodies. Strings may span chunk boundaries; each continuation
    of a split string starts with a one-byte flag (bit 0: UTF-16)."""
    ci, pos = 0, 8
    cur = chunks[0]
    total_unique = struct.unpack_from("<I", cur, 4)[0]

    def take(n: int) -> bytes:
        nonlocal ci, pos, cur
        if n == 0:
            return b""
        out = b""
        while n > 0:
            if pos >= len(cur):
                ci += 1
                if ci >= len(chunks):
                    raise ValueError("SST truncated")
                cur, pos = chunks[ci], 0
            k = min(n, len(cur) - pos)
            out += cur[pos:pos + k]
            pos += k
            n -= k
        return out

    out: list[str] = []
    for _ in range(total_unique):
        if pos >= len(cur) and ci + 1 >= len(chunks):
            break
        cch = struct.unpack("<H", take(2))[0]
        flags = take(1)[0]
        rich = struct.unpack("<H", take(2))[0] if flags & 0x08 else 0
        ext = struct.unpack("<I", take(4))[0] if flags & 0x04 else 0
        wide = bool(flags & 1)
        chars: list[str] = []
        remaining = cch
        while remaining > 0:
            if pos >= len(cur):          # string continues in the next CONTINUE record: new flag byte
                ci += 1
                if ci >= len(chunks):
                    raise ValueError("SST truncated")
                cur, pos = chunks[ci], 0
                wide = bool(cur[0] & 1)
                pos = 1
            avail = len(cur) - pos
            width = 2 if wide else 1
            k = min(remaining, avail // width)
            raw = cur[pos:pos + k * width]
            pos += k * width
            chars.append(raw.decode("utf-16le" if wide else "latin-1", "replace"))
            remaining -= k
            if k == 0 and pos < len(cur):
                break
        take(4 * rich)
        take(ext)
        out.append("".join(chars))
    return out


def read_xls(path: str | Path) -> dict[str, dict[tuple[int, int], object]]:
    stream = _ole_stream(Path(path).read_bytes())
    recs = list(_records(stream))
    # shared strings: SST plus following CONTINUE records
    sst: list[str] = []
    for i, (_, rid, body) in enumerate(recs):
        if rid == 0x00FC:
            chunks = [body]
            j = i + 1
            while j < len(recs) and recs[j][1] == 0x003C:
                chunks.append(recs[j][2])
                j += 1
            sst = _read_sst(chunks)
            break
    sheets: list[tuple[str, int]] = []
    for _, rid, body in recs:
        if rid == 0x0085:
            off = struct.unpack_from("<I", body, 0)[0]
            cch, flags = body[6], body[7]
            nm = body[8:8 + cch * (2 if flags & 1 else 1)].decode("utf-16le" if flags & 1 else "latin-1", "replace")
            sheets.append((nm, off))
    by_offset = {pos: idx for idx, (pos, _, _) in enumerate(recs)}
    out: dict[str, dict[tuple[int, int], object]] = {}
    for nm, off in sheets:
        if off not in by_offset:
            continue
        cells: dict[tuple[int, int], object] = {}
        for _, rid, body in recs[by_offset[off]:]:
            if rid == 0x0809:
                continue
            if rid == 0x000A:
                break
            if rid == 0x0203 and len(body) >= 14:                      # NUMBER
                r, c = struct.unpack_from("<HH", body, 0)
                cells[(r + 1, c + 1)] = struct.unpack_from("<d", body, 6)[0]
            elif rid == 0x027E and len(body) >= 10:                    # RK
                r, c = struct.unpack_from("<HH", body, 0)
                cells[(r + 1, c + 1)] = _rk(struct.unpack_from("<I", body, 6)[0])
            elif rid == 0x00BD and len(body) >= 6:                     # MULRK
                r, c0 = struct.unpack_from("<HH", body, 0)
                n = (len(body) - 6) // 6
                for k in range(n):
                    cells[(r + 1, c0 + k + 1)] = _rk(struct.unpack_from("<I", body, 4 + 6 * k + 2)[0])
            elif rid == 0x00FD and len(body) >= 10:                    # LABELSST
                r, c = struct.unpack_from("<HH", body, 0)
                idx = struct.unpack_from("<I", body, 6)[0]
                if idx < len(sst):
                    cells[(r + 1, c + 1)] = sst[idx]
            elif rid == 0x0204 and len(body) >= 8:                     # LABEL (BIFF8 uses 16-bit length)
                r, c = struct.unpack_from("<HH", body, 0)
                cch = struct.unpack_from("<H", body, 6)[0]
                flags = body[8] if len(body) > 8 else 0
                raw = body[9:9 + cch * (2 if flags & 1 else 1)]
                cells[(r + 1, c + 1)] = raw.decode("utf-16le" if flags & 1 else "latin-1", "replace")
            elif rid == 0x0006 and len(body) >= 14:                    # FORMULA: cached numeric result
                r, c = struct.unpack_from("<HH", body, 0)
                if body[12:14] != b"\xFF\xFF":
                    cells[(r + 1, c + 1)] = struct.unpack_from("<d", body, 6)[0]
            if len(cells) > MAX_CELLS:
                raise ValueError(f"sheet {nm}: too many cells")
        out[nm] = cells
    return out
