from __future__ import annotations

import pathlib
from dataclasses import dataclass

from src.Infrastructure.process_utils import find_process_by_name


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
        return (info is not None, None if info is not None else "酷我音乐未运行")

    def collect_files(self, input_path: pathlib.Path, recursive: bool) -> list[pathlib.Path]:
        if input_path.is_file():
            return [input_path] if input_path.suffix.lower() == ".kwm" else []
        pattern = "**/*" if recursive else "*"
        return sorted(candidate for candidate in input_path.glob(pattern) if candidate.is_file() and candidate.suffix.lower() == ".kwm")

    def output_basename(self, input_path: pathlib.Path) -> str:
        return input_path.stem

    def predicted_extension(self, input_path: pathlib.Path, settings: dict) -> str | None:
        value = str(settings.get("format_kwm", "auto") or "auto").strip().lower().lstrip(".")
        return None if value == "auto" else value

    def desired_target_format(self, input_path: pathlib.Path, settings: dict) -> str:
        return str(settings.get("format_kwm", "auto") or "auto").strip().lower().lstrip(".") or "auto"

    def decrypt_one(self, input_path: pathlib.Path, work_dir: pathlib.Path, settings: dict, *, log_dir: pathlib.Path) -> dict:
        kwm_decrypt_mvp = self._load_runtime()
        report_dir = log_dir / "kuwo_reports"
        output_dir = work_dir / "raw"
        report = kwm_decrypt_mvp.decrypt_one_file(
            input_path,
            output_dir=output_dir,
            report_dir=report_dir,
            final_output_dir=work_dir,
            exe_path=str(settings.get("exe_path", "") or kwm_decrypt_mvp.DEFAULT_EXE_PATH),
            signature_file=str(settings.get("signature_file", "") or kwm_decrypt_mvp.DEFAULT_SIGNATURE_FILE),
            process_name=str(settings.get("process_name", kwm_decrypt_mvp.DEFAULT_PROCESS_NAME) or kwm_decrypt_mvp.DEFAULT_PROCESS_NAME),
            verbose=False,
        )
        if int(report.get("result_code", 1) or 1) != 0 or not report.get("final_output"):
            raise RuntimeError(str(report.get("result_reason") or "kuwo_decrypt_failed"))
        final_output = report["final_output"]
        output_path = pathlib.Path(str(final_output["path"]))
        if not output_path.exists():
            raise RuntimeError("kuwo_output_missing")
        return {
            "output_path": str(output_path),
            "detected_container": str(final_output.get("ext", "bin") or "bin").lower(),
            "final_extension": str(final_output.get("ext", "bin") or "bin").lower(),
            "recognition_stage": "kwm_report",
            "backend": "frida:kuwo",
            "decoded_bytes": int(final_output.get("size", 0) or 0),
            "timing": report.get("timing") or {},
            "report_json_path": report.get("report_json_path"),
            "report_txt_path": report.get("report_txt_path"),
            "result_reason": report.get("result_reason"),
        }
