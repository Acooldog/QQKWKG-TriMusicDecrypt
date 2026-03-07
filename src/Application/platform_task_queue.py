from __future__ import annotations

import pathlib
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from src.Application.decrypt_service import run_batch
from src.Application.models import BatchRunConfig
from src.Infrastructure.platforms.registry import build_platform_adapter


TaskStarter = Callable[[Callable[[], None]], None]
StateSink = Callable[[list[dict[str, Any]]], None]
LogSink = Callable[[str], None]
CollisionResolver = Callable[[str, str, str | None], str]


@dataclass(slots=True)
class PlatformTaskState:
    platform_id: str
    title: str
    input_path: str
    output_dir: str
    recursive: bool
    settings: dict[str, Any]
    status: str = "idle"
    message: str = "空闲"
    current_file: str = ""
    current_index: int = 0
    current_total: int = 0
    queue_position: int = 0
    success_count: int = 0
    recovered_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    candidate_count: int = 0
    result_code: int | None = None
    continuous: bool = False
    stop_requested: bool = False
    loop_interval_sec: float = 3.0
    batch_report_json: str = ""
    batch_report_txt: str = ""
    timing_hotspot: dict[str, Any] = field(default_factory=dict)
    last_timing: dict[str, Any] = field(default_factory=dict)
    last_updated: float = field(default_factory=time.time)

    def to_payload(self) -> dict[str, Any]:
        return {
            "platform_id": self.platform_id,
            "title": self.title,
            "input_path": self.input_path,
            "output_dir": self.output_dir,
            "recursive": self.recursive,
            "status": self.status,
            "message": self.message,
            "current_file": self.current_file,
            "current_index": self.current_index,
            "current_total": self.current_total,
            "queue_position": self.queue_position,
            "success_count": self.success_count,
            "recovered_count": self.recovered_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "candidate_count": self.candidate_count,
            "result_code": self.result_code,
            "continuous": self.continuous,
            "stop_requested": self.stop_requested,
            "batch_report_json": self.batch_report_json,
            "batch_report_txt": self.batch_report_txt,
            "timing_hotspot": dict(self.timing_hotspot),
            "last_timing": dict(self.last_timing),
            "last_updated": self.last_updated,
        }


