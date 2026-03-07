from __future__ import annotations

import json
import logging
import pathlib
import sys
from dataclasses import asdict
from datetime import datetime

from src.Application.models import BatchSummary, FileResult, TIMING_STAGE_KEYS
from src.Infrastructure.runtime_paths import RuntimePaths


APP_LOGGER_NAME = "qkkdecrypt"


def today_log_dir(paths: RuntimePaths) -> pathlib.Path:
    now = datetime.now()
    directory = paths.log_dir / f"{now.year}-{now.month}-{now.day}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def setup_logger(paths: RuntimePaths) -> tuple[logging.Logger, pathlib.Path, pathlib.Path]:
    log_dir = today_log_dir(paths)
    log_path = log_dir / f"run_{datetime.now().strftime('%H-%M-%S')}.log"
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger, log_path, log_dir


def timing_text(value: dict[str, float]) -> str:
    return " ".join(f"{key.replace('_sec', '')}={float(value.get(key, 0.0)):.3f}s" for key in TIMING_STAGE_KEYS)


def write_batch_reports(log_dir: pathlib.Path, platform_id: str, results: list[FileResult], summary: BatchSummary) -> tuple[pathlib.Path, pathlib.Path]:
    stamp = datetime.now().strftime("%H-%M-%S")
    json_path = log_dir / f"{platform_id}_batch_{stamp}.json"
    txt_path = log_dir / f"{platform_id}_batch_{stamp}.txt"
    payload = {
        "summary": asdict(summary),
        "results": [
            {
                "ok": item.ok,
                "skipped": item.skipped,
                "platform": item.platform_id,
                "input_path": item.input_path,
                "output_path": item.output_path,
                "reason": item.reason,
                "timing": item.timing,
                "decrypt_detail_timing": item.decrypt_detail_timing,
                **item.payload,
            }
            for item in results
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"platform={summary.platform_id}",
        f"result_code={summary.result_code}",
        f"success_count={summary.success_count}",
        f"skipped_count={summary.skipped_count}",
        f"failed_count={summary.failed_count}",
        f"input={summary.input_path}",
        f"output={summary.output_dir}",
        f"timing_batch_total={json.dumps(summary.timing_batch_total, ensure_ascii=False)}",
        f"timing_batch_avg={json.dumps(summary.timing_batch_avg, ensure_ascii=False)}",
        f"timing_hotspot_stage={json.dumps(summary.timing_hotspot_stage, ensure_ascii=False)}",
        "",
    ]
    for item in results:
        if item.skipped:
            lines.append(f"SKIP | {item.platform_id} | {item.input_path} -> {item.output_path} | already_decrypted")
        elif item.ok:
            lines.append(f"OK  | {item.platform_id} | {item.input_path} -> {item.output_path}")
        else:
            lines.append(f"ERR | {item.platform_id} | {item.input_path} | {item.reason}")
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, txt_path
