from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass

from src.Infrastructure.process_utils import find_process_by_name


_IGNORED_WORK_SUFFIXES = {".json", ".txt", ".kwm", ".raw"}


@dataclass(slots=True)
class KuwoPlatformAdapter:
    platform_id: str = "kuwo"
    display_name: str = "酷我音乐"

    def requires_running_process(self) -> bool:
        return True

    def _load_runtime(self):
        from src.Infrastructure.platforms.kuwo.runtime_m import kwm_decrypt_mvp

        return kwm_decrypt_mvp

    def validate_runtime(self, settings: dict) -> tuple[bool, str | None]:
        process_name = str(settings.get("process_name", "kwmusic.exe") or "kwmusic.exe")
        info = find_process_by_name(process_name)
        if info is None:
            return False, "???????"
        try:
            self._resolve_exe_path(settings)
        except RuntimeError:
            return False, "???????????????????????"
        return True, None

    def _resolve_exe_path(self, settings: dict) -> str:
        process_name = str(settings.get("process_name", "kwmusic.exe") or "kwmusic.exe")
        configured = str(settings.get("exe_path", "") or "").strip()
        process_info = find_process_by_name(process_name)

        if process_info and process_info.exe_path:
            process_path = pathlib.Path(process_info.exe_path)
            if process_path.exists():
                return str(process_path.resolve())

        if configured:
            configured_path = pathlib.Path(configured)
            if configured_path.exists():
                return str(configured_path.resolve())

        raise RuntimeError("exe_not_found")

    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
        if input_path.is_file():
            return [input_path] if input_path.suffix.lower() == ".kwm" else []
        pattern = "**/*" if recursive else "*"
        return sorted(candidate for candidate in input_path.glob(pattern) if candidate.is_file() and candidate.suffix.lower() == ".kwm")

    def output_basename(self, input_path: pathlib.Path) -> str:
        return input_path.stem

    def predicted_extension(self, input_path: pathlib.Path, settings: dict) -> str | None:
        value = str(settings.get("format_kwm", "auto") or "auto").strip().lower().lstrip(".")
        if value == "ogg":
            value = "m4a"
        return None if value == "auto" else value

    def desired_target_format(self, input_path: pathlib.Path, settings: dict) -> str:
        value = str(settings.get("format_kwm", "auto") or "auto").strip().lower().lstrip(".") or "auto"
        return "m4a" if value == "ogg" else value

    def _snapshot_work_outputs(self, work_dir: pathlib.Path) -> dict[pathlib.Path, tuple[int, int]]:
        snapshot: dict[pathlib.Path, tuple[int, int]] = {}
        if not work_dir.exists():
            return snapshot
        for candidate in work_dir.rglob("*"):
            if not candidate.is_file():
                continue
            if "raw" in candidate.parts:
                continue
            if candidate.suffix.lower() in _IGNORED_WORK_SUFFIXES:
                continue
            try:
                stat = candidate.stat()
            except OSError:
                continue
            snapshot[candidate.resolve()] = (int(stat.st_size), int(stat.st_mtime_ns))
        return snapshot

    def _load_latest_report(self, report_dir: pathlib.Path, input_path: pathlib.Path) -> dict | None:
        if not report_dir.exists():
            return None
        candidates = list(report_dir.glob(f"{input_path.stem}.report.json"))
        candidates.extend(report_dir.glob(f"{input_path.stem}.*.report.json"))
        if not candidates:
            return None
        latest = max(candidates, key=lambda item: item.stat().st_mtime_ns)
        try:
            return json.loads(latest.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _select_work_output(
        self,
        *,
        input_path: pathlib.Path,
        work_dir: pathlib.Path,
        before_snapshot: dict[pathlib.Path, tuple[int, int]],
        expected_ext: str | None,
        expected_size: int | None,
    ) -> pathlib.Path | None:
        after_snapshot = self._snapshot_work_outputs(work_dir)
        candidates: list[pathlib.Path] = []
        for candidate, fingerprint in after_snapshot.items():
            if before_snapshot.get(candidate) != fingerprint:
                candidates.append(candidate)
        if not candidates:
            candidates = list(after_snapshot.keys())
        if not candidates:
            return None

        expected_suffix = f".{expected_ext.lower().lstrip('.')}" if expected_ext else None

        def score(candidate: pathlib.Path) -> tuple[int, int, int]:
            score_value = 0
            if candidate.stem == input_path.stem:
                score_value += 1000
            elif candidate.name.startswith(input_path.stem):
                score_value += 700
            if expected_suffix and candidate.suffix.lower() == expected_suffix:
                score_value += 300
            try:
                size = int(candidate.stat().st_size)
                mtime_ns = int(candidate.stat().st_mtime_ns)
            except OSError:
                size = 0
                mtime_ns = 0
            if expected_size is not None and size > 0:
                delta = abs(size - expected_size)
                if delta == 0:
                    score_value += 200
                else:
                    score_value += max(0, 100 - min(100, delta // 1024))
            return (score_value, size, mtime_ns)

        return max(candidates, key=score)

    def _resolve_output(
        self,
        *,
        report: dict,
        persisted_report: dict | None,
        input_path: pathlib.Path,
        work_dir: pathlib.Path,
        before_snapshot: dict[pathlib.Path, tuple[int, int]],
    ) -> tuple[pathlib.Path | None, str, int]:
        report_variants = [report]
        if persisted_report:
            report_variants.append(persisted_report)

        for variant in report_variants:
            final_output = variant.get("final_output") or {}
            output_path_text = str(final_output.get("path") or "").strip()
            if not output_path_text:
                continue
            candidate = pathlib.Path(output_path_text)
            if candidate.exists():
                ext = str(final_output.get("ext") or candidate.suffix.lstrip(".") or "bin").lower()
                try:
                    size = int(final_output.get("size") or candidate.stat().st_size or 0)
                except OSError:
                    size = int(final_output.get("size") or 0)
                return candidate, ext, size

        expected_ext = None
        expected_size = None
        for variant in report_variants:
            final_output = variant.get("final_output") or {}
            if not expected_ext and final_output.get("ext"):
                expected_ext = str(final_output.get("ext") or "").lower()
            if expected_size is None and final_output.get("size") is not None:
                try:
                    expected_size = int(final_output.get("size") or 0)
                except (TypeError, ValueError):
                    expected_size = None

        selected = self._select_work_output(
            input_path=input_path,
            work_dir=work_dir,
            before_snapshot=before_snapshot,
            expected_ext=expected_ext,
            expected_size=expected_size,
        )
        if selected is None:
            return None, str(expected_ext or "bin"), int(expected_size or 0)

        try:
            selected_size = int(selected.stat().st_size)
        except OSError:
            selected_size = int(expected_size or 0)
        selected_ext = (selected.suffix.lstrip(".") or expected_ext or "bin").lower()
        return selected, selected_ext, selected_size

    def decrypt_one(self, input_path: pathlib.Path, work_dir: pathlib.Path, settings: dict, *, log_dir: pathlib.Path) -> dict:
        kwm_decrypt_mvp = self._load_runtime()
        process_name = str(settings.get("process_name", kwm_decrypt_mvp.DEFAULT_PROCESS_NAME) or kwm_decrypt_mvp.DEFAULT_PROCESS_NAME)
        process_info = find_process_by_name(process_name)
        resolved_exe_path = self._resolve_exe_path(settings)
        report_dir = log_dir / "kuwo_reports"
        output_dir = work_dir / "raw"
        before_snapshot = self._snapshot_work_outputs(work_dir)
        report = kwm_decrypt_mvp.decrypt_one_file(
            input_path,
            output_dir=output_dir,
            report_dir=report_dir,
            final_output_dir=work_dir,
            exe_path=resolved_exe_path,
            signature_file=str(settings.get("signature_file", "") or kwm_decrypt_mvp.DEFAULT_SIGNATURE_FILE),
            process_name=process_name,
            pid=int(process_info.pid) if process_info is not None and int(process_info.pid or 0) > 0 else None,
            timeout_sec=int(settings.get("timeout_sec", 12) or 12),
            verbose=False,
        )
        persisted_report = self._load_latest_report(report_dir, input_path)
        effective_report = persisted_report or report
        result_code_value = effective_report.get("result_code", 1)
        try:
            effective_result_code = int(result_code_value)
        except (TypeError, ValueError):
            effective_result_code = 1
        if effective_result_code != 0:
            raise RuntimeError(str(effective_report.get("result_reason") or "kuwo_decrypt_failed"))

        output_path, detected_ext, decoded_bytes = self._resolve_output(
            report=report,
            persisted_report=persisted_report,
            input_path=input_path,
            work_dir=work_dir,
            before_snapshot=before_snapshot,
        )
        if output_path is None or not output_path.exists():
            raise RuntimeError("kuwo_output_missing")

        return {
            "output_path": str(output_path),
            "detected_container": detected_ext,
            "final_extension": detected_ext,
            "recognition_stage": "kwm_report",
            "backend": "frida:kuwo",
            "decoded_bytes": decoded_bytes,
            "timing": effective_report.get("timing") or {},
            "report_json_path": effective_report.get("report_json_path"),
            "report_txt_path": effective_report.get("report_txt_path"),
            "result_reason": effective_report.get("result_reason"),
        }
