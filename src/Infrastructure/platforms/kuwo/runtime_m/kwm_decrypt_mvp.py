"""KWMusic KWM decrypt MVP (in-process export call path)."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import pathlib
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Any

import frida

try:
    from .process_guard import ProcessGuard
except ImportError:
    from process_guard import ProcessGuard


DEFAULT_EXE_PATH = ""
BASE_DIR = pathlib.Path(__file__).resolve().parent
KUWO_ROOT = BASE_DIR.parent
DEFAULT_OUTPUT_DIR = KUWO_ROOT / "_log" / "work"
DEFAULT_FINAL_OUTPUT_DIR = KUWO_ROOT / "output"
DEFAULT_CANDIDATE_REPORT = BASE_DIR / "decrypt_candidates_latest.json"
DEFAULT_SYMBOL_MAP_REPORT = BASE_DIR / "decrypt_function_symbols.json"
DEFAULT_SIGNATURE_FILE = BASE_DIR / "out" / "recovered_signature.json"
DEFAULT_MAX_CRASH = 3
DEFAULT_MAX_CONSECUTIVE_CLOSES = 3
DEFAULT_TIMEOUT_SEC = 8
DEFAULT_SYMBOL_WAIT_SEC = 5
DEFAULT_PROCESS_NAME = "kwmusic.exe"
DEFAULT_WAIT_POLL_INTERVAL_SEC = 0.2
KNOWN_AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac", ".ape", ".bin"}
MAX_RECOVERED_FASTPATH_CANDIDATES = 1

EXIT_OK = 0
EXIT_NOT_FOUND = 2
EXIT_FAILED = 3

AGENT_PATH = BASE_DIR / "kwm_export_agent.js"


@dataclass
class AttemptSpec:
    symbol: str
    abi: str
    signature: str
    arg_encoding: str


CALL_MATRIX = [
    AttemptSpec("Music_ExportFileA", "cdecl", "int(char*, char*)", "ansi"),
    AttemptSpec("Music_ExportFile", "cdecl", "int(wchar_t*, wchar_t*)", "utf16"),
    AttemptSpec("Music_ExportA", "cdecl", "int(char*, char*)", "ansi"),
    AttemptSpec("Music_Export", "cdecl", "int(wchar_t*, wchar_t*)", "utf16"),
]


def normalize_name(name: str) -> str:
    return (name or "").strip().lower()


def contains_non_ascii(text: str) -> bool:
    try:
        (text or "").encode("ascii")
        return False
    except Exception:
        return True


def ascii_safe_token(text: str, *, fallback: str = "kwm") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip())
    value = value.strip("._-")
    return value or fallback


def normalize_symbol_name(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return ""
    if "!" in text:
        text = text.split("!", 1)[1]
    msvc = re.match(r"^\?([^@]+)@@", text)
    if msvc:
        text = msvc.group(1)
    text = text.lstrip("_?")
    while text.endswith(tuple(str(i) for i in range(10))):
        if "@" not in text:
            break
        head, tail = text.rsplit("@", 1)
        if tail.isdigit():
            text = head
        else:
            break
    return text.lower()


def is_export_symbol_name(name: str) -> bool:
    norm = normalize_symbol_name(name)
    return norm.startswith("music_export")


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


TIMING_KEYS = (
    "prepare_sec",
    "ensure_target_sec",
    "recovered_phase_sec",
    "fallback_phase_sec",
    "attach_load_total_sec",
    "symbol_wait_total_sec",
    "call_total_sec",
    "artifact_wait_total_sec",
    "report_write_sec",
    "total_sec",
)


def new_timing() -> dict[str, float]:
    return {k: 0.0 for k in TIMING_KEYS}


def safe_console_print(message: str) -> None:
    text = str(message)
    try:
        print(text)
        return
    except UnicodeEncodeError:
        pass
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    data = (text + "\n").encode(encoding, errors="replace")
    try:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    except Exception:
        print(text.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


def run_cmd(command: list[str]) -> tuple[int, str, str]:
    cp = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return cp.returncode, cp.stdout.strip(), cp.stderr.strip()


def run_powershell(script: str) -> tuple[int, str, str]:
    return run_cmd(["powershell.exe", "-NoProfile", "-Command", script])


def query_wer(process_name: str, window_minutes: int = 30, limit: int = 6) -> list[dict[str, Any]]:
    escaped = process_name.replace("'", "''")
    ps = (
        f"$name='{escaped}'; "
        f"$start=(Get-Date).AddMinutes(-{int(window_minutes)}); "
        "$events=Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=$start} -ErrorAction SilentlyContinue | "
        "Where-Object { ($_.Id -eq 1000 -or $_.Id -eq 1001) -and $_.Message -match $name } | "
        "Sort-Object TimeCreated -Descending | Select-Object -First "
        f"{int(limit)}; "
        "$out=@(); "
        "foreach($e in $events){ "
        "  $fault=''; $exc=''; "
        "  if($e.Message -match 'Faulting module name: ([^,]+),'){ $fault=$matches[1] } "
        "  if($e.Message -match 'Exception code: ([^\\r\\n]+)'){ $exc=$matches[1].Trim() } "
        "  $first=($e.Message -split \"`r?`n\")[0]; "
        "  $out += [pscustomobject]@{time=$e.TimeCreated;id=$e.Id;provider=$e.ProviderName;fault_module=$fault;exception_code=$exc;summary=$first} "
        "} "
        "$out | ConvertTo-Json -Compress"
    )
    code, out, err = run_powershell(ps)
    if code != 0:
        return [{"error": err or out or "wer_query_failed"}]
    if not out:
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except Exception:
        return [{"raw": out}]


def process_exists(device: frida.core.Device, pid: int) -> bool:
    for proc in device.enumerate_processes():
        if proc.pid == pid:
            return True
    try:
        session = device.attach(pid)
    except Exception:
        return False
    try:
        return True
    finally:
        try:
            session.detach()
        except Exception:
            pass


def find_latest_process_by_name(device: frida.core.Device, process_name: str):
    target = normalize_name(process_name)
    found = [p for p in device.enumerate_processes() if normalize_name(p.name) == target]
    if not found:
        return None
    return sorted(found, key=lambda x: x.pid)[-1]


def start_kwmusic_process(
    device: frida.core.Device,
    exe_path: pathlib.Path,
    process_name: str,
    timeout_sec: int,
) -> int | None:
    try:
        subprocess.Popen(
            [str(exe_path)],
            cwd=str(exe_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return None

    deadline = time.time() + max(2, timeout_sec)
    while time.time() < deadline:
        proc = find_latest_process_by_name(device, process_name)
        if proc is not None:
            return int(proc.pid)
        time.sleep(0.25)
    return None


def ensure_target_process(
    device: frida.core.Device,
    process_name: str,
    exe_path: pathlib.Path,
    guard: ProcessGuard,
    timeout_sec: int,
) -> tuple[int | None, dict[str, Any]]:
    proc = find_latest_process_by_name(device, process_name)
    if proc is not None:
        guard.observe_start(proc.pid, start_time="existing_process")
        return int(proc.pid), {"started_new": False, "detail": "existing_process"}

    if not guard.can_restart():
        return None, {"started_new": False, "detail": "guard_restart_blocked"}

    guard.register_restart_attempt("ensure_target_process")
    if guard.should_stop():
        return None, {"started_new": False, "detail": "guard_stop_after_restart_register"}

    pid = start_kwmusic_process(device, exe_path, process_name, timeout_sec=timeout_sec)
    if pid is None:
        return None, {"started_new": True, "detail": "process_start_timeout_or_failed"}

    guard.observe_start(pid, start_time="spawned")
    return pid, {"started_new": True, "detail": "process_started", "pid": pid}


def detect_audio_ext(path: pathlib.Path) -> str:
    if not path.exists():
        return "bin"
    data = path.read_bytes()[:64]
    if len(data) < 4:
        return "bin"
    if data.startswith(b"fLaC"):
        return "flac"
    if data.startswith(b"ID3"):
        return "mp3"
    if len(data) >= 2 and data[0] == 0xFF and data[1] in (0xFB, 0xF3, 0xF2):
        return "mp3"
    if data.startswith(b"OggS"):
        return "ogg"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WAVE":
        return "wav"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "m4a"
    return "bin"


def compute_effective_wait_sec(input_path: pathlib.Path, timeout_sec: int) -> float:
    size_mb = max(0.0, float(input_path.stat().st_size) / (1024 * 1024))
    dynamic = 6 + math.ceil(size_mb / 2)
    hard_cap = max(4, int(timeout_sec))
    return float(max(4, min(hard_cap, min(45, dynamic))))


def build_attempt_payload(
    spec: AttemptSpec,
    input_path: pathlib.Path,
    raw_output_path: pathlib.Path,
    resolved_symbol: str | None = None,
    symbol_hints: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "symbol": resolved_symbol or spec.symbol,
        "abi": spec.abi,
        "signature": spec.signature,
        "argEncoding": spec.arg_encoding,
        "arg1": str(input_path.resolve()),
        "arg2": str(raw_output_path.resolve()),
        "symbolHints": symbol_hints or [],
    }


def build_default_arg_layout(arg_encoding: str) -> list[dict[str, Any]]:
    if arg_encoding == "utf16":
        return [
            {"index": 1, "name": "input_path", "kind": "wchar_ptr"},
            {"index": 2, "name": "output_path", "kind": "std_wstring_ref_msvc"},
            {"index": 3, "name": "flags", "kind": "u32"},
        ]
    return [
        {"index": 1, "name": "input_path", "kind": "char_ptr"},
        {"index": 2, "name": "output_path", "kind": "std_string_ref_msvc"},
        {"index": 3, "name": "flags", "kind": "u32"},
    ]


def arg_layout_is_wide(arg_layout: list[dict[str, Any]]) -> bool:
    for item in arg_layout or []:
        kind = str(item.get("kind", "")).lower()
        if "wchar" in kind or "wstring" in kind:
            return True
    return False


def call_export_recovered_with_timeout(
    script,
    payload: dict[str, Any],
    timeout_sec: float,
) -> tuple[dict[str, Any] | None, str | None]:
    timeout = max(1.0, float(timeout_sec))
    async_exports = getattr(script, "exports_async", None)
    if async_exports is None:
        try:
            return script.exports_sync.call_export_recovered(payload), None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

    async def _invoke() -> dict[str, Any]:
        return await async_exports.call_export_recovered(payload)

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(asyncio.wait_for(_invoke(), timeout=timeout))
        return result, None
    except asyncio.TimeoutError:
        return None, f"call_timeout_after_{timeout:.1f}s"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def find_symbol_alias(target_symbol: str, symbols: list[dict[str, Any]]) -> str | None:
    target = normalize_symbol_name(target_symbol)
    for item in symbols:
        sym = (item.get("symbol") or "").strip()
        if sym and normalize_symbol_name(sym) == target:
            return sym
    return None


def wait_for_symbol(script, target_symbol: str, timeout_sec: int) -> tuple[bool, dict[str, Any], str | None]:
    last_symbols: dict[str, Any] = {"module": "KwLib.dll", "symbols": []}
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        last_symbols = script.exports_sync.listsymbols()
        symbols = last_symbols.get("symbols", [])
        alias = find_symbol_alias(target_symbol, symbols)
        hit = alias is not None
        if hit:
            return True, last_symbols, alias
        time.sleep(0.5)
    return False, last_symbols, None


def load_symbol_hints(candidate_report: pathlib.Path, symbol_map_report: pathlib.Path) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()

    def push(name: str) -> None:
        value = (name or "").strip()
        if not value:
            return
        norm = normalize_symbol_name(value)
        if not norm:
            return
        if norm.startswith("0x"):
            return
        if norm in seen:
            return
        seen.add(norm)
        hints.append(value)

    if candidate_report.exists():
        try:
            data = json.loads(candidate_report.read_text(encoding="utf-8"))
            for item in data.get("candidates", []):
                frame = (item.get("frame") or "").strip()
                if "!" in frame:
                    push(frame.split("!", 1)[1])
        except Exception:
            pass

    if symbol_map_report.exists():
        try:
            data = json.loads(symbol_map_report.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    push((item or {}).get("symbol") or "")
        except Exception:
            pass

    return hints


def sort_call_matrix(symbol_hints: list[str], *, prefer_wide: bool = False) -> list[AttemptSpec]:
    ordered = list(CALL_MATRIX)
    if prefer_wide:
        wide = [x for x in ordered if x.arg_encoding == "utf16"]
        ansi = [x for x in ordered if x.arg_encoding != "utf16"]
        ordered = wide + ansi
    if not symbol_hints:
        return ordered
    hint_set = {normalize_symbol_name(x) for x in symbol_hints}
    preferred: list[AttemptSpec] = []
    others: list[AttemptSpec] = []
    for spec in ordered:
        if normalize_symbol_name(spec.symbol) in hint_set:
            preferred.append(spec)
        else:
            others.append(spec)
    return preferred + others


def load_recovered_signature(signature_file: pathlib.Path) -> dict[str, Any] | None:
    if not signature_file.exists():
        return None
    try:
        data = json.loads(signature_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        # Backward compatible with old shape: {symbol, abi, arg_layout, ...}
        if data.get("symbol") and data.get("arg_layout"):
            return data
        # New shape: {primary_signature, signature_candidates, ...}
        primary = data.get("primary_signature")
        if isinstance(primary, dict) and primary.get("symbol") and primary.get("arg_layout"):
            merged = dict(primary)
            merged["signature_candidates"] = data.get("signature_candidates") or []
            merged["schema_version"] = data.get("schema_version", "v2")
            merged["evidence"] = data.get("evidence", {})
            return merged
        return None
    except Exception:
        return None


def resolve_output_path_hint(output_hint: str, base_dirs: list[pathlib.Path]) -> pathlib.Path | None:
    text = (output_hint or "").strip()
    if not text:
        return None
    candidate = pathlib.Path(text)
    if candidate.is_absolute() and candidate.exists() and candidate.is_file():
        return candidate
    for base in base_dirs:
        full = base / candidate
        if full.exists() and full.is_file():
            return full
    return None


def snapshot_audio_files(directory: pathlib.Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not directory.exists():
        return out
    for p in directory.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in KNOWN_AUDIO_EXTS:
            continue
        st = p.stat()
        out[str(p.resolve())] = {
            "size": int(st.st_size),
            "mtime_ns": int(st.st_mtime_ns),
            "ctime_ns": int(st.st_ctime_ns),
        }
    return out


def detect_new_audio_file(before: dict[str, dict[str, Any]], directory: pathlib.Path, min_size: int = 4096) -> pathlib.Path | None:
    after = snapshot_audio_files(directory)
    candidates: list[tuple[int, pathlib.Path]] = []
    for path_str, meta in after.items():
        prev = before.get(path_str)
        if prev is None:
            if meta["size"] >= min_size:
                candidates.append((max(meta["mtime_ns"], meta["ctime_ns"]), pathlib.Path(path_str)))
            continue
        is_changed = (
            meta["size"] != prev.get("size")
            or meta["mtime_ns"] != prev.get("mtime_ns")
            or meta["ctime_ns"] != prev.get("ctime_ns")
        )
        if meta["size"] >= min_size and is_changed:
            candidates.append((max(meta["mtime_ns"], meta["ctime_ns"]), pathlib.Path(path_str)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def detect_new_bin_audio_files(
    before_snapshot: dict[str, dict[str, Any]],
    bin_dir: pathlib.Path,
    run_start_ts: float,
) -> list[dict[str, Any]]:
    run_start_ns = int(run_start_ts * 1_000_000_000)
    after = snapshot_audio_files(bin_dir)
    out: list[dict[str, Any]] = []
    for path_str, meta in after.items():
        prev = before_snapshot.get(path_str)
        is_new = prev is None
        is_changed = prev is not None and (
            meta["size"] != prev.get("size")
            or meta["mtime_ns"] != prev.get("mtime_ns")
            or meta["ctime_ns"] != prev.get("ctime_ns")
        )
        if not is_new and not is_changed:
            continue
        if max(meta["mtime_ns"], meta["ctime_ns"]) < run_start_ns:
            continue
        out.append(
            {
                "path": path_str,
                "size": int(meta["size"]),
                "mtime_ns": int(meta["mtime_ns"]),
                "ctime_ns": int(meta["ctime_ns"]),
                "ext": pathlib.Path(path_str).suffix.lower(),
            }
        )
    out.sort(key=lambda x: max(x["mtime_ns"], x["ctime_ns"]), reverse=True)
    return out


def next_output_name(final_output_dir: pathlib.Path, base_name: str, ext: str) -> pathlib.Path:
    ext_text = ext if ext.startswith(".") else f".{ext}"
    candidate = final_output_dir / f"{base_name}{ext_text}"
    if not candidate.exists():
        return candidate
    idx = 1
    while True:
        p = final_output_dir / f"{base_name}.{idx}{ext_text}"
        if not p.exists():
            return p
        idx += 1


def next_report_stem(report_dir: pathlib.Path, base_name: str) -> str:
    stem = base_name
    idx = 1
    while (
        (report_dir / f"{stem}.report.json").exists()
        or (report_dir / f"{stem}.report.txt").exists()
    ):
        stem = f"{base_name}.{idx}"
        idx += 1
    return stem


def resolve_export_base_dir(exe_path: pathlib.Path) -> pathlib.Path:
    bin_dir = exe_path.parent / "bin"
    if bin_dir.exists() and bin_dir.is_dir():
        return bin_dir
    return exe_path.parent


def relocate_bin_outputs(
    new_files: list[dict[str, Any]],
    final_output_dir: pathlib.Path,
    input_base_name: str,
) -> list[dict[str, Any]]:
    relocated: list[dict[str, Any]] = []
    for item in new_files:
        src = pathlib.Path(item["path"])
        if not src.exists() or not src.is_file():
            continue
        size = src.stat().st_size
        if size <= 0:
            continue
        detected = detect_audio_ext(src)
        ext = detected if detected != "bin" else (src.suffix.lower().lstrip(".") or "bin")
        dst = next_output_name(final_output_dir, input_base_name, ext)
        moved = False
        err_text = None
        for _ in range(4):
            try:
                src.replace(dst)
                moved = True
                break
            except Exception as exc:
                err_text = str(exc)
                time.sleep(0.12)
        if not moved:
            try:
                shutil.copy2(src, dst)
                src.unlink(missing_ok=True)
                moved = True
            except Exception as exc:
                err_text = str(exc)
        if moved and dst.exists():
            relocated.append(
                {
                    "src": str(src),
                    "dst": str(dst),
                    "size": int(dst.stat().st_size),
                    "mtime_ns": int(dst.stat().st_mtime_ns),
                    "ctime_ns": int(dst.stat().st_ctime_ns),
                    "ext": dst.suffix.lower(),
                    "detected_ext": detected,
                }
            )
        elif err_text:
            relocated.append(
                {
                    "src": str(src),
                    "dst": None,
                    "size": int(size),
                    "mtime_ns": int(item.get("mtime_ns", 0)),
                    "ctime_ns": int(item.get("ctime_ns", 0)),
                    "ext": src.suffix.lower(),
                    "detected_ext": detected,
                    "error": err_text,
                }
            )
    return relocated


def write_text_report(path: pathlib.Path, report: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("KWMusic KWM Decrypt Report")
    lines.append("=" * 56)
    lines.append(f"timestamp: {report['timestamp']}")
    lines.append(f"input: {report['input']}")
    lines.append(f"output_dir: {report['output_dir']}")
    lines.append(f"report_dir: {report.get('report_dir')}")
    lines.append(f"final_output_dir: {report['final_output_dir']}")
    lines.append(f"raw_output_path: {report['raw_output_path']}")
    lines.append(f"signature_source: {report.get('signature_source')}")
    lines.append(f"relocated_from_bin: {report.get('relocated_from_bin')}")
    lines.append(f"result_code: {report['result_code']}")
    lines.append(f"result_reason: {report['result_reason']}")
    lines.append(f"stop_reason: {report.get('stop_reason')}")
    lines.append("timing:")
    lines.append(to_json(report.get("timing") or {}))
    lines.append("")
    lines.append("guard_summary:")
    lines.append(to_json(report.get("guard_summary")))
    lines.append("")
    lines.append("bin_snapshot_before:")
    lines.append(to_json(report.get("bin_snapshot_before")))
    lines.append("")
    lines.append("relocated_files:")
    lines.append(to_json(report.get("relocated_files")))
    lines.append("")
    lines.append("post_run_bin_new_files:")
    lines.append(to_json(report.get("post_run_bin_new_files")))
    lines.append("")
    lines.append("final_output:")
    lines.append(to_json(report.get("final_output")))
    lines.append("")
    lines.append("recovered_attempt:")
    lines.append(to_json(report.get("recovered_attempt")))
    lines.append("")
    lines.append("fallback_attempts:")
    for item in report.get("fallback_attempts", []):
        attach_sec = float(item.get("attach_load_sec") or 0.0)
        symbol_wait_sec = float(item.get("symbol_wait_sec") or 0.0)
        call_sec = float(item.get("call_sec") or item.get("elapsed_sec") or 0.0)
        artifact_wait_sec = float(item.get("artifact_wait_sec") or 0.0)
        attempt_total_sec = float(item.get("attempt_total_sec") or 0.0)
        lines.append(
            f"- idx={item.get('index')} pid={item.get('pid')} symbol={item.get('symbol')} abi={item.get('abi')} "
            f"status={item.get('status')} return={item.get('return_value')} "
            f"output={item.get('output_exists')} size={item.get('output_size')} ext={item.get('detected_ext')} "
            f"attach={attach_sec:.3f}s symbol_wait={symbol_wait_sec:.3f}s "
            f"call={call_sec:.3f}s artifact_wait={artifact_wait_sec:.3f}s "
            f"attempt_total={attempt_total_sec:.3f}s"
        )
        if item.get("error"):
            lines.append(f"  error={item.get('error')}")
    lines.append("")
    lines.append("wer_summary:")
    lines.append(to_json(report.get("wer_summary", [])))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KWMusic KWM decrypt MVP by export function calls.")
    parser.add_argument("--input", required=True, help="Input .kwm file path")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Working directory for temporary files")
    parser.add_argument("--report-dir", default=None, help="Report output directory (default: same as --output-dir)")
    parser.add_argument("--final-output-dir", default=str(DEFAULT_FINAL_OUTPUT_DIR), help="Final audio output directory")
    parser.add_argument("--exe-path", default=DEFAULT_EXE_PATH, help="KWMusic executable path")
    parser.add_argument("--candidate-report", default=str(DEFAULT_CANDIDATE_REPORT), help="Candidate report JSON path")
    parser.add_argument("--symbol-map-report", default=str(DEFAULT_SYMBOL_MAP_REPORT), help="Symbol map JSON path")
    parser.add_argument("--signature-file", default=str(DEFAULT_SIGNATURE_FILE), help="Recovered signature JSON path")
    parser.add_argument("--process-name", default=DEFAULT_PROCESS_NAME, help="Target process name")
    parser.add_argument("--pid", type=int, default=None, help="Preferred target PID (optional)")
    parser.add_argument("--max-crash", type=int, default=DEFAULT_MAX_CRASH, help="Max total restart attempts")
    parser.add_argument(
        "--max-consecutive-closes",
        type=int,
        default=DEFAULT_MAX_CONSECUTIVE_CLOSES,
        help="Stop after this many consecutive script-observed closes",
    )
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC, help="Timeout seconds per attempt")
    return parser


def _decrypt_impl(args: argparse.Namespace, verbose: bool = True) -> tuple[int, dict[str, Any]]:
    started = dt.datetime.now().astimezone()
    impl_started_perf = time.perf_counter()
    timing = new_timing()
    prepare_started_perf = time.perf_counter()
    input_path = pathlib.Path(args.input)
    output_dir = pathlib.Path(args.output_dir).resolve()
    report_dir = pathlib.Path(args.report_dir).resolve() if args.report_dir else output_dir
    final_output_dir = pathlib.Path(args.final_output_dir).resolve()
    exe_path_text = str(getattr(args, "exe_path", "") or "").strip()
    exe_path = pathlib.Path(exe_path_text) if exe_path_text else pathlib.Path()
    candidate_report = pathlib.Path(args.candidate_report).resolve()
    symbol_map_report = pathlib.Path(args.symbol_map_report).resolve()
    signature_file = pathlib.Path(args.signature_file).resolve()
    process_name = str(getattr(args, "process_name", DEFAULT_PROCESS_NAME) or DEFAULT_PROCESS_NAME)
    preferred_pid = int(args.pid) if getattr(args, "pid", None) else None

    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    final_output_dir.mkdir(parents=True, exist_ok=True)
    base_name = input_path.stem
    run_id = started.strftime("%Y%m%d_%H%M%S_%f")
    safe_base = ascii_safe_token(base_name, fallback="kwm")
    raw_output = output_dir / f"{safe_base}_{run_id}.raw"
    bin_dir = exe_path.parent.resolve() if exe_path_text else output_dir
    run_start_ts = time.time()
    bin_snapshot_before = snapshot_audio_files(bin_dir)
    bin_snapshot_cursor = dict(bin_snapshot_before)
    export_base_dir = resolve_export_base_dir(exe_path)
    report_stem = next_report_stem(report_dir, base_name)
    json_report_path = report_dir / f"{report_stem}.report.json"
    text_report_path = report_dir / f"{report_stem}.report.txt"

    def finish_early(reason: str, detail: str) -> tuple[int, dict[str, Any]]:
        timing["prepare_sec"] = round(time.perf_counter() - prepare_started_perf, 6)
        timing["total_sec"] = round(time.perf_counter() - impl_started_perf, 6)
        report_write_started = time.perf_counter()
        report = {
            "timestamp": started.isoformat(),
            "input": str(input_path.resolve()),
            "output_dir": str(output_dir),
            "report_dir": str(report_dir),
            "final_output_dir": str(final_output_dir),
            "raw_output_path": str(raw_output),
            "exe_path": exe_path_text,
            "candidate_report": str(candidate_report),
            "symbol_map_report": str(symbol_map_report),
            "signature_source": str(signature_file) if signature_file.exists() else None,
            "layout_inference": None,
            "result_code": EXIT_FAILED,
            "result_reason": reason,
            "stop_reason": None,
            "bin_snapshot_before": {},
            "relocated_from_bin": False,
            "relocated_files": [],
            "post_run_bin_new_files": [],
            "final_output": None,
            "recovered_attempt": None,
            "fallback_attempts": [],
            "wer_summary": [],
            "guard_summary": {},
            "error_detail": detail,
            "timing": timing,
            "report_json_path": str(json_report_path),
            "report_txt_path": str(text_report_path),
        }
        json_report_path.write_text(to_json(report) + "\n", encoding="utf-8")
        write_text_report(text_report_path, report)
        timing["report_write_sec"] = round(time.perf_counter() - report_write_started, 6)
        timing["total_sec"] = round(time.perf_counter() - impl_started_perf, 6)
        report["timing"] = timing
        json_report_path.write_text(to_json(report) + "\n", encoding="utf-8")
        write_text_report(text_report_path, report)
        if verbose:
            safe_console_print(f"[kwm_decrypt_mvp] result_code={EXIT_FAILED} reason={reason}")
            safe_console_print(f"[kwm_decrypt_mvp] detail={detail}")
            safe_console_print(f"[kwm_decrypt_mvp] report_json={json_report_path}")
            safe_console_print(f"[kwm_decrypt_mvp] report_txt={text_report_path}")
        return EXIT_FAILED, report

    if not input_path.exists() or input_path.suffix.lower() != ".kwm":
        return finish_early("invalid_input", "invalid input path or extension")
    if not exe_path_text:
        return finish_early("exe_not_found", "exe path unavailable")
    if not exe_path.exists() or not exe_path.is_file():
        return finish_early("exe_not_found", f"exe not found: {exe_path}")
    if not AGENT_PATH.exists():
        return finish_early("agent_not_found", f"agent not found: {AGENT_PATH}")

    # Use ASCII-safe input path for in-process export call to avoid mojibake path failures.
    call_input_path = input_path.resolve()
    copied_input_path: pathlib.Path | None = None
    if contains_non_ascii(str(call_input_path)):
        ascii_input_dir = output_dir / "_ascii_input"
        ascii_input_dir.mkdir(parents=True, exist_ok=True)
        copied_input_path = ascii_input_dir / f"{safe_base}_{run_id}{input_path.suffix.lower()}"
        shutil.copy2(call_input_path, copied_input_path)
        call_input_path = copied_input_path.resolve()
    timing["prepare_sec"] = round(time.perf_counter() - prepare_started_perf, 6)

    result_code = EXIT_NOT_FOUND
    result_reason = "export_timeout_no_artifact"
    stop_reason: str | None = None
    final_output: dict[str, Any] | None = None
    recovered_attempt: dict[str, Any] | None = None
    fallback_attempts: list[dict[str, Any]] = []
    wer_summary: list[dict[str, Any]] = []
    relocated_from_bin = False
    relocated_files: list[dict[str, Any]] = []
    post_run_bin_new_files: list[dict[str, Any]] = []

    device = frida.get_local_device()
    guard = ProcessGuard(max_restart_total=args.max_crash, max_consecutive_closes=args.max_consecutive_closes)
    agent_source = AGENT_PATH.read_text(encoding="utf-8")
    symbol_hints = load_symbol_hints(candidate_report, symbol_map_report)
    recovered_signature = load_recovered_signature(signature_file)
    # Prefer wide only when the actual call input path contains non-ASCII.
    # If input is copied to ASCII-safe temp path, ANSI-first is usually faster and more stable.
    prefer_wide = contains_non_ascii(str(call_input_path))
    call_matrix = sort_call_matrix(symbol_hints, prefer_wide=prefer_wide)
    effective_wait_sec = compute_effective_wait_sec(input_path, args.timeout_sec)
    stop_on_runtime_exit = True

    def capture_and_relocate_bin_outputs(source_tag: str) -> pathlib.Path | None:
        nonlocal bin_snapshot_cursor, relocated_from_bin, relocated_files

        new_files = detect_new_bin_audio_files(bin_snapshot_cursor, bin_dir, run_start_ts)
        if not new_files:
            bin_snapshot_cursor = snapshot_audio_files(bin_dir)
            return None

        moved = relocate_bin_outputs(new_files, final_output_dir, base_name)
        bin_snapshot_cursor = snapshot_audio_files(bin_dir)
        if not moved:
            return None

        relocated_from_bin = True
        for item in moved:
            item["source"] = source_tag
        relocated_files.extend(moved)

        for item in moved:
            dst_text = item.get("dst")
            if not dst_text:
                continue
            dst = pathlib.Path(dst_text)
            if not dst.exists() or not dst.is_file():
                continue
            size = dst.stat().st_size
            if size <= 4096:
                continue
            ext = detect_audio_ext(dst)
            if ext == "bin":
                continue
            return dst
        return None

    def wait_for_artifacts(
        source_tag: str,
        *,
        output_string: str = "",
        before_audio: dict[str, dict[str, Any]] | None = None,
        max_wait_sec: float | None = None,
    ) -> pathlib.Path | None:
        wait_sec = float(max_wait_sec) if max_wait_sec is not None else effective_wait_sec
        wait_sec = max(1.0, wait_sec)
        deadline = time.time() + wait_sec
        last_alt_path: pathlib.Path | None = None
        while time.time() < deadline:
            if raw_output.exists() and raw_output.stat().st_size > 0:
                return raw_output

            if output_string and last_alt_path is None:
                last_alt_path = resolve_output_path_hint(
                    output_string,
                    [export_base_dir, exe_path.parent, call_input_path.parent, input_path.parent, final_output_dir, output_dir],
                )
                if last_alt_path is not None and last_alt_path.exists():
                    try:
                        if raw_output.exists():
                            raw_output.unlink()
                        try:
                            last_alt_path.replace(raw_output)
                        except OSError:
                            shutil.copy2(last_alt_path, raw_output)
                            last_alt_path.unlink(missing_ok=True)
                        return raw_output
                    except Exception:
                        pass

            if before_audio is not None:
                generated_audio = detect_new_audio_file(before_audio, export_base_dir, min_size=4096)
                if generated_audio is not None and generated_audio.exists():
                    if raw_output.exists():
                        raw_output.unlink()
                    shutil.copy2(generated_audio, raw_output)
                    return raw_output

            relocated_final = capture_and_relocate_bin_outputs(f"{source_tag}:poll")
            if relocated_final is not None:
                return relocated_final

            time.sleep(DEFAULT_WAIT_POLL_INTERVAL_SEC)

        relocated_final = capture_and_relocate_bin_outputs(f"{source_tag}:timeout_check")
        if relocated_final is not None:
            return relocated_final
        return None

    recovered_candidates: list[dict[str, Any]] = []
    if recovered_signature:
        if is_export_symbol_name(str(recovered_signature.get("symbol") or "")):
            recovered_candidates.append(dict(recovered_signature))
        for item in recovered_signature.get("signature_candidates", []):
            if not isinstance(item, dict):
                continue
            if not item.get("symbol") or not item.get("arg_layout"):
                continue
            if not is_export_symbol_name(str(item.get("symbol") or "")):
                continue
            recovered_candidates.append(item)
    if prefer_wide and recovered_candidates:
        wide_candidates = [x for x in recovered_candidates if arg_layout_is_wide(x.get("arg_layout") or [])]
        if wide_candidates:
            recovered_candidates = wide_candidates
    # Fast-path: try primary recovered signature first, then rely on fallback matrix for compatibility.
    if len(recovered_candidates) > MAX_RECOVERED_FASTPATH_CANDIDATES:
        recovered_candidates = recovered_candidates[:MAX_RECOVERED_FASTPATH_CANDIDATES]

    ensure_target_started = time.perf_counter()
    if preferred_pid is not None:
        if process_exists(device, preferred_pid):
            current_pid = preferred_pid
            guard.observe_start(current_pid, start_time="preferred_pid")
            start_detail = {"started_new": False, "detail": "preferred_pid"}
        else:
            current_pid = None
            start_detail = {"started_new": False, "detail": "preferred_pid_not_running"}
            result_code = EXIT_FAILED
            result_reason = "target_pid_not_running"
    else:
        current_pid, start_detail = ensure_target_process(
            device=device,
            process_name=process_name,
            exe_path=exe_path,
            guard=guard,
            timeout_sec=max(3, args.timeout_sec),
        )
    timing["ensure_target_sec"] = round(time.perf_counter() - ensure_target_started, 6)
    if current_pid is None:
        result_code = EXIT_FAILED
        if result_reason not in {"target_pid_not_running"}:
            result_reason = "target_process_unavailable"
        stop_reason = guard.stop_reason

    def on_runtime_exit(reason: str, *, hard_stop: bool = False) -> None:
        nonlocal current_pid, result_code, result_reason, stop_reason
        guard.observe_exit(current_pid, reason, by_script=True)
        current_pid = None
        if hard_stop or stop_on_runtime_exit:
            if guard.stop_reason is None:
                guard.stop_reason = "script_induced_crash_suspected"
                guard.limit_reason = "runtime_exit_after_injection"
            stop_reason = guard.stop_reason
            result_code = EXIT_FAILED
            result_reason = stop_reason or "script_induced_crash_suspected"

    if result_code != EXIT_FAILED and recovered_candidates:
        recovered_phase_started = time.perf_counter()
        recovered_attempt = {
            "index": 0,
            "pid": current_pid,
            "symbol": recovered_candidates[0].get("symbol"),
            "abi": recovered_candidates[0].get("abi", "cdecl"),
            "signature": "recovered_signature",
            "arg_encoding": "recovered",
            "status": "pending",
            "error": None,
            "return_value": None,
            "resolved_address": None,
            "output_exists": False,
            "output_size": 0,
            "detected_ext": None,
            "symbol_wait_hit": False,
            "symbols_count": 0,
            "resolved_symbol": None,
            "env": None,
            "start_detail": start_detail,
            "restart_attempts": guard.restart_attempts,
            "consecutive_closes": guard.consecutive_closes,
            "arg_layout": recovered_candidates[0].get("arg_layout"),
            "flags_hint": recovered_candidates[0].get("flags_hint", 0),
            "confidence": recovered_candidates[0].get("confidence"),
            "attach_load_sec": 0.0,
            "symbol_wait_sec": 0.0,
            "call_sec": 0.0,
            "artifact_wait_sec": 0.0,
            "attempt_total_sec": 0.0,
        }

        session = None
        script = None
        try:
            if raw_output.exists():
                raw_output.unlink()

            if current_pid is None or not process_exists(device, current_pid):
                on_runtime_exit("missing_before_recovered_attempt", hard_stop=True)
                recovered_attempt["status"] = "process_unavailable"
                recovered_attempt["pid"] = None

            if result_code != EXIT_FAILED and current_pid is not None:
                attach_load_started = time.perf_counter()
                session = device.attach(current_pid)
                script = session.create_script(agent_source)
                script.load()
                recovered_attach_load_sec = round(time.perf_counter() - attach_load_started, 6)
                recovered_attempt["attach_load_sec"] = recovered_attach_load_sec
                timing["attach_load_total_sec"] += recovered_attach_load_sec

                recovered_attempt["env"] = script.exports_sync.getenv()
                symbol_wait_started = time.perf_counter()
                wait_hit, symbol_snapshot, resolved_symbol = wait_for_symbol(
                    script,
                    str(recovered_candidates[0].get("symbol") or ""),
                    DEFAULT_SYMBOL_WAIT_SEC,
                )
                recovered_symbol_wait_sec = round(time.perf_counter() - symbol_wait_started, 6)
                recovered_attempt["symbol_wait_sec"] = recovered_symbol_wait_sec
                timing["symbol_wait_total_sec"] += recovered_symbol_wait_sec
                recovered_attempt["symbol_wait_hit"] = wait_hit
                recovered_attempt["resolved_symbol"] = resolved_symbol
                symbols = symbol_snapshot.get("symbols", [])
                recovered_attempt["symbols_count"] = len(symbols)

                if wait_hit:
                    recovered_attempt["variant_results"] = []
                    # Use a single stable layout variant by default to reduce repeated in-process calls.
                    recovered_variants = ["msvc24"]
                    before_audio = snapshot_audio_files(export_base_dir)
                    artifact_path: pathlib.Path | None = None
                    for candidate in recovered_candidates:
                        symbol_text = str(candidate.get("symbol") or resolved_symbol or "")
                        abi_text = str(candidate.get("abi") or "cdecl")
                        arg_layout = candidate.get("arg_layout") or []
                        flags_hint = int(candidate.get("flags_hint", 0) or 0)
                        confidence = candidate.get("confidence")
                        for variant in recovered_variants:
                            payload = {
                                "symbol": symbol_text,
                                "abi": abi_text,
                                "argLayout": arg_layout,
                                "inputPath": str(call_input_path),
                                "outputPath": str(raw_output.resolve()),
                                "flags": flags_hint,
                                "symbolHints": symbol_hints,
                                "layoutVariant": variant,
                            }
                            t0 = time.time()
                            call_res, call_err = call_export_recovered_with_timeout(
                                script,
                                payload,
                                timeout_sec=max(4.0, min(30.0, effective_wait_sec + 2.0)),
                            )
                            elapsed = round(time.time() - t0, 3)
                            timing["call_total_sec"] += elapsed
                            recovered_attempt["call_sec"] = round(float(recovered_attempt.get("call_sec", 0.0)) + elapsed, 6)
                            if call_res is None:
                                call_res = {
                                    "ok": False,
                                    "error": call_err or "call_timeout",
                                    "returnValue": None,
                                    "resolvedAddress": None,
                                    "resolvedSymbol": None,
                                    "outputString": "",
                                }

                            variant_result = {
                                "candidate_symbol": symbol_text,
                                "candidate_abi": abi_text,
                                "candidate_confidence": confidence,
                                "variant": variant,
                                "ok": bool(call_res.get("ok")),
                                "return_value": call_res.get("returnValue"),
                                "error": call_res.get("error"),
                                "resolved_address": call_res.get("resolvedAddress"),
                                "resolved_symbol": call_res.get("resolvedSymbol"),
                                "output_string": call_res.get("outputString"),
                                "elapsed_sec": elapsed,
                                "call_sec": elapsed,
                            }
                            recovered_attempt["variant_results"].append(variant_result)
                            recovered_attempt["variant"] = variant
                            recovered_attempt["elapsed_sec"] = elapsed
                            recovered_attempt["return_value"] = call_res.get("returnValue")
                            recovered_attempt["resolved_address"] = call_res.get("resolvedAddress")
                            recovered_attempt["output_string"] = call_res.get("outputString")
                            recovered_attempt["arg_layout"] = arg_layout
                            recovered_attempt["flags_hint"] = flags_hint
                            recovered_attempt["confidence"] = confidence
                            if call_res.get("resolvedSymbol"):
                                recovered_attempt["resolved_symbol"] = call_res.get("resolvedSymbol")
                            if call_res.get("ok"):
                                recovered_attempt["status"] = "call_ok"
                                recovered_attempt["error"] = None
                            else:
                                err_text = str(call_res.get("error") or "")
                                recovered_attempt["status"] = "call_timeout" if "call_timeout" in err_text else "call_failed"
                                recovered_attempt["error"] = err_text

                            rv = call_res.get("returnValue")
                            wait_budget = effective_wait_sec
                            if rv not in (0, "0", None):
                                wait_budget = min(effective_wait_sec, 2.0)
                            if rv in (0, "0", None) and (call_res.get("outputString") or "").strip() and wait_budget < 6.0:
                                wait_budget = 6.0
                            artifact_wait_started = time.perf_counter()
                            artifact_path = wait_for_artifacts(
                                f"recovered:{symbol_text}:{variant}",
                                output_string=(call_res.get("outputString") or "").strip(),
                                before_audio=before_audio,
                                max_wait_sec=wait_budget,
                            )
                            artifact_wait_sec = round(time.perf_counter() - artifact_wait_started, 6)
                            recovered_attempt["artifact_wait_sec"] = round(
                                float(recovered_attempt.get("artifact_wait_sec", 0.0)) + artifact_wait_sec, 6
                            )
                            timing["artifact_wait_total_sec"] += artifact_wait_sec
                            variant_result["artifact_wait_sec"] = artifact_wait_sec
                            if artifact_path is not None:
                                if artifact_path != raw_output and artifact_path.exists():
                                    recovered_attempt["relocated_output_path"] = str(artifact_path)
                                break
                        if artifact_path is not None:
                            break
                else:
                    recovered_attempt["status"] = "symbol_not_found"

                recovered_attempt["output_exists"] = raw_output.exists()
                recovered_attempt["output_size"] = raw_output.stat().st_size if raw_output.exists() else 0
                ext = detect_audio_ext(raw_output) if raw_output.exists() else "bin"
                recovered_attempt["detected_ext"] = ext

                output_string = (recovered_attempt.get("output_string") or "").strip()
                alt_output_path = resolve_output_path_hint(
                    output_string,
                    [export_base_dir, exe_path.parent, call_input_path.parent, input_path.parent, final_output_dir, output_dir],
                )
                if (
                    (not recovered_attempt["output_exists"])
                    and alt_output_path is not None
                ):
                    recovered_attempt["alt_output_path"] = str(alt_output_path)
                    recovered_attempt["output_exists"] = True
                    recovered_attempt["output_size"] = alt_output_path.stat().st_size
                    ext = detect_audio_ext(alt_output_path)
                    recovered_attempt["detected_ext"] = ext
                    if raw_output.exists():
                        raw_output.unlink()
                    alt_output_path.replace(raw_output)

                if "relocated_output_path" not in recovered_attempt:
                    relocated_final = capture_and_relocate_bin_outputs("recovered:post_check")
                    if relocated_final is not None:
                        recovered_attempt["relocated_output_path"] = str(relocated_final)

                alive_now = process_exists(device, current_pid)
                if alive_now:
                    guard.mark_stable("recovered_attempt_completed_alive")
                else:
                    on_runtime_exit("exited_after_recovered_attempt", hard_stop=True)

                if raw_output.exists() and recovered_attempt["output_size"] > 4096 and ext != "bin":
                    final_path = next_output_name(final_output_dir, base_name, ext)
                    raw_output.replace(final_path)
                    final_output = {
                        "path": str(final_path),
                        "ext": ext,
                        "size": final_path.stat().st_size,
                    }
                    result_code = EXIT_OK
                    result_reason = "success"
                elif recovered_attempt.get("relocated_output_path"):
                    moved_path = pathlib.Path(str(recovered_attempt["relocated_output_path"]))
                    if moved_path.exists():
                        moved_size = moved_path.stat().st_size
                        moved_ext = detect_audio_ext(moved_path)
                        if moved_size > 4096 and moved_ext != "bin":
                            final_output = {
                                "path": str(moved_path),
                                "ext": moved_ext,
                                "size": moved_size,
                            }
                            result_code = EXIT_OK
                            result_reason = "success"

        except Exception as exc:
            recovered_attempt["status"] = "exception"
            recovered_attempt["error"] = f"{type(exc).__name__}: {exc}"
            recovered_attempt["traceback"] = traceback.format_exc().splitlines()
            alive_now = current_pid is not None and process_exists(device, current_pid)
            if not alive_now:
                on_runtime_exit("recovered_exception_with_process_exit", hard_stop=True)
            else:
                guard.mark_stable("recovered_exception_but_process_alive")
            if guard.should_stop() or stop_reason:
                stop_reason = guard.stop_reason
                result_code = EXIT_FAILED
                result_reason = stop_reason or "script_induced_crash_suspected"
            else:
                result_reason = "recovered_call_failed"
                result_code = EXIT_NOT_FOUND
        finally:
            try:
                if script is not None:
                    script.unload()
            except Exception:
                pass
            try:
                if session is not None:
                    session.detach()
            except Exception:
                pass
        recovered_total_sec = round(time.perf_counter() - recovered_phase_started, 6)
        if recovered_attempt is not None:
            recovered_attempt["attempt_total_sec"] = recovered_total_sec
        timing["recovered_phase_sec"] += recovered_total_sec

    if result_code == EXIT_OK:
        call_matrix = []

    fallback_phase_started = time.perf_counter()
    for idx, spec in enumerate(call_matrix, start=1):
        if result_code == EXIT_FAILED:
            break

        attempt_started = time.perf_counter()
        attempt = {
            "index": idx,
            "pid": current_pid,
            "symbol": spec.symbol,
            "abi": spec.abi,
            "signature": spec.signature,
            "arg_encoding": spec.arg_encoding,
            "status": "pending",
            "error": None,
            "return_value": None,
            "resolved_address": None,
            "output_exists": False,
            "output_size": 0,
            "detected_ext": None,
            "symbol_wait_hit": False,
            "symbols_count": 0,
            "resolved_symbol": None,
            "env": None,
            "start_detail": start_detail,
            "restart_attempts": guard.restart_attempts,
            "consecutive_closes": guard.consecutive_closes,
            "attach_load_sec": 0.0,
            "symbol_wait_sec": 0.0,
            "call_sec": 0.0,
            "artifact_wait_sec": 0.0,
            "attempt_total_sec": 0.0,
        }

        session = None
        script = None
        try:
            if raw_output.exists():
                raw_output.unlink()

            if current_pid is None or not process_exists(device, current_pid):
                on_runtime_exit("missing_before_attempt", hard_stop=True)
                attempt["status"] = "process_unavailable"
                attempt["pid"] = None
                fallback_attempts.append(attempt)
                break

            attach_load_started = time.perf_counter()
            session = device.attach(current_pid)
            script = session.create_script(agent_source)
            script.load()
            attempt_attach_load_sec = round(time.perf_counter() - attach_load_started, 6)
            attempt["attach_load_sec"] = attempt_attach_load_sec
            timing["attach_load_total_sec"] += attempt_attach_load_sec

            attempt["env"] = script.exports_sync.getenv()
            symbol_wait_started = time.perf_counter()
            wait_hit, symbol_snapshot, resolved_symbol = wait_for_symbol(script, spec.symbol, DEFAULT_SYMBOL_WAIT_SEC)
            attempt_symbol_wait_sec = round(time.perf_counter() - symbol_wait_started, 6)
            attempt["symbol_wait_sec"] = attempt_symbol_wait_sec
            timing["symbol_wait_total_sec"] += attempt_symbol_wait_sec
            attempt["symbol_wait_hit"] = wait_hit
            attempt["resolved_symbol"] = resolved_symbol
            symbols = symbol_snapshot.get("symbols", [])
            attempt["symbols_count"] = len(symbols)
            if not wait_hit:
                attempt["status"] = "symbol_not_found"
                fallback_attempts.append(attempt)
                alive_now = process_exists(device, current_pid)
                if alive_now:
                    guard.mark_stable("symbol_not_found_but_process_alive")
                else:
                    on_runtime_exit("exited_after_symbol_wait", hard_stop=True)
                    break
                continue

            before_audio = snapshot_audio_files(export_base_dir)
            payload = {
                "symbol": resolved_symbol or spec.symbol,
                "abi": spec.abi,
                "argLayout": build_default_arg_layout(spec.arg_encoding),
                "inputPath": str(call_input_path),
                "outputPath": str(raw_output.resolve()),
                "flags": int((recovered_candidates[0].get("flags_hint", 4) if recovered_candidates else 4) or 4),
                "symbolHints": symbol_hints,
                "layoutVariant": "msvc24",
            }
            t0 = time.time()
            call_res, call_err = call_export_recovered_with_timeout(
                script,
                payload,
                timeout_sec=max(4.0, min(30.0, effective_wait_sec + 2.0)),
            )
            if call_res is None:
                call_res = {
                    "ok": False,
                    "error": call_err or "call_timeout",
                    "returnValue": None,
                    "resolvedAddress": None,
                    "resolvedSymbol": None,
                    "outputString": "",
                }
            elapsed = time.time() - t0
            attempt["elapsed_sec"] = round(elapsed, 3)
            attempt["call_sec"] = round(elapsed, 6)
            timing["call_total_sec"] += float(attempt["call_sec"])
            attempt["return_value"] = call_res.get("returnValue")
            attempt["resolved_address"] = call_res.get("resolvedAddress")
            if call_res.get("resolvedSymbol"):
                attempt["resolved_symbol"] = call_res.get("resolvedSymbol")
            attempt["output_string"] = call_res.get("outputString")
            if not call_res.get("ok"):
                err_text = str(call_res.get("error") or "")
                attempt["status"] = "call_timeout" if "call_timeout" in err_text else "call_failed"
                attempt["error"] = err_text
            else:
                attempt["status"] = "call_ok"
                attempt["error"] = None

            rv = call_res.get("returnValue")
            wait_budget = effective_wait_sec
            if rv not in (0, "0", None):
                wait_budget = min(effective_wait_sec, 2.0)
            if rv in (0, "0", None) and (call_res.get("outputString") or "").strip() and wait_budget < 6.0:
                wait_budget = 6.0
            artifact_wait_started = time.perf_counter()
            artifact_path = wait_for_artifacts(
                f"fallback:{idx}:{spec.symbol}:{spec.abi}",
                output_string=(call_res.get("outputString") or "").strip(),
                before_audio=before_audio,
                max_wait_sec=wait_budget,
            )
            attempt_artifact_wait_sec = round(time.perf_counter() - artifact_wait_started, 6)
            attempt["artifact_wait_sec"] = attempt_artifact_wait_sec
            timing["artifact_wait_total_sec"] += attempt_artifact_wait_sec
            attempt["output_exists"] = raw_output.exists()
            attempt["output_size"] = raw_output.stat().st_size if raw_output.exists() else 0
            ext = detect_audio_ext(raw_output) if raw_output.exists() else "bin"
            attempt["detected_ext"] = ext
            if artifact_path is not None and artifact_path != raw_output and artifact_path.exists():
                attempt["relocated_output_path"] = str(artifact_path)

            alive_now = process_exists(device, current_pid)
            if alive_now:
                guard.mark_stable("attempt_completed_alive")
            else:
                on_runtime_exit("exited_after_attempt", hard_stop=True)

            if raw_output.exists() and attempt["output_size"] > 4096 and ext != "bin":
                final_path = next_output_name(final_output_dir, base_name, ext)
                raw_output.replace(final_path)
                final_output = {
                    "path": str(final_path),
                    "ext": ext,
                    "size": final_path.stat().st_size,
                }
                result_code = EXIT_OK
                result_reason = "success"
                fallback_attempts.append(attempt)
                break
            if attempt.get("relocated_output_path"):
                moved_path = pathlib.Path(str(attempt["relocated_output_path"]))
                if moved_path.exists():
                    moved_size = moved_path.stat().st_size
                    moved_ext = detect_audio_ext(moved_path)
                    if moved_size > 4096 and moved_ext != "bin":
                        final_output = {
                            "path": str(moved_path),
                            "ext": moved_ext,
                            "size": moved_size,
                        }
                        result_code = EXIT_OK
                        result_reason = "success"
                        fallback_attempts.append(attempt)
                        break

            fallback_attempts.append(attempt)

            if result_code == EXIT_FAILED:
                break

        except Exception as exc:
            attempt["status"] = "exception"
            attempt["error"] = f"{type(exc).__name__}: {exc}"
            attempt["traceback"] = traceback.format_exc().splitlines()
            if "VirtualAllocEx returned 0x00000005" in attempt["error"]:
                attempt["status"] = "injection_blocked"
            fallback_attempts.append(attempt)

            alive_now = current_pid is not None and process_exists(device, current_pid)
            if not alive_now:
                on_runtime_exit("exception_with_process_exit", hard_stop=True)
            else:
                guard.mark_stable("exception_but_process_alive")

            if guard.should_stop() or stop_reason:
                stop_reason = guard.stop_reason
                result_code = EXIT_FAILED
                result_reason = stop_reason or "script_induced_crash_suspected"
                break
            if attempt["status"] == "injection_blocked":
                result_code = EXIT_NOT_FOUND
                result_reason = "export_rejected_or_unsupported"
                break
        finally:
            attempt["attempt_total_sec"] = round(time.perf_counter() - attempt_started, 6)
            try:
                if script is not None:
                    script.unload()
            except Exception:
                pass
            try:
                if session is not None:
                    session.detach()
            except Exception:
                pass

    timing["fallback_phase_sec"] += round(time.perf_counter() - fallback_phase_started, 6)

    terminal_reasons = {
        "target_pid_not_running",
        "target_process_unavailable",
        "script_induced_crash_suspected",
        "restart_limit_exceeded",
        "target_process_not_found_or_not_startable",
    }
    if result_code != EXIT_OK and not (result_code == EXIT_FAILED and result_reason in terminal_reasons):
        status_list: list[str] = []
        if recovered_attempt and recovered_attempt.get("status"):
            status_list.append(str(recovered_attempt.get("status")))
        status_list.extend(str(x.get("status")) for x in fallback_attempts if x.get("status"))

        if guard.should_stop():
            stop_reason = guard.stop_reason
            result_code = EXIT_FAILED
            result_reason = stop_reason or "script_induced_crash_suspected"
        elif "call_ok" in status_list:
            result_code = EXIT_NOT_FOUND
            result_reason = "export_timeout_no_artifact"
        elif "call_timeout" in status_list:
            result_code = EXIT_NOT_FOUND
            result_reason = "export_timeout_no_artifact"
        elif status_list and all(x == "symbol_not_found" for x in status_list):
            result_code = EXIT_NOT_FOUND
            result_reason = "symbol_not_found"
        elif "call_failed" in status_list or "injection_blocked" in status_list:
            result_code = EXIT_NOT_FOUND
            result_reason = "export_rejected_or_unsupported"
        elif "exception" in status_list:
            result_code = EXIT_FAILED
            result_reason = "injection_failed"
        else:
            result_code = EXIT_NOT_FOUND
            result_reason = "export_timeout_no_artifact"

    if result_code == EXIT_FAILED:
        wer_summary = query_wer(process_name, window_minutes=30, limit=6)

    post_run_bin_new_files = detect_new_bin_audio_files(bin_snapshot_before, bin_dir, run_start_ts)
    if copied_input_path is not None:
        try:
            copied_input_path.unlink(missing_ok=True)
        except Exception:
            pass

    timing["total_sec"] = round(time.perf_counter() - impl_started_perf, 6)

    report = {
        "timestamp": started.isoformat(),
        "input": str(input_path.resolve()),
        "call_input_path": str(call_input_path),
        "output_dir": str(output_dir),
        "report_dir": str(report_dir),
        "final_output_dir": str(final_output_dir),
        "raw_output_path": str(raw_output),
        "exe_path": exe_path_text,
        "candidate_report": str(candidate_report),
        "symbol_map_report": str(symbol_map_report),
        "signature_source": str(signature_file) if recovered_candidates else None,
        "layout_inference": recovered_candidates[0].get("arg_layout") if recovered_candidates else None,
        "effective_wait_sec": effective_wait_sec,
        "target_pid": current_pid,
        "target_process_name": process_name,
        "preferred_pid": preferred_pid,
        "result_code": result_code,
        "result_reason": result_reason,
        "stop_reason": stop_reason or guard.stop_reason,
        "bin_snapshot_before": bin_snapshot_before,
        "relocated_from_bin": relocated_from_bin,
        "relocated_files": relocated_files,
        "post_run_bin_new_files": post_run_bin_new_files,
        "final_output": final_output,
        "recovered_attempt": recovered_attempt,
        "fallback_attempts": fallback_attempts,
        "wer_summary": wer_summary,
        "guard_summary": guard.summary(),
        "timing": timing,
        "report_json_path": str(json_report_path),
        "report_txt_path": str(text_report_path),
    }

    report_write_started = time.perf_counter()
    json_report_path.write_text(to_json(report) + "\n", encoding="utf-8")
    write_text_report(text_report_path, report)
    timing["report_write_sec"] = round(time.perf_counter() - report_write_started, 6)
    timing["total_sec"] = round(time.perf_counter() - impl_started_perf, 6)
    report["timing"] = timing
    json_report_path.write_text(to_json(report) + "\n", encoding="utf-8")
    write_text_report(text_report_path, report)

    if verbose:
        safe_console_print(f"[kwm_decrypt_mvp] result_code={result_code} reason={result_reason}")
        safe_console_print(f"[kwm_decrypt_mvp] stop_reason={report['stop_reason']}")
        safe_console_print(f"[kwm_decrypt_mvp] timing={to_json(report.get('timing') or {})}")
        safe_console_print(f"[kwm_decrypt_mvp] report_json={json_report_path}")
        safe_console_print(f"[kwm_decrypt_mvp] report_txt={text_report_path}")
        if final_output:
            safe_console_print(f"[kwm_decrypt_mvp] output={final_output['path']}")

    return result_code, report


def decrypt_one_file(
    input_path: str | pathlib.Path,
    *,
    output_dir: str | pathlib.Path = DEFAULT_OUTPUT_DIR,
    report_dir: str | pathlib.Path | None = None,
    final_output_dir: str | pathlib.Path = DEFAULT_FINAL_OUTPUT_DIR,
    exe_path: str | pathlib.Path = DEFAULT_EXE_PATH,
    candidate_report: str | pathlib.Path = DEFAULT_CANDIDATE_REPORT,
    symbol_map_report: str | pathlib.Path = DEFAULT_SYMBOL_MAP_REPORT,
    signature_file: str | pathlib.Path = DEFAULT_SIGNATURE_FILE,
    process_name: str = DEFAULT_PROCESS_NAME,
    pid: int | None = None,
    max_crash: int = DEFAULT_MAX_CRASH,
    max_consecutive_closes: int = DEFAULT_MAX_CONSECUTIVE_CLOSES,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    verbose: bool = True,
) -> dict[str, Any]:
    args = argparse.Namespace(
        input=str(input_path),
        output_dir=str(output_dir),
        report_dir=str(report_dir) if report_dir else None,
        final_output_dir=str(final_output_dir),
        exe_path=str(exe_path),
        candidate_report=str(candidate_report),
        symbol_map_report=str(symbol_map_report),
        signature_file=str(signature_file),
        process_name=str(process_name),
        pid=int(pid) if pid is not None else None,
        max_crash=int(max_crash),
        max_consecutive_closes=int(max_consecutive_closes),
        timeout_sec=int(timeout_sec),
    )
    _, report = _decrypt_impl(args, verbose=verbose)
    return report


def main() -> int:
    args = make_parser().parse_args()
    code, _ = _decrypt_impl(args, verbose=True)
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())

