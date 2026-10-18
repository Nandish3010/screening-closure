"""Download the external calibration datasets (ticket B18) and write the manifest.

    python3 -I tools/calibration/fetch.py [--force]

Each file goes into its own directory data/external/<source>/ (created empty, gitignored). The script only downloads and
hashes bytes; it never opens, unpacks or executes what it fetched. Existing files are kept (and re-hashed against the
manifest) unless --force. The manifest records URL, download time (UTC), sha256, size and licence; a copy is committed as
tools/calibration/manifest.json because data/ is ignored.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources as S  # noqa: E402

MAX_BYTES = 50 * 1024 * 1024
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    if not url.startswith("https://"):
        raise SystemExit(f"refusing non-https url {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    tmp = dest.with_suffix(dest.suffix + ".part")
    n = 0
    with urllib.request.urlopen(req, timeout=120) as r, tmp.open("wb") as out:
        if not r.geturl().startswith("https://"):
            raise SystemExit(f"redirected to non-https {r.geturl()}")
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            n += len(chunk)
            if n > MAX_BYTES:
                tmp.unlink()
                raise SystemExit(f"{url}: larger than {MAX_BYTES} bytes, aborted")
            out.write(chunk)
    tmp.replace(dest)


def main(argv: list[str]) -> int:
    force = "--force" in argv
    old = {}
    if S.MANIFEST_LOCAL.exists():
        old = {e["id"]: e for e in json.loads(S.MANIFEST_LOCAL.read_text())["files"]}
    entries = []
    for src in S.SOURCES:
        d = S.EXTERNAL / src["dir"]
        d.mkdir(parents=True, exist_ok=True)
        dest = d / src["file"]
        prev = old.get(src["id"])
        if dest.exists() and not force:
            sha = sha256_file(dest)
            if prev and prev["sha256"] != sha:
                print(f"WARNING {src['id']}: local file hash differs from the manifest ({sha[:12]} vs {prev['sha256'][:12]})")
            when = prev["downloaded_utc"] if prev and prev["sha256"] == sha else datetime.fromtimestamp(dest.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            print(f"kept     {src['id']:14s} {dest.stat().st_size:>10,d} bytes sha256 {sha[:12]}")
        else:
            download(src["url"], dest)
            sha = sha256_file(dest)
            when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            print(f"download {src['id']:14s} {dest.stat().st_size:>10,d} bytes sha256 {sha[:12]}")
        entries.append({
            "id": src["id"], "title": src["title"], "url": src["url"], "path": f"data/external/{src['dir']}/{src['file']}",
            "downloaded_utc": when, "sha256": sha, "size_bytes": dest.stat().st_size,
            "licence": src["licence"], "licence_evidence": src["licence_evidence"], "attribution": src["attribution"],
        })
    aihw = S.EXTERNAL / "aihw"
    manifest = {"note": "External datasets are untrusted input: kept only under data/external/ (gitignored), read only by tools/calibration scripts run with python3 -I.",
                "files": entries, "not_downloaded": S.BLOCKED}
    # a hand-placed AIHW file (owner action) is hashed too
    if aihw.is_dir():
        for p in sorted(aihw.glob("*.xlsx")):
            manifest["files"].append({"id": "aihw-nbcsp-2024", "title": "AIHW NBCSP monitoring report 2024 data tables (placed by hand)",
                                      "url": S.BLOCKED[0]["page"], "path": f"data/external/aihw/{p.name}",
                                      "downloaded_utc": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      "sha256": sha256_file(p), "size_bytes": p.stat().st_size, "licence": "CC BY 4.0 (per the ticket)",
                                      "licence_evidence": "not confirmed", "attribution": "Source: Australian Institute of Health and Welfare, CC BY 4.0."})
            manifest["not_downloaded"] = []
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    S.MANIFEST_LOCAL.write_text(text)
    shutil.copyfile(S.MANIFEST_LOCAL, S.MANIFEST_COMMITTED)
    print(f"manifest written: {S.MANIFEST_LOCAL.relative_to(S.ROOT)} (copy: {S.MANIFEST_COMMITTED.relative_to(S.ROOT)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
