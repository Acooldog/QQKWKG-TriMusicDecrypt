from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class OutputManifestRepository:
    manifest_path: pathlib.Path

    def load(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, payload: dict[str, Any]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_platform(self, output_path: pathlib.Path) -> str | None:
        payload = self.load()
        key = str(output_path.resolve()).lower() if output_path.exists() else str(output_path).lower()
        entry = payload.get(key)
        if isinstance(entry, dict):
            return str(entry.get("platform", "") or "") or None
        if isinstance(entry, str):
            return entry or None
        return None

    def set_platform(self, output_path: pathlib.Path, platform_id: str) -> None:
        payload = self.load()
        key = str(output_path.resolve()).lower()
        payload[key] = {"platform": platform_id, "path": str(output_path.resolve())}
        self.save(payload)

    def remove(self, output_path: pathlib.Path) -> None:
        payload = self.load()
        key = str(output_path).lower()
        payload.pop(key, None)
        self.save(payload)
