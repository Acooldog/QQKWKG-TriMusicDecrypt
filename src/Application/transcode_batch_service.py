from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import pathlib
import threading
import time
from typing import Any, Callable, Iterable

from src.Infrastructure.transcoder import transcode_file

ALL_SOURCE_FORMAT = "全部"
TRANSCODE_SOURCE_FORMATS: tuple[str, ...] = (
    ALL_SOURCE_FORMAT,
    "flac",
    "m4a",
    "mp3",
    "wav",
    "ogg",
    "aac",
    "ape",
)
TRANSCODE_TARGET_FORMATS: tuple[str, ...] = ("flac", "m4a", "mp3", "wav")
TRANSCODE_SAMPLE_RATE_OPTIONS: tuple[int, ...] = (22050, 32000, 44100, 48000, 88200, 96000)
TRANSCODE_BITRATE_OPTIONS: tuple[int, ...] = (96, 128, 160, 192, 256, 320)
SUPPORTED_INPUT_EXTENSIONS = {item for item in TRANSCODE_SOURCE_FORMATS if item != ALL_SOURCE_FORMAT}
EventSink = Callable[[str, dict[str, Any]], None]


@dataclass(slots=True)
class TranscodeRule:
    source_format: str
    target_format: str
    sample_rate_hz: int | None = None
    bitrate_kbps: int | None = None


@dataclass(slots=True)
class TranscodeJob:
    source_root: pathlib.Path
    input_path: pathlib.Path
    relative_path: pathlib.Path
    target_format: str
    output_path: pathlib.Path
    sample_rate_hz: int | None = None
    bitrate_kbps: int | None = None

    @property
    def source_format(self) -> str:
        return self.input_path.suffix.lower().lstrip(".")


@dataclass(slots=True)
class TranscodeBatchResult:
    total_jobs: int
    success_count: int
    failed_count: int
    skipped_count: int
    results: list[dict[str, Any]]
    elapsed_sec: float


def normalize_source_format(value: str) -> str:
    raw = str(value or ALL_SOURCE_FORMAT).strip().lower().lstrip(".")
    if raw in {"all", ALL_SOURCE_FORMAT.lower()}:
        return ALL_SOURCE_FORMAT
    if raw not in SUPPORTED_INPUT_EXTENSIONS:
        raise ValueError(f"unsupported source format: {value}")
    return raw


def normalize_target_format(value: str) -> str:
    raw = str(value or "m4a").strip().lower().lstrip(".")
    if raw not in TRANSCODE_TARGET_FORMATS:
        raise ValueError(f"unsupported target format: {value}")
    return raw


def _normalize_optional_int(
    value: Any,
    *,
    label: str,
    allowed: tuple[int, ...],
) -> int | None:
    if value in (None, "", False):
        return None
    try:
        normalized = int(value)
    except Exception as exc:
        raise ValueError(f"invalid {label}: {value}") from exc
    if normalized <= 0:
        return None
    if normalized not in allowed:
        allowed_text = ", ".join(str(item) for item in allowed)
        raise ValueError(f"unsupported {label}: {value}; allowed={allowed_text}")
    return normalized


def normalize_sample_rate(value: Any) -> int | None:
    return _normalize_optional_int(value, label="sample rate", allowed=TRANSCODE_SAMPLE_RATE_OPTIONS)


def normalize_bitrate(value: Any) -> int | None:
    return _normalize_optional_int(value, label="bitrate", allowed=TRANSCODE_BITRATE_OPTIONS)


