from __future__ import annotations

import pathlib
import threading
from collections import deque
from typing import Any, Callable

import flet as ft

from src.Application.platform_task_queue import PlatformTaskQueue
from src.Infrastructure.config_repository import (
    DEFAULT_QQ_INPUT,
    FLET_NOTE,
    LEGAL_NOTICE,
    PROJECT_ADDRESS,
    PROJECT_NAME_EN,
    PROJECT_NAME_ZH,
    PROJECT_QQ,
    QQMUSIC_ATTRIBUTION,
    auto_find_kgg_db_path,
    auto_find_kugou_key,
    default_kuwo_signature_path,
    load_config,
    save_config,
    save_default_config_if_missing,
    supported_transcode_formats,
)
from src.Infrastructure.platforms.registry import build_platform_adapter
from src.Infrastructure.runtime_paths import RuntimePaths


PLATFORMS = {
    "qq": {"label": "QQ Music", "accent": "#0078D4"},
    "kuwo": {"label": "Kuwo Music", "accent": "#C239B3"},
    "kugou": {"label": "Kugou Music", "accent": "#107C10"},
}
STATUS_COLORS = {
    "idle": "#6B7280",
    "queued": "#D97706",
    "running": "#2563EB",
    "success": "#0F766E",
    "failed": "#B91C1C",
}
STATUS_TEXT = {
    "idle": "Idle",
    "queued": "Queued",
    "running": "Running",
    "success": "Completed",
    "failed": "Failed",
}


def _show_snackbar(page: ft.Page, message: str, *, error: bool = False) -> None:
    page.snack_bar = ft.SnackBar(
        content=ft.Text(message),
        bgcolor="#B91C1C" if error else "#111827",
        duration=2400,
    )
    page.snack_bar.open = True
    page.update()


def _format_options(*, auto: bool = True) -> list[ft.dropdown.Option]:
    values = supported_transcode_formats()
    if not auto:
        values = [item for item in values if item != "auto"]
    return [ft.dropdown.Option(value) for value in values]


def _status_label(value: str) -> str:
    return STATUS_TEXT.get(value, value)


def _safe_path_text(value: str | pathlib.Path | None) -> str:
    return str(value or "")


def main() -> int:
    ft.app(target=_app_main)
    return 0


