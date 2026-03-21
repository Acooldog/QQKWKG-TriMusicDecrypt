from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class QQVariantAdaptResult:
    status: str
    staged_path: str
    mode: str
    message: str
    original_input_path: str


class QQVariantAdapterService:
    """Prepare QQ encrypted inputs into a legacy-compatible form for the old chain.

    Current rule-set focuses on the verified path-compatibility variant:
    stage the encrypted source into an ASCII-safe temporary path before the
    existing Frida decrypt flow touches it.

    Additional variant transforms can be appended here later without changing
    the decrypt pipeline shape.
    """

    def prepare_legacy_compatible_input(self, source_file_path: str, temp_dir: str) -> QQVariantAdaptResult:
        source = Path(source_file_path)
        temp_root = Path(temp_dir)
        temp_root.mkdir(parents=True, exist_ok=True)
        token = hashlib.md5(str(source).encode("utf-8")).hexdigest()
        staged_path = temp_root / f"qqsrc_{token}{source.suffix.lower()}"
        shutil.copy2(source, staged_path)
        return QQVariantAdaptResult(
            status="staged",
            staged_path=str(staged_path),
            mode="ascii_source_stage",
            message="正在转换 QQ 加密变体，准备旧链兼容输入",
            original_input_path=str(source),
        )
