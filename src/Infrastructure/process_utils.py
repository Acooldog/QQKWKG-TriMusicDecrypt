from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProcessMatch:
    pid: int
    name: str
    exe_path: str


def _run_powershell_json(script: str) -> list[dict[str, Any]]:
    command = ["powershell.exe", "-NoProfile", "-Command", script]
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


def _query_processes(filter_script: str) -> list[ProcessMatch]:
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        f"$procs = {filter_script}; "
        "$rows=@(); "
        "foreach($p in $procs){ "
        "  $path=''; "
        "  try { $path = $p.Path } catch {} "
        "  if(-not $path){ try { $path = $p.MainModule.FileName } catch {} } "
        "  if(-not $path){ try { $cim = Get-CimInstance Win32_Process -Filter (\"ProcessId = \" + $p.Id); if($cim){ $path = $cim.ExecutablePath } } catch {} } "
        "  $rows += [pscustomobject]@{pid=$p.Id;name=$p.ProcessName;exe_path=$path} "
        "} "
        "$rows | Sort-Object pid | ConvertTo-Json -Compress"
    )
    rows = _run_powershell_json(script)
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


def find_process_by_substring(fragment: str) -> ProcessMatch | None:
    value = (fragment or "").strip().lower()
    if not value:
        return None
    for item in reversed(_query_processes("Get-Process")):
        if value in item.name.lower():
            return item
    return None


def find_process_by_name(process_name: str) -> ProcessMatch | None:
    target = (process_name or "").strip().lower()
    if not target:
        return None
    if target.endswith(".exe"):
        query_name = target[:-4]
    else:
        query_name = target
        target = f"{target}.exe"
    results = _query_processes(f"Get-Process -Name '{query_name}'")
    for item in reversed(results):
        if item.name.lower() == target:
            return item
    return results[-1] if results else None
