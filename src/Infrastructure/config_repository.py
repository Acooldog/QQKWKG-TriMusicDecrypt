from __future__ import annotations

import json
import pathlib
from typing import Any

from src.Infrastructure.runtime_paths import RuntimePaths, appdata_path
from src.Infrastructure.transcoder import SUPPORTED_TARGET_FORMATS, normalize_target_format


CONFIG_NAMESPACE = "decrypt_cli"
PROJECT_NAME_EN = "QKKDecrypt"
PROJECT_NAME_ZH = "QQ酷狗酷我音乐解密工具"
PROJECT_ADDRESS = r"O:\A_python\A_QKKd"
PROJECT_QQ = "2622138410"
QQMUSIC_ATTRIBUTION = "QQ音乐解密模型思路参考: https://github.com/luyikk/qqmusic_decrypt"
LEGAL_NOTICE = "仅供学习交流使用，禁止商用，禁止倒卖；倒卖者将举报平台并持续追责。"
FLET_NOTE = "main-ui 分支采用 Flet，本地 Python 后端与 Flutter 前端通过本地桌面会话协同运行。"
DEFAULT_KUGOU_INPUT = pathlib.Path(r"O:\KuGou\KugouMusic")
DEFAULT_KUWO_INPUT = pathlib.Path(r"C:\Users\01080\Documents\Frontier Developments\Planet Coaster\UserMusic\MusicPack")
DEFAULT_QQ_INPUT = pathlib.Path("")


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def iter_kugou_key_candidates(paths: RuntimePaths) -> list[pathlib.Path]:
    candidates = [
        paths.assets_dir / "kugou_key.xz",
        paths.bundle_dir / "assets" / "kugou_key.xz",
        paths.root_dir / "assets" / "kugou_key.xz",
        pathlib.Path.cwd() / "assets" / "kugou_key.xz",
        pathlib.Path.cwd() / "kugou_key.xz",
    ]
    unique: list[pathlib.Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        lowered = str(candidate).lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(candidate)
    return unique


def auto_find_kugou_key(paths: RuntimePaths) -> pathlib.Path | None:
    for candidate in iter_kugou_key_candidates(paths):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def iter_kgg_db_candidates() -> list[pathlib.Path]:
    candidates: list[pathlib.Path] = []
    appdata = appdata_path()
    if appdata is not None:
        candidates.append(appdata / "KuGou8" / "KGMusicV3.db")
        candidates.extend(sorted(appdata.glob("KuGou*\\KGMusicV3.db")))
    unique: list[pathlib.Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        lowered = str(candidate).lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(candidate)
    return unique


def auto_find_kgg_db_path() -> pathlib.Path | None:
    for candidate in iter_kgg_db_candidates():
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def default_kuwo_signature_path(paths: RuntimePaths) -> pathlib.Path:
    candidates = [
        paths.bundle_dir / "src" / "Infrastructure" / "platforms" / "kuwo" / "runtime_m" / "out" / "recovered_signature.json",
        paths.bundle_dir / "src" / "Infrastructure" / "platforms" / "kuwo" / "runtime_m" / "out" / "out" / "recovered_signature.json",
        paths.root_dir / "src" / "Infrastructure" / "platforms" / "kuwo" / "runtime_m" / "out" / "recovered_signature.json",
        paths.root_dir / "src" / "Infrastructure" / "platforms" / "kuwo" / "runtime_m" / "out" / "out" / "recovered_signature.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def load_config(paths: RuntimePaths) -> tuple[dict[str, Any], dict[str, Any]]:
    paths.ensure_runtime_dirs()
    root = _read_json(paths.plugins_config)
    payload = root.get(CONFIG_NAMESPACE)
    payload = payload if isinstance(payload, dict) else {}
    config = {
        "shared": {
            "output_dir": str(paths.output_dir),
            "cli_collision_policy": "suffix",
            "recursive": True,
        },
        "qq": {
            "input_dir": str(DEFAULT_QQ_INPUT),
            "process_match": "qqmusic",
            "format_rules": {"mflac": "flac", "mgg": "ogg", "mmp4": "m4a"},
        },
        "kuwo": {
            "input_dir": str(DEFAULT_KUWO_INPUT),
            "process_name": "kwmusic.exe",
            "exe_path": "",
            "signature_file": str(default_kuwo_signature_path(paths)),
            "format_kwm": "auto",
        },
        "kugou": {
            "input_dir": str(DEFAULT_KUGOU_INPUT),
            "kgg_db_path": str(auto_find_kgg_db_path() or ""),
            "key_file": str(auto_find_kugou_key(paths) or (paths.assets_dir / "kugou_key.xz")),
            "target_format_kgma": "auto",
            "target_format_kgg": "auto",
        },
    }
    for section in ("shared", "qq", "kuwo", "kugou"):
        value = payload.get(section)
        if isinstance(value, dict):
            config[section].update(value)
    config["shared"]["cli_collision_policy"] = str(config["shared"].get("cli_collision_policy", "suffix") or "suffix").lower()
    config["shared"]["recursive"] = bool(config["shared"].get("recursive", True))
    config["kuwo"]["format_kwm"] = normalize_target_format(config["kuwo"].get("format_kwm", "auto"))
    config["kugou"]["target_format_kgma"] = normalize_target_format(config["kugou"].get("target_format_kgma", "auto"))
    config["kugou"]["target_format_kgg"] = normalize_target_format(config["kugou"].get("target_format_kgg", "auto"))
    return root, config


def save_config(paths: RuntimePaths, root: dict[str, Any], config: dict[str, Any]) -> None:
    paths.ensure_runtime_dirs()
    root[CONFIG_NAMESPACE] = config
    paths.plugins_config.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")


def save_default_config_if_missing(paths: RuntimePaths) -> dict[str, Any]:
    root, config = load_config(paths)
    save_config(paths, root, config)
    return config


def build_banner(paths: RuntimePaths) -> str:
    return (
        f"{PROJECT_NAME_EN} | {PROJECT_NAME_ZH}\n"
        f"项目地址: {PROJECT_ADDRESS}\n"
        f"QQ: {PROJECT_QQ}\n"
        f"{LEGAL_NOTICE}\n"
        f"{QQMUSIC_ATTRIBUTION}"
    )


def format_help_epilog(paths: RuntimePaths) -> str:
    return (
        f"项目地址: {PROJECT_ADDRESS}\n"
        f"QQ: {PROJECT_QQ}\n"
        f"{QQMUSIC_ATTRIBUTION}\n"
        f"{FLET_NOTE}\n"
        f"{LEGAL_NOTICE}"
    )


def validate_target_format(value: str) -> str:
    return normalize_target_format(value)


def supported_transcode_formats() -> list[str]:
    return sorted(SUPPORTED_TARGET_FORMATS)