def normalize_rules(items: Iterable[dict[str, Any] | TranscodeRule]) -> list[TranscodeRule]:
    rules: list[TranscodeRule] = []
    for item in items:
        if isinstance(item, TranscodeRule):
            source_format = normalize_source_format(item.source_format)
            target_format = normalize_target_format(item.target_format)
            sample_rate_hz = normalize_sample_rate(item.sample_rate_hz)
            bitrate_kbps = normalize_bitrate(item.bitrate_kbps)
        else:
            source_format = normalize_source_format(str(item.get("source_format", ALL_SOURCE_FORMAT)))
            target_format = normalize_target_format(str(item.get("target_format", "m4a")))
            sample_rate_hz = normalize_sample_rate(item.get("sample_rate_hz"))
            bitrate_kbps = normalize_bitrate(item.get("bitrate_kbps"))
        rules.append(
            TranscodeRule(
                source_format=source_format,
                target_format=target_format,
                sample_rate_hz=sample_rate_hz,
                bitrate_kbps=bitrate_kbps,
            )
        )
    if not rules:
        rules.append(TranscodeRule(source_format=ALL_SOURCE_FORMAT, target_format="m4a"))
    return rules


def _iter_input_files(root: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
    if root.is_file():
        return [root]
    if recursive:
        return [path for path in root.rglob("*") if path.is_file()]
    return [path for path in root.iterdir() if path.is_file()]


def _output_base_name(input_root: pathlib.Path) -> str:
    if input_root.is_dir():
        return input_root.name or "input"
    return input_root.stem or "input"


def _rule_profile_suffix(rule: TranscodeRule) -> str:
    parts: list[str] = []
    if rule.sample_rate_hz:
        parts.append(f"{rule.sample_rate_hz}hz")
    if rule.bitrate_kbps:
        parts.append(f"{rule.bitrate_kbps}k")
    return ("." + ".".join(parts)) if parts else ""


def build_transcode_jobs(
    input_paths: Iterable[pathlib.Path],
    output_dir: pathlib.Path,
    rules: Iterable[dict[str, Any] | TranscodeRule],
    *,
    recursive: bool = True,
) -> tuple[list[TranscodeJob], list[str]]:
    normalized_rules = normalize_rules(rules)
    jobs: list[TranscodeJob] = []
    warnings: list[str] = []
    seen: set[tuple[str, str, int | None, int | None]] = set()
    output_dir = output_dir.resolve()

    for raw_root in input_paths:
        source_root = pathlib.Path(raw_root).expanduser()
        if not source_root.exists():
            warnings.append(f"输入路径不存在：{source_root}")
            continue
        files = _iter_input_files(source_root, recursive)
        if not files:
            warnings.append(f"输入路径下没有文件：{source_root}")
            continue
        root_name = _output_base_name(source_root)
        for file_path in files:
            source_format = file_path.suffix.lower().lstrip(".")
            if source_format not in SUPPORTED_INPUT_EXTENSIONS:
                continue
            matching_rules: list[TranscodeRule] = []
            for rule in normalized_rules:
                if rule.source_format == ALL_SOURCE_FORMAT or rule.source_format == source_format:
                    matching_rules.append(rule)
            if not matching_rules:
                continue
            relative_path = pathlib.Path(file_path.name)
            if source_root.is_dir():
                relative_path = file_path.relative_to(source_root)
            for rule in matching_rules:
                key = (str(file_path).lower(), rule.target_format, rule.sample_rate_hz, rule.bitrate_kbps)
                if key in seen:
                    continue
                seen.add(key)
                suffix = _rule_profile_suffix(rule)
                output_path = output_dir / root_name / relative_path.parent / f"{file_path.stem}{suffix}.{rule.target_format}"
                jobs.append(
                    TranscodeJob(
                        source_root=source_root,
                        input_path=file_path,
                        relative_path=relative_path,
                        target_format=rule.target_format,
                        output_path=output_path,
                        sample_rate_hz=rule.sample_rate_hz,
                        bitrate_kbps=rule.bitrate_kbps,
                    )
                )
    return jobs, warnings


def run_transcode_batch(
    input_paths: Iterable[pathlib.Path],
    output_dir: pathlib.Path,
    rules: Iterable[dict[str, Any] | TranscodeRule],
    *,
    recursive: bool = True,
    max_workers: int = 2,
    event_sink: EventSink | None = None,
) -> TranscodeBatchResult:
    started = time.perf_counter()
    jobs, warnings = build_transcode_jobs(input_paths, output_dir, rules, recursive=recursive)
    sink = event_sink or (lambda _event, _payload: None)
    sink(
        "plan_ready",
        {
            "total_jobs": len(jobs),
            "warnings": list(warnings),
            "output_dir": str(pathlib.Path(output_dir)),
            "worker_count": max(1, min(int(max_workers or 1), 4)),
        },
    )
    for warning in warnings:
        sink("warning", {"message": warning})
    if not jobs:
        elapsed_sec = time.perf_counter() - started
        sink(
            "batch_finished",
            {
                "total_jobs": 0,
                "success_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "elapsed_sec": round(elapsed_sec, 3),
            },
        )
        return TranscodeBatchResult(
            total_jobs=0,
            success_count=0,
            failed_count=0,
            skipped_count=0,
            results=[],
            elapsed_sec=elapsed_sec,
        )

    worker_count = max(1, min(int(max_workers or 1), 4))
    queued = len(jobs)
    running = 0
    completed = 0
    lock = threading.Lock()
    results: list[dict[str, Any]] = []

    def _run_job(job: TranscodeJob) -> dict[str, Any]:
        nonlocal queued, running, completed
        with lock:
            queued -= 1
            running += 1
            sink(
                "job_started",
                {
                    "input_path": str(job.input_path),
                    "output_path": str(job.output_path),
                    "target_format": job.target_format,
                    "sample_rate_hz": job.sample_rate_hz,
                    "bitrate_kbps": job.bitrate_kbps,
                    "queued": queued,
                    "running": running,
                    "completed": completed,
                },
            )
        job_started = time.perf_counter()
        try:
            transcode_file(
                job.input_path,
                job.output_path,
                job.target_format,
                sample_rate_hz=job.sample_rate_hz,
                bitrate_kbps=job.bitrate_kbps,
            )
            elapsed = time.perf_counter() - job_started
            result = {
                "ok": True,
                "input_path": str(job.input_path),
                "output_path": str(job.output_path),
                "target_format": job.target_format,
                "sample_rate_hz": job.sample_rate_hz,
                "bitrate_kbps": job.bitrate_kbps,
                "elapsed_sec": round(elapsed, 3),
            }
            sink("job_succeeded", result)
            return result
        except Exception as exc:
            elapsed = time.perf_counter() - job_started
            result = {
                "ok": False,
                "input_path": str(job.input_path),
                "output_path": str(job.output_path),
                "target_format": job.target_format,
                "sample_rate_hz": job.sample_rate_hz,
                "bitrate_kbps": job.bitrate_kbps,
                "elapsed_sec": round(elapsed, 3),
                "reason": str(exc),
            }
            sink("job_failed", result)
            return result
        finally:
            with lock:
                running -= 1
                completed += 1
                sink(
                    "queue_progress",
                    {
                        "queued": queued,
                        "running": running,
                        "completed": completed,
                        "total_jobs": len(jobs),
                    },
                )

    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="transcode") as executor:
        futures = [executor.submit(_run_job, job) for job in jobs]
        for future in as_completed(futures):
            results.append(future.result())

    success_count = sum(1 for item in results if item.get("ok"))
    failed_count = sum(1 for item in results if not item.get("ok"))
    elapsed_sec = time.perf_counter() - started
    sink(
        "batch_finished",
        {
            "total_jobs": len(jobs),
            "success_count": success_count,
            "failed_count": failed_count,
            "elapsed_sec": round(elapsed_sec, 3),
        },
    )
    return TranscodeBatchResult(
        total_jobs=len(jobs),
        success_count=success_count,
        failed_count=failed_count,
        skipped_count=0,
        results=results,
        elapsed_sec=elapsed_sec,
    )
