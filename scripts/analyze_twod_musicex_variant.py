from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


TAIL_LEN = 192


def decode_utf16le_fragment(data: bytes) -> list[str]:
    text = data.decode("utf-16le", errors="ignore")
    return [part for part in text.split("\x00") if part.strip("\x00")]


def parse_tail(data: bytes) -> dict[str, object]:
    tail = data[-TAIL_LEN:] if len(data) >= TAIL_LEN else data
    pieces = decode_utf16le_fragment(tail)
    short_token = next((p for p in pieces if p.startswith("00") and ".mgg" not in p), "")
    full_name = next((p for p in pieces if p.startswith("O8M") and p.endswith(".mgg")), "")
    trailer_magic = tail[-8:].decode("latin1", errors="ignore") if len(tail) >= 8 else ""
    return {
        "tail_len": len(tail),
        "tail_hex": tail.hex(),
        "tail32": tail[-32:].hex() if len(tail) >= 32 else tail.hex(),
        "tail16": tail[-16:].hex() if len(tail) >= 16 else tail.hex(),
        "pieces": pieces,
        "short_token": short_token,
        "full_name": full_name,
        "trailer_magic_hex": tail[-8:].hex() if len(tail) >= 8 else tail.hex(),
        "trailer_magic_latin1": trailer_magic,
        "flag_0": int.from_bytes(tail[0:4], "little") if len(tail) >= 4 else None,
        "flag_1": int.from_bytes(tail[4:8], "little") if len(tail) >= 8 else None,
        "flag_2": int.from_bytes(tail[8:12], "little") if len(tail) >= 12 else None,
        "flag_3": int.from_bytes(tail[172:176], "little") if len(tail) >= 176 else None,
        "flag_4": int.from_bytes(tail[176:180], "little") if len(tail) >= 180 else None,
        "flag_5": int.from_bytes(tail[180:184], "little") if len(tail) >= 184 else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze TwoD musicex-style mgg variant tails.")
    parser.add_argument("--root", default=r"D:\A_python\QQKWKG-TriMusicDecrypt\safeChange\TwoD")
    parser.add_argument("--output", default=r"D:\A_python\QQKWKG-TriMusicDecrypt\_log\twod_musicex_analysis.json")
    args = parser.parse_args()

    root = Path(args.root)
    rows = []
    for path in sorted(root.rglob("*.mgg")):
        data = path.read_bytes()
        parsed = parse_tail(data)
        rows.append({
            "name": path.name,
            "path": str(path),
            "size": len(data),
            **parsed,
        })

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"wrote {len(rows)} rows -> {output_path}")
    print("trailer magics:")
    for marker, count in Counter(row["trailer_magic_hex"] for row in rows).most_common():
        print(f"  {count:>3}  {marker}")
    print("full names sample:")
    for row in rows[:5]:
        print(f"  {row['name']} => {row['full_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