class PlatformTaskQueue:
    def __init__(
        self,
        *,
        task_starter: TaskStarter,
        state_sink: StateSink,
        log_sink: LogSink,
        collision_resolver: CollisionResolver,
        max_running: int = 2,
    ) -> None:
        self._task_starter = task_starter
        self._state_sink = state_sink
        self._log_sink = log_sink
        self._collision_resolver = collision_resolver
        self._max_running = max_running
        self._lock = threading.RLock()
        self._tasks: dict[str, PlatformTaskState] = {}
        self._running: set[str] = set()
        self._queue: deque[str] = deque()

    def submit(
        self,
        *,
        platform_id: str,
        title: str,
        input_path: pathlib.Path,
        output_dir: pathlib.Path,
        recursive: bool,
        settings: dict[str, Any],
        continuous: bool = False,
    ) -> tuple[bool, str | None]:
        with self._lock:
            current = self._tasks.get(platform_id)
            if current and current.status in {"queued", "running", "waiting", "stopping"}:
                return False, f"{title} 任务已在运行或排队。"

            task = PlatformTaskState(
                platform_id=platform_id,
                title=title,
                input_path=str(input_path),
                output_dir=str(output_dir),
                recursive=recursive,
                settings=dict(settings),
                continuous=bool(continuous),
            )
            self._tasks[platform_id] = task
            if len(self._running) < self._max_running:
                self._start_locked(task)
            else:
                task.status = "queued"
                task.message = "排队中"
                self._queue.append(platform_id)
                self._reindex_queue_locked()
                self._emit_log_locked(f"[queue] {title} queued")
                self._push_state_locked()
            return True, None

    def stop(self, platform_id: str) -> tuple[bool, str | None]:
        with self._lock:
            task = self._tasks.get(platform_id)
            if task is None:
                return False, "未找到对应平台任务。"
            if task.status == "queued":
                task.stop_requested = True
                task.status = "stopped"
                task.message = "已停止（未开始）"
                try:
                    self._queue.remove(platform_id)
                except ValueError:
                    pass
                self._emit_log_locked(f"[stop] {task.title} stopped before start")
                self._reindex_queue_locked()
                self._push_state_locked()
                return True, None
            if task.status in {"running", "waiting"}:
                task.stop_requested = True
                task.status = "stopping"
                task.message = "停止中，当前批次结束后退出"
                task.last_updated = time.time()
                self._emit_log_locked(f"[stop] {task.title} stopping after current batch")
                self._push_state_locked()
                return True, None
            if task.status in {"stopping", "stopped"}:
                return False, "该平台任务已经在停止或已停止。"
            return False, "当前平台没有正在运行的任务。"

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._tasks[key].to_payload() for key in sorted(self._tasks)]

    def _emit_log_locked(self, message: str) -> None:
        self._log_sink(message)

    def _push_state_locked(self) -> None:
        self._state_sink([self._tasks[key].to_payload() for key in sorted(self._tasks)])

    def _reindex_queue_locked(self) -> None:
        for position, platform_id in enumerate(self._queue, start=1):
            task = self._tasks.get(platform_id)
            if task is not None:
                task.queue_position = position

    def _start_locked(self, task: PlatformTaskState) -> None:
        task.status = "running"
        task.message = "任务已启动"
        task.queue_position = 0
        task.stop_requested = False
        task.last_updated = time.time()
        self._running.add(task.platform_id)
        self._emit_log_locked(f"[start] {task.title} started")
        self._push_state_locked()
        self._task_starter(lambda: self._run_task(task.platform_id))

    def _drain_locked(self) -> None:
        while self._queue and len(self._running) < self._max_running:
            next_platform_id = self._queue.popleft()
            next_task = self._tasks.get(next_platform_id)
            if next_task is None:
                continue
            self._start_locked(next_task)
        self._reindex_queue_locked()
        self._push_state_locked()

    def _build_completion_message(self, task: PlatformTaskState, result_code: int) -> tuple[str, str]:
        if result_code == 3 or task.stop_requested:
            return "stopped", "已停止"
        if result_code != 0:
            return "failed", f"失败 {task.failed_count} 个，跳过 {task.skipped_count} 个"
        if task.success_count == 0 and task.skipped_count > 0:
            return "skipped", f"本轮全部跳过，共 {task.skipped_count} 个文件"
        pieces = [f"成功 {task.success_count}"]
        if task.recovered_count > 0:
            pieces.append(f"恢复 {task.recovered_count}")
        if task.skipped_count > 0:
            pieces.append(f"跳过 {task.skipped_count}")
        return "success", "已完成，" + "，".join(pieces)

    def _run_task(self, platform_id: str) -> None:
        adapter = build_platform_adapter(platform_id)
        while True:
            task = self._tasks[platform_id]
            batch_config = BatchRunConfig(
                platform_id=platform_id,
                input_path=pathlib.Path(task.input_path),
                output_dir=pathlib.Path(task.output_dir),
                recursive=task.recursive,
                collision_policy="suffix",
                settings=dict(task.settings),
                interactive=True,
                collision_resolver=self._collision_resolver,
                event_sink=lambda event_name, payload: self._handle_event(platform_id, event_name, payload),
                stop_requested=lambda: self._is_stop_requested(platform_id),
            )
            try:
                result_code = run_batch(batch_config, adapter)
            except Exception as exc:
                with self._lock:
                    task = self._tasks[platform_id]
                    task.status = "failed"
                    task.message = str(exc)
                    task.result_code = 2
                    task.last_updated = time.time()
                    self._running.discard(platform_id)
                    self._emit_log_locked(f"[failed] {task.title}: {exc}")
                    self._drain_locked()
                return

            with self._lock:
                task = self._tasks[platform_id]
                task.result_code = result_code
                task.last_updated = time.time()
                if not task.continuous:
                    task.status, task.message = self._build_completion_message(task, result_code)
                    self._running.discard(platform_id)
                    self._emit_log_locked(f"[done] {task.title} result_code={result_code}")
                    self._drain_locked()
                    return
                if result_code == 3 or task.stop_requested:
                    task.status = "stopped"
                    task.message = "已停止"
                    self._running.discard(platform_id)
                    self._emit_log_locked(f"[done] {task.title} stopped")
                    self._drain_locked()
                    return
                task.status = "waiting"
                task.message = f"持续解密等待 {int(task.loop_interval_sec)} 秒后重扫"
                self._emit_log_locked(
                    f"[loop] {task.title} waiting {task.loop_interval_sec:.0f}s for next scan"
                )
                self._push_state_locked()

            waited = 0.0
            while waited < task.loop_interval_sec:
                if self._is_stop_requested(platform_id):
                    break
                time.sleep(0.1)
                waited += 0.1

            with self._lock:
                task = self._tasks[platform_id]
                if task.stop_requested:
                    task.status = "stopped"
                    task.message = "已停止"
                    task.last_updated = time.time()
                    self._running.discard(platform_id)
                    self._emit_log_locked(f"[done] {task.title} stopped")
                    self._drain_locked()
                    return
                task.status = "running"
                task.message = "持续解密重新扫描中"
                task.last_updated = time.time()
                self._push_state_locked()

    def _handle_event(self, platform_id: str, event_name: str, payload: dict[str, Any]) -> None:
        with self._lock:
            task = self._tasks.get(platform_id)
            if task is None:
                return
            if event_name == "batch_started":
                task.candidate_count = int(payload.get("candidate_count", 0) or 0)
                task.current_total = task.candidate_count
                task.message = "批次已开始"
            elif event_name == "file_started":
                task.current_file = str(payload.get("input_path", "") or "")
                task.current_index = int(payload.get("index", 0) or 0)
                task.current_total = int(payload.get("total", task.current_total) or task.current_total)
                task.message = pathlib.Path(task.current_file).name if task.current_file else "处理中"
            elif event_name == "file_finished":
                result = str(payload.get("result", "") or "")
                detail_payload = dict(payload.get("payload", {}) or {})
                backend = str(detail_payload.get("backend", "") or "")
                if result == "success":
                    task.success_count += 1
                    if backend == "python-forced":
                        task.recovered_count += 1
                elif result == "already_decrypted":
                    task.skipped_count += 1
                elif result == "failed":
                    task.failed_count += 1
                task.last_timing = dict(payload.get("timing", {}) or {})
            elif event_name == "batch_finished":
                task.success_count = int(payload.get("success_count", task.success_count) or task.success_count)
                task.skipped_count = int(payload.get("skipped_count", task.skipped_count) or task.skipped_count)
                task.failed_count = int(payload.get("failed_count", task.failed_count) or task.failed_count)
                task.batch_report_json = str(payload.get("batch_report_json", "") or "")
                task.batch_report_txt = str(payload.get("batch_report_txt", "") or "")
                task.timing_hotspot = dict(payload.get("timing_hotspot_stage", {}) or {})
            task.last_updated = time.time()
            self._push_state_locked()

    def _is_stop_requested(self, platform_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(platform_id)
            return bool(task and task.stop_requested)