def _app_main(page: ft.Page) -> None:
    paths = RuntimePaths.discover()
    config = save_default_config_if_missing(paths)

    page.title = PROJECT_NAME_EN
    page.padding = 20
    page.spacing = 18
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = "#F3F4F6"
    page.window.width = 1520
    page.window.height = 940
    page.window.min_width = 1280
    page.window.min_height = 820

    log_lines: deque[str] = deque(maxlen=240)
    platform_cards: dict[str, dict[str, Any]] = {}
    state_lock = threading.RLock()

    shared_output = ft.TextField(
        label="Shared output directory",
        value=str(config["shared"].get("output_dir", paths.output_dir)),
        dense=True,
        expand=True,
        border_radius=6,
        filled=True,
        bgcolor="#FFFFFF",
    )
    recursive_switch = ft.Switch(
        label="Scan subdirectories recursively",
        value=bool(config["shared"].get("recursive", True)),
    )
    queue_summary = ft.Text("Running 0 / 2 | Queue 0", size=14, color="#334155")
    log_view = ft.ListView(expand=True, spacing=6, auto_scroll=True)

    def push_log(message: str) -> None:
        with state_lock:
            log_lines.appendleft(message)
            log_view.controls = [ft.Text(line, size=12, color="#111827", selectable=True) for line in list(log_lines)]
            page.update()

    def refresh_cards(states: list[dict[str, Any]]) -> None:
        with state_lock:
            running = 0
            queued = 0
            for state in states:
                card = platform_cards.get(state["platform_id"])
                if card is None:
                    continue
                status = state.get("status", "idle")
                if status == "running":
                    running += 1
                elif status == "queued":
                    queued += 1
                card["status"].value = _status_label(status)
                card["status"].color = STATUS_COLORS.get(status, "#6B7280")
                card["message"].value = str(state.get("message", ""))
                current_file = pathlib.Path(str(state.get("current_file", "") or "")).name if state.get("current_file") else "-"
                card["progress"].value = (
                    f"Current file: {current_file} | Progress: {int(state.get('current_index', 0) or 0)}/"
                    f"{int(state.get('current_total', 0) or 0)}"
                )
                hotspot = state.get("timing_hotspot") or {}
                hotspot_text = str(hotspot.get("stage") or "-")
                card["metrics"].value = (
                    f"Success {int(state.get('success_count', 0) or 0)} | "
                    f"Skipped {int(state.get('skipped_count', 0) or 0)} | "
                    f"Failed {int(state.get('failed_count', 0) or 0)} | hotspot {hotspot_text}"
                )
                position = int(state.get("queue_position", 0) or 0)
                card["badge"].visible = position > 0
                card["badge"].value = f"Queue #{position}" if position > 0 else ""
                card["report"].value = _safe_path_text(state.get("batch_report_json"))
                card["container"].border = ft.border.all(1, STATUS_COLORS.get(status, "#D1D5DB"))
                card["container"].offset = ft.Offset(0, 0.0 if status in {"running", "queued"} else 0.02)
                card["container"].scale = 1.01 if status == "running" else 1.0
            queue_summary.value = f"Running {running} / 2 | Queue {queued}"
            page.update()

    def collision_resolver(base_name: str, extension: str, existing_platform: str | None) -> str:
        ready = threading.Event()
        decision = {"value": "suffix"}

        def _choose(value: str) -> None:
            decision["value"] = value
            dialog.open = False
            page.update()
            ready.set()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Output conflict"),
            content=ft.Column(
                controls=[
                    ft.Text(f"Conflicting file name: {base_name}.{extension}"),
                    ft.Text(f"Existing platform: {existing_platform or 'unknown'}", color="#475569"),
                ],
                tight=True,
            ),
            actions=[
                ft.TextButton("Append platform suffix", on_click=lambda _: _choose("suffix")),
                ft.TextButton("Use platform subfolder", on_click=lambda _: _choose("subdir")),
                ft.TextButton("Overwrite", on_click=lambda _: _choose("overwrite")),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.dialog = dialog
        dialog.open = True
        page.update()
        ready.wait()
        return str(decision["value"])

    scheduler = PlatformTaskQueue(
        task_starter=lambda fn: page.run_thread(fn),
        state_sink=refresh_cards,
        log_sink=push_log,
        collision_resolver=collision_resolver,
        max_running=2,
    )

    def save_current_config() -> dict[str, Any]:
        root, current = load_config(paths)
        current["shared"]["output_dir"] = shared_output.value.strip()
        current["shared"]["recursive"] = bool(recursive_switch.value)

        current["qq"]["input_dir"] = platform_cards["qq"]["input"].value.strip()
        current["qq"]["format_rules"] = {
            "mflac": platform_cards["qq"]["mflac"].value,
            "mgg": platform_cards["qq"]["mgg"].value,
            "mmp4": platform_cards["qq"]["mmp4"].value,
        }

        current["kuwo"]["input_dir"] = platform_cards["kuwo"]["input"].value.strip()
        current["kuwo"]["format_kwm"] = platform_cards["kuwo"]["format_kwm"].value
        current["kuwo"]["signature_file"] = str(default_kuwo_signature_path(paths))

        current["kugou"]["input_dir"] = platform_cards["kugou"]["input"].value.strip()
        current["kugou"]["key_file"] = platform_cards["kugou"]["key_file"].value.strip()
        current["kugou"]["kgg_db_path"] = platform_cards["kugou"]["kgg_db_path"].value.strip()
        current["kugou"]["target_format_kgma"] = platform_cards["kugou"]["format_kgma"].value
        current["kugou"]["target_format_kgg"] = platform_cards["kugou"]["format_kgg"].value

        save_config(paths, root, current)
        return current

    def validate_or_prompt(platform_id: str, settings: dict[str, Any], on_ready: Callable[[], None]) -> None:
        adapter = build_platform_adapter(platform_id)
        if platform_id == "kugou":
            if not settings.get("key_file"):
                auto_key = auto_find_kugou_key(paths)
                if auto_key is not None:
                    settings["key_file"] = str(auto_key)
                    platform_cards["kugou"]["key_file"].value = str(auto_key)
            if not settings.get("kgg_db_path"):
                auto_db = auto_find_kgg_db_path()
                if auto_db is not None:
                    settings["kgg_db_path"] = str(auto_db)
                    platform_cards["kugou"]["kgg_db_path"].value = str(auto_db)
            on_ready()
            return

        ok, reason = adapter.validate_runtime(settings)
        if ok:
            on_ready()
            return

        message = ft.Text(reason or f"{PLATFORMS[platform_id]['label']} is not running.", color="#111827")

        def retry(_: ft.ControlEvent) -> None:
            ok_retry, reason_retry = adapter.validate_runtime(settings)
            if ok_retry:
                dialog.open = False
                page.update()
                on_ready()
                return
            message.value = reason_retry or f"{PLATFORMS[platform_id]['label']} is still not running."
            page.update()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"{PLATFORMS[platform_id]['label']} not running"),
            content=ft.Column(
                controls=[
                    ft.Text(f"Please start {PLATFORMS[platform_id]['label']} first."),
                    message,
                ],
                tight=True,
            ),
            actions=[
                ft.TextButton("Started, check again", on_click=retry),
                ft.TextButton("Cancel", on_click=lambda _: _close_dialog(dialog)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.dialog = dialog
        dialog.open = True
        page.update()

    def _close_dialog(dialog: ft.AlertDialog) -> None:
        dialog.open = False
        page.update()

    def start_platform(platform_id: str) -> None:
        current = save_current_config()
        output_text = current["shared"].get("output_dir", "")
        if not str(output_text).strip():
            _show_snackbar(page, "Shared output directory cannot be empty.", error=True)
            return
        input_text = str(current[platform_id].get("input_dir", "") or "").strip()
        if not input_text:
            _show_snackbar(page, f"{PLATFORMS[platform_id]['label']} input path cannot be empty.", error=True)
            return

        shared_output_path = pathlib.Path(str(output_text))
        input_path = pathlib.Path(input_text)
        settings = dict(current[platform_id])

        def _submit() -> None:
            accepted, reason = scheduler.submit(
                platform_id=platform_id,
                title=PLATFORMS[platform_id]["label"],
                input_path=input_path,
                output_dir=shared_output_path,
                recursive=bool(current["shared"].get("recursive", True)),
                settings=settings,
            )
            if not accepted:
                _show_snackbar(page, reason or "Task submission failed.", error=True)
                return
            push_log(f"[submit] {PLATFORMS[platform_id]['label']} submitted")

        validate_or_prompt(platform_id, settings, _submit)

    def build_platform_card(platform_id: str) -> ft.Container:
        title = PLATFORMS[platform_id]["label"]
        accent = PLATFORMS[platform_id]["accent"]
        input_field = ft.TextField(
            label="Input file or directory",
            value=str(config[platform_id].get("input_dir", DEFAULT_QQ_INPUT if platform_id == "qq" else "")),
            dense=True,
            border_radius=6,
            filled=True,
            bgcolor="#FFFFFF",
        )
        status_text = ft.Text("Idle", size=16, weight=ft.FontWeight.W_600, color=STATUS_COLORS["idle"])
        message_text = ft.Text("Waiting to start", size=12, color="#475569")
        progress_text = ft.Text("Current file: - | Progress: 0/0", size=12, color="#334155")
        metrics_text = ft.Text("Success 0 | Skipped 0 | Failed 0 | hotspot -", size=12, color="#334155")
        report_text = ft.Text("", size=11, color="#64748B", selectable=True)
        badge = ft.Text("", size=11, color="#D97706", weight=ft.FontWeight.W_600, visible=False)

        extra_controls: list[ft.Control] = []
        card_refs: dict[str, Any] = {
            "input": input_field,
            "status": status_text,
            "message": message_text,
            "progress": progress_text,
            "metrics": metrics_text,
            "report": report_text,
            "badge": badge,
        }

        if platform_id == "qq":
            mflac = ft.Dropdown(label="mflac output", value=str(config["qq"]["format_rules"].get("mflac", "flac")), options=_format_options(auto=False), dense=True, filled=True, bgcolor="#FFFFFF", border_radius=6)
            mgg = ft.Dropdown(label="mgg output", value=str(config["qq"]["format_rules"].get("mgg", "ogg")), options=_format_options(auto=False), dense=True, filled=True, bgcolor="#FFFFFF", border_radius=6)
            mmp4 = ft.Dropdown(label="mmp4 output", value=str(config["qq"]["format_rules"].get("mmp4", "m4a")), options=_format_options(auto=False), dense=True, filled=True, bgcolor="#FFFFFF", border_radius=6)
            card_refs.update({"mflac": mflac, "mgg": mgg, "mmp4": mmp4})
            extra_controls.extend([mflac, mgg, mmp4])
        elif platform_id == "kuwo":
            format_kwm = ft.Dropdown(label="kwm output", value=str(config["kuwo"].get("format_kwm", "auto")), options=_format_options(auto=True), dense=True, filled=True, bgcolor="#FFFFFF", border_radius=6)
            card_refs["format_kwm"] = format_kwm
            extra_controls.append(format_kwm)
        else:
            key_file = ft.TextField(label="kugou_key.xz", value=str(config["kugou"].get("key_file", auto_find_kugou_key(paths) or "")), dense=True, filled=True, bgcolor="#FFFFFF", border_radius=6)
            kgg_db_path = ft.TextField(label="KGMusicV3.db", value=str(config["kugou"].get("kgg_db_path", auto_find_kgg_db_path() or "")), dense=True, filled=True, bgcolor="#FFFFFF", border_radius=6)
            format_kgma = ft.Dropdown(label="kgma/kgm/vpr output", value=str(config["kugou"].get("target_format_kgma", "auto")), options=_format_options(auto=True), dense=True, filled=True, bgcolor="#FFFFFF", border_radius=6)
            format_kgg = ft.Dropdown(label="kgg output", value=str(config["kugou"].get("target_format_kgg", "auto")), options=_format_options(auto=True), dense=True, filled=True, bgcolor="#FFFFFF", border_radius=6)
            card_refs.update({
                "key_file": key_file,
                "kgg_db_path": kgg_db_path,
                "format_kgma": format_kgma,
                "format_kgg": format_kgg,
            })
            extra_controls.extend([key_file, kgg_db_path, format_kgma, format_kgg])

        start_button = ft.ElevatedButton(
            f"Start {title}",
            icon=ft.Icons.PLAY_ARROW_ROUNDED,
            bgcolor=accent,
            color="#FFFFFF",
            on_click=lambda _: start_platform(platform_id),
        )
        card_refs["start_button"] = start_button

        container = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Text(title, size=20, weight=ft.FontWeight.W_600, color="#111827"),
                            badge,
                            ft.Container(expand=True),
                            status_text,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    message_text,
                    input_field,
                    *extra_controls,
                    progress_text,
                    metrics_text,
                    report_text,
                    ft.Row([start_button], alignment=ft.MainAxisAlignment.END),
                ],
                spacing=12,
            ),
            bgcolor="#FFFFFF",
            padding=16,
            border_radius=10,
            border=ft.border.all(1, "#D1D5DB"),
            shadow=ft.BoxShadow(blur_radius=18, color="#14000000", offset=ft.Offset(0, 8)),
            animate=ft.Animation(220, ft.AnimationCurve.EASE_OUT_CUBIC),
            animate_scale=ft.Animation(220, ft.AnimationCurve.EASE_OUT_CUBIC),
            animate_offset=ft.Animation(220, ft.AnimationCurve.EASE_OUT_CUBIC),
        )
        card_refs["container"] = container
        platform_cards[platform_id] = card_refs
        return container

    about_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("About QKKDecrypt"),
        content=ft.Column(
            controls=[
                ft.Text(f"{PROJECT_NAME_EN} | {PROJECT_NAME_ZH}", weight=ft.FontWeight.W_600),
                ft.Text(f"Project path: {PROJECT_ADDRESS}"),
                ft.Text(f"QQ: {PROJECT_QQ}"),
                ft.Text(FLET_NOTE),
                ft.Text(QQMUSIC_ATTRIBUTION),
                ft.Text("Other platform models are independently studied for learning and interoperability only."),
                ft.Text(LEGAL_NOTICE, color="#B91C1C"),
            ],
            tight=True,
            spacing=8,
        ),
        actions=[ft.TextButton("Close", on_click=lambda _: _close_dialog(about_dialog))],
    )

    save_button = ft.FilledButton(
        "Save config",
        icon=ft.Icons.SAVE_ROUNDED,
        on_click=lambda _: (save_current_config(), _show_snackbar(page, "Configuration saved.")),
    )
    about_button = ft.OutlinedButton(
        "About",
        icon=ft.Icons.INFO_OUTLINE_ROUNDED,
        on_click=lambda _: _open_about(),
    )

    def _open_about() -> None:
        page.dialog = about_dialog
        about_dialog.open = True
        page.update()

    page.add(
        ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Text(PROJECT_NAME_EN, size=30, weight=ft.FontWeight.W_700, color="#111827"),
                                    ft.Text(PROJECT_NAME_ZH, size=18, color="#334155"),
                                    ft.Text(f"Project path: {PROJECT_ADDRESS} | QQ: {PROJECT_QQ}", color="#475569"),
                                    ft.Text(LEGAL_NOTICE, color="#B91C1C"),
                                ],
                                spacing=4,
                            ),
                            ft.Container(expand=True),
                            about_button,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                shared_output,
                                recursive_switch,
                                queue_summary,
                                save_button,
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        bgcolor="#FFFFFF",
                        padding=16,
                        border_radius=10,
                        shadow=ft.BoxShadow(blur_radius=18, color="#10000000", offset=ft.Offset(0, 8)),
                    ),
                    ft.ResponsiveRow(
                        controls=[
                            ft.Container(build_platform_card("qq"), col={"sm": 12, "md": 6, "xl": 4}),
                            ft.Container(build_platform_card("kuwo"), col={"sm": 12, "md": 6, "xl": 4}),
                            ft.Container(build_platform_card("kugou"), col={"sm": 12, "md": 12, "xl": 4}),
                        ],
                        run_spacing=16,
                        spacing=16,
                    ),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text("Runtime log", size=18, weight=ft.FontWeight.W_600, color="#111827"),
                                ft.Container(log_view, height=230),
                            ],
                            spacing=10,
                        ),
                        bgcolor="#FFFFFF",
                        padding=16,
                        border_radius=10,
                        shadow=ft.BoxShadow(blur_radius=18, color="#10000000", offset=ft.Offset(0, 8)),
                    ),
                ],
                spacing=18,
                scroll=ft.ScrollMode.AUTO,
            ),
            expand=True,
        )
    )

    refresh_cards(scheduler.snapshot())
    push_log("[ready] UI started. Max concurrent platform tasks: 2. Remaining tasks use FIFO queue.")
