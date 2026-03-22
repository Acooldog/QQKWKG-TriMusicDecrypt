from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def scan_file(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "name": path.name,
        "relative_path": str(path),
        "suffix": path.suffix.lower(),
        "size": len(data),
        "head16": data[:16].hex(),
        "head64": data[:64].hex(),
        "tail32": data[-32:].hex() if len(data) >= 32 else data.hex(),
        "tail256": data[-256:].hex() if len(data) >= 256 else data.hex(),
        "tail_marker8": data[-8:].hex() if len(data) >= 8 else data.hex(),
        "head_sha1_4k": hashlib.sha1(data[:4096]).hexdigest(),
        "tail_sha1_4k": hashlib.sha1(data[-4096:]).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan QQ safeChange encrypted variants.")
    parser.add_argument("--root", default=r"D:\A_python\QQKWKG-TriMusicDecrypt\safeChange", help="Root folder to scan")
    parser.add_argument("--output", default=r"D:\A_python\QQKWKG-TriMusicDecrypt\_log\safechange_variant_scan.json", help="JSON output path")
    args = parser.parse_args()

    root = Path(args.root)
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".mgg", ".mflac", ".mmp4"}:
            continue
        rows.append(scan_file(path))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote {len(rows)} rows -> {output_path}")
    print("suffixes:", dict(Counter(row["suffix"] for row in rows)))
    print("tail_marker8:")
    for marker, count in Counter(row["tail_marker8"] for row in rows).most_common():
        print(f"  {count:>3}  {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
