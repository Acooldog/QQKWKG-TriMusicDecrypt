from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


TIMING_STAGE_KEYS = ("scan_sec", "dedupe_sec", "decrypt_sec", "transcode_sec", "publish_sec", "total_sec")
RunEventSink = Callable[[str, dict[str, Any]], None]
StopRequested = Callable[[], bool]


class PlatformAdapter(Protocol):
    platform_id: str
    display_name: str

    def requires_running_process(self) -> bool: ...
    def validate_runtime(self, settings: dict[str, Any]) -> tuple[bool, str | None]: ...
    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]: ...
    def output_basename(self, input_path: pathlib.Path) -> str: ...
    def predicted_extension(self, input_path: pathlib.Path, settings: dict[str, Any]) -> str | None: ...
    def desired_target_format(self, input_path: pathlib.Path, settings: dict[str, Any]) -> str: ...
    def decrypt_one(self, input_path: pathlib.Path, work_dir: pathlib.Path, settings: dict[str, Any], *, log_dir: pathlib.Path) -> dict[str, Any]: ...


@dataclass(slots=True)
class BatchRunConfig:
    platform_id: str
    input_path: pathlib.Path
    output_dir: pathlib.Path
    recursive: bool
    collision_policy: str
    settings: dict[str, Any]
    interactive: bool = False
    collision_resolver: Callable[[str, str, str | None], str] | None = None
    event_sink: RunEventSink | None = None
    stop_requested: StopRequested | None = None


@dataclass(slots=True)
class FileResult:
    ok: bool
    platform_id: str
    input_path: str
    output_path: str | None = None
    reason: str | None = None
    skipped: bool = False
    timing: dict[str, float] = field(default_factory=dict)
    decrypt_detail_timing: dict[str, float] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BatchSummary:
    result_code: int
    platform_id: str
    input_path: str
    output_dir: str
    success_count: int
    skipped_count: int
    failed_count: int
    candidate_count: int
    timing_batch_total: dict[str, float]
    timing_batch_avg: dict[str, float]
    timing_hotspot_stage: dict[str, float | str | None]
