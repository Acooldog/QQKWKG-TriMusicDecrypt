"""Find probable KWMusic decrypt functions by dynamic stack aggregation.

This script:
1. Attaches to kwmusic.exe (or spawns it).
2. Loads stack_probe.js in sampled mode.
3. Restricts trace to .kwm paths under target music directory.
4. Ranks high-frequency frames from core modules as decrypt candidates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import time
import traceback
from typing import Any

import frida
from process_guard import ProcessGuard


DEFAULT_PROCESS = "kwmusic.exe"
DEFAULT_MUSIC_DIR = r"C:\Users\01080\Documents\Frontier Developments\Planet Coaster\UserMusic\MusicPack"
DEFAULT_EXE_PATH = r"M:\kuwo\kuwomusic\9.5.0.0_W1\bin\kwmusic.exe"
DEFAULT_DURATION = 120
DEFAULT_TOPN = 80
DEFAULT_SAMPLE_RATE = 0.35
DEFAULT_REPORT_EVERY = 80
DEFAULT_STACK_FRAMES = 18
DEFAULT_MAX_RESTART_TOTAL = 3
DEFAULT_MAX_CONSECUTIVE_CLOSES = 3

EXIT_OK = 0
EXIT_NO_CANDIDATE = 2
EXIT_FAILED = 3

BASE_DIR = pathlib.Path(__file__).resolve().parent
STACK_PROBE_PATH = BASE_DIR.parent / "stack_probe.js"
DEFAULT_TEXT_REPORT = BASE_DIR / "decrypt_candidates_latest.txt"
DEFAULT_JSON_REPORT = BASE_DIR / "decrypt_candidates_latest.json"

INTEREST_MODULES = {
    "kwmusic.exe",
    "kwmusicdll.dll",
    "kwmusiccore.dll",
    "kwmodlocalmusic.dll",
}

KEYWORD_HINTS = [
    "dec",
    "decrypt",
    "codec",
    "audio",
    "media",
    "stream",
    "open",
    "read",
]


def normalize_name(name: str) -> str:
    return (name or "").strip().lower()


def find_target_process(device: frida.core.Device, process_name: str):
    target = normalize_name(process_name)
    matches = [p for p in device.enumerate_processes() if normalize_name(p.name) == target]
    if not matches:
        return None
    return sorted(matches, key=lambda x: x.pid)[-1]


def find_target_processes(device: frida.core.Device, process_name: str) -> list[Any]:
    target = normalize_name(process_name)
    return sorted([p for p in device.enumerate_processes() if normalize_name(p.name) == target], key=lambda x: x.pid)


def process_exists(device: frida.core.Device, pid: int) -> bool:
    for proc in device.enumerate_processes():
        if proc.pid == pid:
            return True
    return False


def build_path_regex(music_dir: pathlib.Path) -> str:
    escaped_full = re.escape(str(music_dir))
    escaped_name = re.escape(music_dir.name)
    return rf"((\\\\\?\\)?{escaped_full}|{escaped_name}).*\.kwm$"


def build_agent_source(
    pid: int,
    process_name: str,
    path_regex: str,
    sample_rate: float,
    report_every: int,
    stack_frames: int,
) -> str:
    source = STACK_PROBE_PATH.read_text(encoding="utf-8")
    prefix = (
        f"globalThis.__MODULE_SCAN_TARGET_PID__ = {pid};\n"
        f"globalThis.__MODULE_SCAN_TARGET_PROCESS__ = {json.dumps(process_name)};\n"
        f"globalThis.__STACK_PROBE_SAMPLE_RATE__ = {sample_rate};\n"
        "globalThis.__STACK_PROBE_BACKTRACER__ = 'fuzzy';\n"
        f"globalThis.__STACK_PROBE_REPORT_EVERY__ = {int(report_every)};\n"
        f"globalThis.__STACK_PROBE_STACK_FRAMES__ = {int(stack_frames)};\n"
        f"globalThis.__STACK_PROBE_PATH_REGEX__ = {json.dumps(path_regex)};\n"
    )
    return prefix + source


def parse_frame_module(frame_name: str) -> str:
    if "!" not in frame_name:
        return "?"
    return frame_name.split("!", 1)[0]


def rank_candidates(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for item in frames:
        name = str(item.get("name", ""))
        count = int(item.get("count", 0))
        module = normalize_name(parse_frame_module(name))
        if module not in INTEREST_MODULES:
            continue

        score = count
        low = normalize_name(name)
        if any(k in low for k in KEYWORD_HINTS):
            score += 30
        if re.search(r"!0x[0-9a-f]+$", low):
            score += 15
        if module == "kwmusicdll.dll":
            score += 20

        ranked.append(
            {
                "score": score,
                "count": count,
                "frame": name,
                "module": module,
                "reason": "high-frequency frame in core module",
            }
        )

    ranked.sort(key=lambda x: (-x["score"], -x["count"], x["frame"]))
    return ranked


def write_text_report(path: pathlib.Path, payload: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("KWMusic Decrypt Candidate Report")
    lines.append("=" * 56)
    lines.append(f"timestamp: {payload['timestamp']}")
    lines.append(f"process: {payload['process_name']} pid={payload['pid']}")
    lines.append(f"music_dir: {payload['music_dir']}")
    lines.append(f"path_regex: {payload['path_regex']}")
    lines.append(f"duration_sec: {payload['duration_sec']}")
    lines.append(f"stop_reason: {payload.get('stop_reason')}")
    lines.append("")
    lines.append("kwm_files:")
    for item in payload["kwm_files"][:20]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("stats:")
    lines.append(json.dumps(payload["stats"], ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("guard_summary:")
    lines.append(json.dumps(payload.get("guard_summary", {}), ensure_ascii=False, indent=2))
    lines.append("")
    lines.append("top_modules:")
    for item in payload["top_modules"][:30]:
        lines.append(f"- {item['name']} -> {item['count']}")
    lines.append("")
    lines.append("top_frames:")
    for item in payload["top_frames"][:40]:
        lines.append(f"- {item['name']} -> {item['count']}")
    lines.append("")
    lines.append("suspected_decrypt_candidates:")
    for item in payload["candidates"][:30]:
        lines.append(f"- score={item['score']} count={item['count']} frame={item['frame']}")
    lines.append("")
    lines.append("note:")
    lines.append("- These are dynamic stack hotspots ranked by frequency/module/keyword hints.")
    lines.append("- If ioEvents/sampleEvents is low, play a .kwm track in KWMusic and rerun.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find KWMusic decrypt candidates from dynamic stack traces.")
    parser.add_argument("--process", default=DEFAULT_PROCESS, help="Target process name")
    parser.add_argument("--exe-path", default=DEFAULT_EXE_PATH, help="Executable path used by --spawn")
    parser.add_argument("--music-dir", default=DEFAULT_MUSIC_DIR, help="Directory containing .kwm files")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION, help="Capture duration in seconds")
    parser.add_argument("--topn", type=int, default=DEFAULT_TOPN, help="Top N frames/modules")
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE, help="stack_probe sample rate")
    parser.add_argument("--report-every", type=int, default=DEFAULT_REPORT_EVERY, help="stack_probe report interval")
    parser.add_argument("--stack-frames", type=int, default=DEFAULT_STACK_FRAMES, help="stack_probe max frames")
    parser.add_argument("--max-restart-total", type=int, default=DEFAULT_MAX_RESTART_TOTAL, help="Max restart attempts")
    parser.add_argument(
        "--max-consecutive-closes",
        type=int,
        default=DEFAULT_MAX_CONSECUTIVE_CLOSES,
        help="Stop after this many consecutive script-observed closes",
    )
    parser.add_argument("--spawn", action="store_true", help="Spawn process and capture startup phase")
    parser.add_argument("--kill-existing", action="store_true", help="Kill existing target process before --spawn")
    parser.add_argument("--text-report", default=str(DEFAULT_TEXT_REPORT), help="Text report path")
    parser.add_argument("--json-report", default=str(DEFAULT_JSON_REPORT), help="JSON report path")
    return parser


def main() -> int:
    args = make_parser().parse_args()
    started = dt.datetime.now().astimezone()
    music_dir = pathlib.Path(args.music_dir)
    exe_path = pathlib.Path(args.exe_path)
    text_report = pathlib.Path(args.text_report).resolve()
    json_report = pathlib.Path(args.json_report).resolve()

    if not music_dir.exists():
        print(f"[find_kwm_decrypt_candidates] music dir not found: {music_dir}")
        return EXIT_FAILED

    kwm_files = sorted(music_dir.glob("*.kwm"))
    if not kwm_files:
        print(f"[find_kwm_decrypt_candidates] no .kwm files found in: {music_dir}")
        return EXIT_FAILED

    path_regex = build_path_regex(music_dir)
    device = frida.get_local_device()
    guard = ProcessGuard(
        max_restart_total=args.max_restart_total,
        max_consecutive_closes=args.max_consecutive_closes,
    )

    proc_name: str
    proc_pid: int
    spawned_pid: int | None = None

    if args.spawn:
        if args.kill_existing:
            for p in find_target_processes(device, args.process):
                try:
                    device.kill(p.pid)
                    guard.observe_exit(p.pid, "kill_existing_before_spawn", by_script=True)
                except Exception:
                    pass
            if guard.should_stop():
                payload = {
                    "timestamp": started.isoformat(),
                    "process_name": args.process,
                    "pid": None,
                    "music_dir": str(music_dir),
                    "path_regex": path_regex,
                    "duration_sec": int(args.duration),
                    "kwm_files": [str(p) for p in kwm_files],
                    "stats": {},
                    "top_modules": [],
                    "top_frames": [],
                    "candidates": [],
                    "messages_preview": [],
                    "stop_reason": guard.stop_reason,
                    "guard_summary": guard.summary(),
                }
                write_text_report(text_report, payload)
                json_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[find_kwm_decrypt_candidates] stopped by guard: {guard.stop_reason}")
                return EXIT_FAILED
        if not exe_path.exists():
            print(f"[find_kwm_decrypt_candidates] exe path not found: {exe_path}")
            return EXIT_FAILED
        guard.register_restart_attempt("spawn_capture_process")
        if guard.should_stop():
            payload = {
                "timestamp": started.isoformat(),
                "process_name": args.process,
                "pid": None,
                "music_dir": str(music_dir),
                "path_regex": path_regex,
                "duration_sec": int(args.duration),
                "kwm_files": [str(p) for p in kwm_files],
                "stats": {},
                "top_modules": [],
                "top_frames": [],
                "candidates": [],
                "messages_preview": [],
                "stop_reason": guard.stop_reason,
                "guard_summary": guard.summary(),
            }
            write_text_report(text_report, payload)
            json_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[find_kwm_decrypt_candidates] stopped by guard: {guard.stop_reason}")
            return EXIT_FAILED
        spawned_pid = int(device.spawn([str(exe_path)]))
        proc_name = args.process
        proc_pid = spawned_pid
        guard.observe_start(proc_pid, start_time="spawn_capture_process")
    else:
        proc = find_target_process(device, args.process)
        if proc is None:
            print(f"[find_kwm_decrypt_candidates] process not found: {args.process}")
            return EXIT_FAILED
        proc_name = proc.name
        proc_pid = int(proc.pid)
        guard.observe_start(proc_pid, start_time="existing_process")

    session = None
    script = None
    messages: list[dict[str, Any]] = []
    try:
        source = build_agent_source(
            pid=proc_pid,
            process_name=proc_name,
            path_regex=path_regex,
            sample_rate=float(args.sample_rate),
            report_every=int(args.report_every),
            stack_frames=int(args.stack_frames),
        )

        session = device.attach(proc_pid)
        script = session.create_script(source)

        def on_message(message, _data):
            if len(messages) < 200:
                messages.append(message)

        script.on("message", on_message)
        script.load()
        if spawned_pid is not None:
            device.resume(spawned_pid)

        print(
            f"[find_kwm_decrypt_candidates] attached pid={proc_pid}, capturing {args.duration}s; "
            "please play a .kwm file in KWMusic now"
        )
        time.sleep(max(3, int(args.duration)))

        stats = script.exports_sync.getstats()
        top = script.exports_sync.gettop(int(args.topn))

        top_modules = top.get("modules", [])
        top_frames = top.get("frames", [])
        candidates = rank_candidates(top_frames)

        if process_exists(device, proc_pid):
            guard.mark_stable("capture_completed_alive")
        else:
            guard.observe_exit(proc_pid, "capture_process_exit", by_script=True)

        payload = {
            "timestamp": started.isoformat(),
            "process_name": proc_name,
            "pid": proc_pid,
            "music_dir": str(music_dir),
            "path_regex": path_regex,
            "duration_sec": int(args.duration),
            "kwm_files": [str(p) for p in kwm_files],
            "stats": stats,
            "top_modules": top_modules,
            "top_frames": top_frames,
            "candidates": candidates,
            "messages_preview": messages[:80],
            "stop_reason": guard.stop_reason,
            "guard_summary": guard.summary(),
        }

        write_text_report(text_report, payload)
        json_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        io_events = int((stats or {}).get("ioEvents", 0))
        sampled_events = int((stats or {}).get("sampledEvents", 0))
        print(f"[find_kwm_decrypt_candidates] ioEvents={io_events} sampledEvents={sampled_events}")
        print(f"[find_kwm_decrypt_candidates] candidates={len(candidates)}")
        print(f"[find_kwm_decrypt_candidates] text_report={text_report}")
        print(f"[find_kwm_decrypt_candidates] json_report={json_report}")

        if guard.should_stop():
            return EXIT_FAILED
        if io_events <= 0 or sampled_events <= 0 or len(candidates) == 0:
            return EXIT_NO_CANDIDATE
        return EXIT_OK

    except Exception as exc:
        if proc_pid:
            if not process_exists(device, proc_pid):
                guard.observe_exit(proc_pid, "exception_process_exit", by_script=True)
        print(f"[find_kwm_decrypt_candidates] failed: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return EXIT_FAILED
    finally:
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


if __name__ == "__main__":
    raise SystemExit(main())
