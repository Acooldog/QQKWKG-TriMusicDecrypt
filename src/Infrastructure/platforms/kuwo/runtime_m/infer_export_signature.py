"""Infer recovered export signature from 180s export-behavior captures."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
from collections import Counter
from typing import Any


DEFAULT_REPORT_DIR = pathlib.Path("kuwo/m/out")
EXIT_OK = 0
EXIT_NOT_FOUND = 2
EXIT_FAILED = 3


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def normalize_symbol_name(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return ""
    if "!" in text:
        text = text.split("!", 1)[1]
    msvc = re.match(r"^\?([^@]+)@@", text)
    if msvc:
        text = msvc.group(1)
    return text.lower()


def guess_abi(symbol: str) -> str:
    text = symbol or ""
    if "@@YG" in text:
        return "stdcall"
    if "@@YA" in text:
        return "cdecl"
    return "cdecl"


def infer_arg_layout(normalized_symbol: str) -> list[dict[str, Any]]:
    if "exportfilea" in normalized_symbol or normalized_symbol.endswith("exporta"):
        return [
            {"index": 1, "name": "input_path", "kind": "char_ptr"},
            {"index": 2, "name": "output_path", "kind": "std_string_ref_msvc"},
            {"index": 3, "name": "flags", "kind": "u32"},
        ]
    return [
        {"index": 1, "name": "input_path", "kind": "wchar_ptr"},
        {"index": 2, "name": "output_path", "kind": "std_wstring_ref_msvc"},
        {"index": 3, "name": "flags", "kind": "u32"},
    ]


def rank_symbol_priority(symbol: str) -> int:
    normalized = normalize_symbol_name(symbol)
    preferred_order = ["music_exportfilea", "music_exportfile", "music_exporta", "music_export"]
    for idx, key in enumerate(preferred_order):
        if key in normalized:
            return idx
    return len(preferred_order)


def confidence_from_count(count: int) -> str:
    if count >= 6:
        return "high"
    if count >= 3:
        return "medium"
    if count >= 1:
        return "low"
    return "none"


def build_candidate(symbol: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_symbol_name(symbol)
    abi = guess_abi(symbol)
    layout = infer_arg_layout(normalized)
    flags_counter: Counter[int] = Counter()
    for sample in items:
        try:
            flags_counter[int(sample.get("arg2_u32", 0))] += 1
        except Exception:
            pass
    flags_hint = flags_counter.most_common(1)[0][0] if flags_counter else 0
    sample_count = len(items)

    return {
        "symbol": symbol,
        "abi": abi,
        "arg_layout": layout,
        "flags_hint": int(flags_hint),
        "confidence": confidence_from_count(sample_count),
        "evidence": {
            "sample_count": sample_count,
            "flags_histogram_top": flags_counter.most_common(8),
        },
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Infer recovered signature from export behavior capture.")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="Directory of capture outputs")
    return parser


def main() -> int:
    args = make_parser().parse_args()
    report_dir = pathlib.Path(args.report_dir).resolve()
    signature_input = report_dir / "export_signature_180s.json"
    summary_input = report_dir / "call_summary_180s.json"
    recovered_output = report_dir / "recovered_signature.json"

    started = dt.datetime.now().astimezone().isoformat()

    if not signature_input.exists():
        payload = {
            "timestamp": started,
            "schema_version": "v2",
            "result_code": EXIT_NOT_FOUND,
            "result_reason": "signature_capture_missing",
            "source": str(signature_input),
            "primary_signature": None,
            "signature_candidates": [],
            "confidence": "none",
        }
        recovered_output.write_text(to_json(payload) + "\n", encoding="utf-8")
        print("[infer_export_signature] signature capture missing")
        return EXIT_NOT_FOUND

    try:
        samples = json.loads(signature_input.read_text(encoding="utf-8"))
        if not isinstance(samples, list):
            samples = []
    except Exception:
        samples = []

    summary: dict[str, Any] = {}
    if summary_input.exists():
        try:
            summary = json.loads(summary_input.read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                summary = {}
        except Exception:
            summary = {}

    if not samples:
        payload = {
            "timestamp": started,
            "schema_version": "v2",
            "result_code": EXIT_NOT_FOUND,
            "result_reason": "no_export_behavior_captured",
            "primary_signature": None,
            "signature_candidates": [],
            "confidence": "none",
            "evidence": {
                "sample_count_total": 0,
                "summary_path": str(summary_input),
                "signature_path": str(signature_input),
            },
        }
        recovered_output.write_text(to_json(payload) + "\n", encoding="utf-8")
        print("[infer_export_signature] no export behavior captured")
        return EXIT_NOT_FOUND

    groups: dict[str, list[dict[str, Any]]] = {}
    for item in samples:
        symbol = (item.get("symbol") or "").strip()
        if not symbol:
            continue
        groups.setdefault(symbol, []).append(item)

    if not groups:
        payload = {
            "timestamp": started,
            "schema_version": "v2",
            "result_code": EXIT_NOT_FOUND,
            "result_reason": "export_symbol_not_found",
            "primary_signature": None,
            "signature_candidates": [],
            "confidence": "none",
        }
        recovered_output.write_text(to_json(payload) + "\n", encoding="utf-8")
        print("[infer_export_signature] export symbol not found")
        return EXIT_NOT_FOUND

    symbol_items = sorted(
        groups.items(),
        key=lambda kv: (rank_symbol_priority(kv[0]), -len(kv[1]), normalize_symbol_name(kv[0])),
    )

    candidates: list[dict[str, Any]] = [build_candidate(symbol, items) for symbol, items in symbol_items]
    primary = candidates[0] if candidates else None

    result_code = EXIT_OK if primary else EXIT_NOT_FOUND
    result_reason = "ok" if primary else "export_symbol_not_found"
    confidence = primary.get("confidence", "none") if primary else "none"

    payload = {
        "timestamp": started,
        "schema_version": "v2",
        "result_code": result_code,
        "result_reason": result_reason,
        "primary_signature": primary,
        "signature_candidates": candidates,
        # Backward-compatible mirrors
        "symbol": (primary or {}).get("symbol"),
        "abi": (primary or {}).get("abi"),
        "arg_layout": (primary or {}).get("arg_layout"),
        "flags_hint": (primary or {}).get("flags_hint"),
        "confidence": confidence,
        "evidence": {
            "sample_count_total": len(samples),
            "sample_count_selected": (primary or {}).get("evidence", {}).get("sample_count", 0),
            "symbols_seen": sorted(groups.keys()),
            "summary_total_calls": int(summary.get("total_calls", 0) or 0),
            "summary_unique_functions": int(summary.get("unique_functions", 0) or 0),
        },
    }

    recovered_output.write_text(to_json(payload) + "\n", encoding="utf-8")
    print(f"[infer_export_signature] result_code={result_code} reason={result_reason}")
    print(f"[infer_export_signature] symbol={(primary or {}).get('symbol')}")
    print(f"[infer_export_signature] recovered={recovered_output}")
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())
