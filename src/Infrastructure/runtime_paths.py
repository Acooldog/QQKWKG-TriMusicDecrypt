from __future__ import annotations

import os
import pathlib
import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root_dir: pathlib.Path
    bundle_dir: pathlib.Path
    assets_dir: pathlib.Path
    plugins_dir: pathlib.Path
    log_dir: pathlib.Path
    output_dir: pathlib.Path
    docs_dir: pathlib.Path
    plugins_config: pathlib.Path
    output_manifest: pathlib.Path

    @classmethod
    def discover(cls) -> "RuntimePaths":
        if getattr(sys, "frozen", False):
            root_dir = pathlib.Path(sys.executable).resolve().parent
            bundle_candidates: list[pathlib.Path] = []
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                bundle_candidates.append(pathlib.Path(meipass).resolve())
            bundle_candidates.extend([root_dir / "_internal", root_dir / "internal", root_dir])
        else:
            root_dir = pathlib.Path(__file__).resolve().parents[2]
            bundle_candidates = [root_dir]

        bundle_dir = next((candidate for candidate in bundle_candidates if candidate.exists()), bundle_candidates[0])
        assets_dir = next(
            (
                candidate
                for candidate in [bundle_dir / "assets", root_dir / "assets", pathlib.Path.cwd() / "assets"]
                if candidate.exists()
            ),
            bundle_dir / "assets",
        )
        return cls(
            root_dir=root_dir,
            bundle_dir=bundle_dir,
            assets_dir=assets_dir,
            plugins_dir=root_dir / "plugins",
            log_dir=root_dir / "_log",
            output_dir=root_dir / "output",
            docs_dir=root_dir / "_文档",
            plugins_config=root_dir / "plugins" / "plugins.json",
            output_manifest=root_dir / "plugins" / "output_manifest.json",
        )

    def ensure_runtime_dirs(self) -> None:
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir.mkdir(parents=True, exist_ok=True)


def appdata_path() -> pathlib.Path | None:
    value = os.environ.get("APPDATA", "").strip()
    return pathlib.Path(value) if value else None
