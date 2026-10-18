"""Minimal, defensive, stdlib-only XLSX reader for untrusted workbooks (no formulas evaluated, nothing executed).

    book = read_xlsx(path)  ->  {sheet name: {(row, col): value}}   (row and col are 1-based; numbers are floats, text is str)

Limits: at most MAX_MEMBER bytes per archive member after decompression, MAX_CELLS cells per sheet. Only the sheet XML,
shared strings and workbook relationships are read; macros, external links and embedded objects are ignored.
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_MEMBER = 200 * 1024 * 1024
MAX_CELLS = 2_000_000
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _member(z: zipfile.ZipFile, name: str) -> bytes:
    info = z.getinfo(name)
    if info.file_size > MAX_MEMBER:
        raise ValueError(f"{name}: {info.file_size} bytes uncompressed exceeds the limit")
    with z.open(info) as f:
        data = f.read(MAX_MEMBER + 1)
    if len(data) > MAX_MEMBER:
        raise ValueError(f"{name}: exceeds the limit")
    return data


def col_to_num(ref: str) -> int:
    n = 0
    for ch in ref:
        n = n * 26 + (ord(ch) - 64)
    return n


def _text(si: ET.Element) -> str:
    return "".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t"))


def read_xlsx(path: str | Path) -> dict[str, dict[tuple[int, int], object]]:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(_member(z, "xl/sharedStrings.xml"))
            shared = [_text(si) for si in root.findall("m:si", NS)]
        wb = ET.fromstring(_member(z, "xl/workbook.xml"))
        rels = ET.fromstring(_member(z, "xl/_rels/workbook.xml.rels"))
        target = {r.get("Id"): r.get("Target") for r in rels.findall("pr:Relationship", NS)}
        out: dict[str, dict[tuple[int, int], object]] = {}
        for sh in wb.find("m:sheets", NS).findall("m:sheet", NS):
            rid = sh.get(f"{{{NS['r']}}}id")
            t = target[rid].lstrip("/")
            part = t if t.startswith("xl/") else "xl/" + t
            root = ET.fromstring(_member(z, part))
            cells: dict[tuple[int, int], object] = {}
            for c in root.iter(f"{{{NS['m']}}}c"):
                ref = c.get("r")
                m = re.fullmatch(r"([A-Z]+)(\d+)", ref or "")
                if not m:
                    continue
                typ = c.get("t")
                v = c.find("m:v", NS)
                if typ == "inlineStr":
                    val: object = _text(c.find("m:is", NS)) if c.find("m:is", NS) is not None else ""
                elif v is None or v.text is None:
                    continue
                elif typ == "s":
                    val = shared[int(v.text)]
                elif typ in ("str", "e"):
                    val = v.text
                elif typ == "b":
                    val = v.text == "1"
                else:
                    try:
                        val = float(v.text)
                    except ValueError:
                        val = v.text
                cells[(int(m.group(2)), col_to_num(m.group(1)))] = val
                if len(cells) > MAX_CELLS:
                    raise ValueError(f"sheet {sh.get('name')}: too many cells")
            out[sh.get("name")] = cells
        return out


def rows(sheet: dict[tuple[int, int], object]) -> list[tuple[int, list[object]]]:
    """[(row number, [cell values from column 1 to the last used column, None for empty])] in row order."""
    by: dict[int, dict[int, object]] = {}
    for (r, c), v in sheet.items():
        by.setdefault(r, {})[c] = v
    res = []
    for r in sorted(by):
        mc = max(by[r])
        res.append((r, [by[r].get(c) for c in range(1, mc + 1)]))
    return res
