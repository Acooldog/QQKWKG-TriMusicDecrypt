from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProcessMatch:
    pid: int
    name: str
    exe_path: str


def _run_powershell_json(script: str) -> list[dict[str, Any]]:
    command = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        return []
    text = (completed.stdout or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _normalize_name(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized.endswith(".exe"):
        normalized = normalized[:-4]
    return normalized


def _single_quoted(value: str) -> str:
    return value.replace("'", "''")


def _build_cim_query_script(query_script: str) -> str:
    return (
        "$ErrorActionPreference='SilentlyContinue'; "
        f"$procs = @({query_script}); "
        "@($procs | Sort-Object ProcessId | ForEach-Object { "
        "  $path=''; "
        "  try { if($_.ExecutablePath){ $path = [string]$_.ExecutablePath } } catch {} "
        "  [pscustomobject]@{pid=$_.ProcessId;name=$_.Name;exe_path=$path} "
        "}) | ConvertTo-Json -Compress"
    )


def _query_cim_processes(query_script: str) -> list[ProcessMatch]:
    rows = _run_powershell_json(_build_cim_query_script(query_script))
    matches: list[ProcessMatch] = []
    for row in rows:
        pid = int(row.get("pid", 0) or 0)
        if pid <= 0:
            continue
        name = str(row.get("name", "") or "")
        if name and not name.lower().endswith(".exe"):
            name = f"{name}.exe"
        matches.append(ProcessMatch(pid=pid, name=name, exe_path=str(row.get("exe_path", "") or "")))
    return matches


def _query_processes_by_substrings(fragments: Iterable[str]) -> list[ProcessMatch]:
    normalized: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        value = _normalize_name(str(fragment or ""))
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    if not normalized:
        return []
    predicates: list[str] = []
    for fragment in normalized:
        escaped = _single_quoted(fragment)
        predicates.append(f"$name.Contains('{escaped}')")
        predicates.append(f"$path.Contains('{escaped}')")
    query_script = (
        "Get-CimInstance Win32_Process | Where-Object { "
        "  $name = ([string]$_.Name).ToLowerInvariant(); "
        "  $path = ([string]$_.ExecutablePath).ToLowerInvariant(); "
        f"  {' -or '.join(predicates)} "
        "}"
    )
    return _query_cim_processes(query_script)


def find_process_by_substrings(fragments: Iterable[str]) -> ProcessMatch | None:
    results = _query_processes_by_substrings(fragments)
    return results[-1] if results else None


def find_process_by_substring(fragment: str) -> ProcessMatch | None:
    return find_process_by_substrings([fragment])


def find_process_by_name(process_name: str) -> ProcessMatch | None:
    target = _normalize_name(process_name)
    if not target:
        return None
    target = f"{target}.exe"
    escaped_target = _single_quoted(target)
    results = _query_cim_processes(f"Get-CimInstance Win32_Process -Filter \"Name = '{escaped_target}'\"")
    for item in reversed(results):
        if item.name.lower() == target:
            return item
    return results[-1] if results else None
