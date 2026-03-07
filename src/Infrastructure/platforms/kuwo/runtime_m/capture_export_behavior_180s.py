"""Capture 180-second export behavior with full core-module call traces."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time
import traceback
from typing import Any

import frida

PROCESS_GUARD_PATH = pathlib.Path(__file__).resolve().parent
if str(PROCESS_GUARD_PATH) not in sys.path:
    sys.path.insert(0, str(PROCESS_GUARD_PATH))

from process_guard import ProcessGuard


DEFAULT_PROCESS = "kwmusic.exe"
DEFAULT_DURATION = 180
DEFAULT_REPORT_DIR = pathlib.Path("kuwo/m/out")
DEFAULT_MAX_RESTART_TOTAL = 3
DEFAULT_MAX_CONSECUTIVE_CLOSES = 3

EXIT_OK = 0
EXIT_NOT_FOUND = 2
EXIT_FAILED = 3

BASE_DIR = pathlib.Path(__file__).resolve().parent
TRACE_SCRIPT_PATH = BASE_DIR / "trace_all_calls_export.js"


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def normalize_name(name: str) -> str:
    value = (name or "").strip().lower()
    if value.endswith(".exe"):
        value = value[:-4]
    return value


def find_process_by_name(device: frida.core.Device, process_name: str):
    target = normalize_name(process_name)
    matches = [p for p in device.enumerate_processes() if normalize_name(p.name) == target]
    if not matches:
        return None
    return sorted(matches, key=lambda x: x.pid)[-1]


def process_exists(device: frida.core.Device, pid: int) -> bool:
    for proc in device.enumerate_processes():
        if proc.pid == pid:
            return True
    return False


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture 180-second export behavior call traces.")
    parser.add_argument("--process", default=DEFAULT_PROCESS, help="Target process name")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION, help="Capture duration in seconds")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="Report output directory")
    parser.add_argument("--max-restart-total", type=int, default=DEFAULT_MAX_RESTART_TOTAL, help="Guard restart limit")
    parser.add_argument(
        "--max-consecutive-closes",
        type=int,
        default=DEFAULT_MAX_CONSECUTIVE_CLOSES,
        help="Stop after this many consecutive script-observed closes",
    )
    return parser


def main() -> int:
    args = make_parser().parse_args()
    started = dt.datetime.now().astimezone()
    report_dir = pathlib.Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)

    trace_jsonl = report_dir / "call_trace_180s.jsonl"
    summary_json = report_dir / "call_summary_180s.json"
    signature_json = report_dir / "export_signature_180s.json"
    report_json = report_dir / "capture_export_behavior_180s_report.json"

    guard = ProcessGuard(
        max_restart_total=args.max_restart_total,
        max_consecutive_closes=args.max_consecutive_closes,
    )

    result_code = EXIT_FAILED
    result_reason = "runtime_failure"
    stats_snapshot: dict[str, Any] = {}
    event_total = 0
    function_counter: dict[str, int] = {}
    signature_samples: list[dict[str, Any]] = []
    pid = 0
    interrupted = False

    device = frida.get_local_device()
    proc = find_process_by_name(device, args.process)
    if proc is None:
        result_code = EXIT_FAILED
        result_reason = "target_process_not_found"
        payload = {
            "timestamp": started.isoformat(),
            "result_code": result_code,
            "result_reason": result_reason,
            "target_process": args.process,
            "guard_summary": guard.summary(),
        }
        report_json.write_text(to_json(payload) + "\n", encoding="utf-8")
        print(f"[capture_export_behavior_180s] {result_reason}")
        return result_code

    if not TRACE_SCRIPT_PATH.exists():
        result_code = EXIT_FAILED
        result_reason = "trace_script_missing"
        payload = {
            "timestamp": started.isoformat(),
            "result_code": result_code,
            "result_reason": result_reason,
            "trace_script": str(TRACE_SCRIPT_PATH),
            "guard_summary": guard.summary(),
        }
        report_json.write_text(to_json(payload) + "\n", encoding="utf-8")
        print(f"[capture_export_behavior_180s] {result_reason}")
        return result_code

    pid = int(proc.pid)
    guard.observe_start(pid, start_time="existing_process")
    trace_jsonl.write_text("", encoding="utf-8")

    session = None
    script = None
    def consume_batch(batch: dict[str, Any]) -> None:
        nonlocal event_total, stats_snapshot, signature_samples, function_counter
        if not isinstance(batch, dict):
            return
        events = batch.get("events", [])
        samples = batch.get("samples", [])
        stats_snapshot = batch.get("stats", stats_snapshot)

        if events:
            with trace_jsonl.open("a", encoding="utf-8") as fw:
                for ev in events:
                    fw.write(json.dumps(ev, ensure_ascii=False) + "\n")
                    event_total += 1
                    module = (ev.get("module") or "?").strip()
                    symbol = (ev.get("symbol") or ev.get("address") or "?").strip()
                    key = f"{module}!{symbol}"
                    function_counter[key] = function_counter.get(key, 0) + 1

        if samples:
            signature_samples.extend(samples)

    try:
        source = TRACE_SCRIPT_PATH.read_text(encoding="utf-8")
        session = device.attach(pid)
        script = session.create_script(source)
        script.load()

        print(
            f"[capture_export_behavior_180s] attached pid={pid}, duration={args.duration}s; "
            "please trigger export/convert behavior now"
        )

        deadline = time.time() + max(5, int(args.duration))
        while time.time() < deadline:
            time.sleep(1.0)
            consume_batch(script.exports_sync.flush())

            if not process_exists(device, pid):
                guard.observe_exit(pid, "process_exited_during_capture", by_script=False)
                result_code = EXIT_FAILED
                result_reason = "target_process_exited"
                break

            if guard.should_stop():
                result_code = EXIT_FAILED
                result_reason = guard.stop_reason or "script_induced_crash_suspected"
                break

    except KeyboardInterrupt:
        interrupted = True
        result_reason = "user_interrupted"
        print("[capture_export_behavior_180s] user interrupted (Ctrl+C), flushing collected data...")

    except Exception as exc:
        result_code = EXIT_FAILED
        result_reason = f"exception:{type(exc).__name__}"
        print(f"[capture_export_behavior_180s] error: {exc}")
        traceback.print_exc()
    finally:
        if script is not None:
            try:
                consume_batch(script.exports_sync.flush())
            except Exception:
                pass

        if script is not None:
            try:
                script.unload()
            except Exception:
                pass
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass

    if result_reason in {"runtime_failure", "user_interrupted"}:
        unique_functions = len(function_counter)
        if unique_functions <= 0:
            result_code = EXIT_NOT_FOUND
            result_reason = "no_function_calls_captured" if not interrupted else "user_interrupted_no_data"
        elif len(signature_samples) <= 0:
            result_code = EXIT_NOT_FOUND
            result_reason = "no_export_behavior_captured" if not interrupted else "user_interrupted_no_export_samples"
        else:
            result_code = EXIT_OK
            result_reason = "capture_ok" if not interrupted else "user_interrupted_saved"

    top_functions = sorted(function_counter.items(), key=lambda x: x[1], reverse=True)[:300]
    summary_payload = {
        "timestamp": started.isoformat(),
        "process": args.process,
        "pid": pid,
        "duration_sec": int(args.duration),
        "interrupted": interrupted,
        "total_calls": event_total,
        "unique_functions": len(function_counter),
        "top_functions": [{"name": name, "count": count} for name, count in top_functions],
        "stats_snapshot": stats_snapshot,
    }
    summary_json.write_text(to_json(summary_payload) + "\n", encoding="utf-8")
    signature_json.write_text(to_json(signature_samples) + "\n", encoding="utf-8")

    report_payload = {
        "timestamp": started.isoformat(),
        "target_process": args.process,
        "pid": pid,
        "duration_sec": int(args.duration),
        "interrupted": interrupted,
        "result_code": result_code,
        "result_reason": result_reason,
        "files": {
            "trace_jsonl": str(trace_jsonl),
            "summary_json": str(summary_json),
            "signature_json": str(signature_json),
        },
        "event_total": event_total,
        "signature_sample_count": len(signature_samples),
        "stats_snapshot": stats_snapshot,
        "guard_summary": guard.summary(),
    }
    report_json.write_text(to_json(report_payload) + "\n", encoding="utf-8")

    print(f"[capture_export_behavior_180s] result_code={result_code} reason={result_reason}")
    print(f"[capture_export_behavior_180s] trace={trace_jsonl}")
    print(f"[capture_export_behavior_180s] summary={summary_json}")
    print(f"[capture_export_behavior_180s] signatures={signature_json}")
    print(f"[capture_export_behavior_180s] report={report_json}")
    return result_code


if __name__ == "__main__":
    raise SystemExit(main())
