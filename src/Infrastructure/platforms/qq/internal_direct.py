from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import frida

logger = logging.getLogger("qkkdecrypt.infrastructure.qq_internal_direct")


@dataclass(slots=True)
class QQInternalDirectResult:
    status: str
    staged_path: str | None = None
    source_cache_path: str | None = None
    original_output_path: str | None = None
    cover_path: str | None = None
    message: str = ""


class QQInternalDirectDecryptService:
    """Reuse QQMusic's live internal decrypt route by redirecting its FLAC output path."""

    DECRYPT_CACHE_FILE_RVA = 0x1845C0
    QUALITY_SUFFIX_RE = re.compile(r"_[A-Za-z0-9]{1,8}(?:\(\d+\))?$")
    CACHE_DIR = Path(r"K:\QQMusicCache\QMDL")
    PICTURE_DIR = Path(r"K:\QQMusicCache\QQMusicPicture")
    ACTIVE_ARG0_HEX = "582f83260300000000000000a40f8979a40f897901000000020000000500000014d46600e81c7723020000000000000001000000e27c3700e3000000ffffffff"
    ACTIVE_ARG1_HEX = "802c8326e0a9811d00000000a40f897901000000c09cf80915d93301cada330172d93301000000002067a61400000000a40f8979c88095c2262700907818e67b"

    def __init__(self, *, timeout_seconds: float = 15.0):
        self.timeout_seconds = timeout_seconds

    def stage_internal_flac(self, source_file_path: str, stage_path: str, *, wait_seconds: float | None = None) -> QQInternalDirectResult:
        timeout = self.timeout_seconds if wait_seconds is None else max(wait_seconds, 0.0)
        pid = self._find_qqmusic_pid()
        if pid is None:
            return QQInternalDirectResult(status="qq_not_running", message="QQMusic.exe is not running")

        sample = Path(source_file_path)
        target = Path(stage_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass

        artist_hint, title_hint = self._derive_title_hints(sample)
        passive = self._stage_live_redirect(sample=sample, target=target, timeout=timeout, artist_hint=artist_hint, title_hint=title_hint, pid=pid)
        if passive.status == "staged":
            return passive

        active = self._stage_active_direct(sample=sample, target=target, pid=pid)
        if active.status == "staged":
            return active
        if passive.status in {"attach_failed", "hook_error"}:
            return passive
        return active if active.status != "active_context_missing" else passive

    @staticmethod
    def _as_text(value: object) -> str | None:
        if value is None:
            return None
        return str(value)

    def _stage_live_redirect(
        self,
        *,
        sample: Path,
        target: Path,
        timeout: float,
        artist_hint: str,
        title_hint: str,
        pid: int,
    ) -> QQInternalDirectResult:
        session = None
        script = None
        result: dict[str, object] = {"status": "timeout"}
        script_error: dict[str, str] = {}

        try:
            session = frida.attach(pid)
            script = session.create_script(
                self._build_script_source(
                    sample_name=sample.name,
                    artist_hint=artist_hint,
                    title_hint=title_hint,
                    output_path=str(target),
                )
            )

            def on_message(message, _data):
                if message.get("type") == "send":
                    payload = message.get("payload", {})
                    kind = payload.get("kind")
                    if kind == "redirect_applied":
                        result.update(
                            {
                                "status": "redirect_applied",
                                "source_cache_path": payload.get("src_path"),
                                "original_output_path": payload.get("original_output"),
                                "cover_path": payload.get("cover_path"),
                            }
                        )
                    elif kind == "redirect_result":
                        result.update(
                            {
                                "status": "staged" if payload.get("retval") == 1 else "invoke_failed",
                                "staged_path": payload.get("final_output"),
                                "source_cache_path": payload.get("src_path"),
                                "cover_path": payload.get("cover_path"),
                                "retval": payload.get("retval"),
                            }
                        )
                elif message.get("type") == "error":
                    script_error["message"] = message.get("description") or message.get("stack") or "script error"

            script.on("message", on_message)
            script.load()

            deadline = time.time() + timeout
            while time.time() < deadline:
                if result.get("status") == "staged":
                    break
                if script_error:
                    break
                time.sleep(0.2)
        except Exception as exc:
            logger.exception("QQ internal direct decrypt attach failed")
            return QQInternalDirectResult(status="attach_failed", message=str(exc))
        finally:
            if script is not None:
                try:
                    script.unload()
                except Exception:
                    pass
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass

        if script_error:
            return QQInternalDirectResult(status="hook_error", message=script_error["message"])

        status = str(result.get("status") or "timeout")
        if status == "staged":
            staged_path = str(result.get("staged_path") or target)
            if Path(staged_path).exists() and Path(staged_path).stat().st_size > 1024:
                logger.info("QQ internal direct decrypt staged: %s", staged_path)
                return QQInternalDirectResult(
                    status="staged",
                    staged_path=staged_path,
                    source_cache_path=self._as_text(result.get("source_cache_path")),
                    original_output_path=self._as_text(result.get("original_output_path")),
                    cover_path=self._as_text(result.get("cover_path")),
                    message="reused QQMusic live decrypt route",
                )
            return QQInternalDirectResult(
                status="output_missing",
                staged_path=staged_path,
                source_cache_path=self._as_text(result.get("source_cache_path")),
                message="QQ internal decrypt reported success but output file was not found",
            )

        if status == "invoke_failed":
            return QQInternalDirectResult(
                status="invoke_failed",
                source_cache_path=self._as_text(result.get("source_cache_path")),
                message=f"QQ internal decrypt returned {result.get('retval')}",
            )

        if status == "redirect_applied":
            return QQInternalDirectResult(
                status="output_missing",
                source_cache_path=self._as_text(result.get("source_cache_path")),
                original_output_path=self._as_text(result.get("original_output_path")),
                cover_path=self._as_text(result.get("cover_path")),
                message="QQ internal decrypt started but no completed output was observed",
            )

        return QQInternalDirectResult(status="timeout", message="QQ internal decrypt did not trigger within timeout")

    def _stage_active_direct(self, *, sample: Path, target: Path, pid: int) -> QQInternalDirectResult:
        source_cache_path = self._find_source_cache_path(sample)
        if source_cache_path is None:
            return QQInternalDirectResult(status="active_context_missing", message="QQ internal direct source cache was not found")

        target.parent.mkdir(parents=True, exist_ok=True)
        temp_ascii_dir = target.parent / "_qq_internal_direct"
        temp_ascii_dir.mkdir(parents=True, exist_ok=True)
        source_ext = source_cache_path.suffix or ".mflac"
        active_source = temp_ascii_dir / f"source{source_ext}"
        active_output = temp_ascii_dir / "output.flac"
        active_cover: Path | None = None
        if active_output.exists():
            try:
                active_output.unlink()
            except OSError:
                pass
        try:
            shutil.copyfile(source_cache_path, active_source)
        except OSError as exc:
            return QQInternalDirectResult(status="output_missing", source_cache_path=str(source_cache_path), message=f"failed to stage QQ cache source: {exc}")

        cover_path = self._pick_cover_path()
        try:
            active_cover = temp_ascii_dir / cover_path.name
            shutil.copyfile(cover_path, active_cover)
        except OSError:
            active_cover = None
        if active_cover is None or not active_cover.exists():
            fallback_cover = temp_ascii_dir / "cover.png"
            try:
                shutil.copyfile(cover_path, fallback_cover)
                active_cover = fallback_cover
            except OSError:
                active_cover = cover_path
        helper = self._run_active_helper(
            source_cache_path=active_source,
            output_path=active_output,
            cover_path=Path(str(active_cover)),
        )
        if helper["status"] == "attach_failed":
            return QQInternalDirectResult(status="attach_failed", source_cache_path=str(source_cache_path), cover_path=str(active_cover), message=str(helper.get("message") or "helper attach failed"))
        if helper["status"] == "hook_error":
            return QQInternalDirectResult(status="hook_error", source_cache_path=str(source_cache_path), cover_path=str(active_cover), message=str(helper.get("message") or "helper hook error"))
        if helper["status"] == "invoke_failed":
            return QQInternalDirectResult(status="invoke_failed", source_cache_path=str(source_cache_path), cover_path=str(active_cover), message=str(helper.get("message") or "QQ direct decrypt returned 0"))
        if helper["status"] == "staged" and active_output.exists() and active_output.stat().st_size > 1024:
            try:
                shutil.copyfile(active_output, target)
            except OSError as exc:
                return QQInternalDirectResult(
                    status="output_missing",
                    source_cache_path=str(source_cache_path),
                    cover_path=str(active_cover),
                    message=f"QQ direct decrypt produced a staged file but final copy failed: {exc}",
                )
            return QQInternalDirectResult(
                status="staged",
                staged_path=str(target),
                source_cache_path=str(source_cache_path),
                original_output_path=str(active_output),
                cover_path=str(active_cover),
                message="triggered QQ internal decrypt_cache_file directly",
            )
        return QQInternalDirectResult(status="output_missing", source_cache_path=str(source_cache_path), cover_path=str(active_cover), message="QQ direct decrypt call did not produce output")

    @staticmethod
    def _find_qqmusic_pid() -> int | None:
        for line in os.popen('tasklist /FI "IMAGENAME eq QQMusic.exe" /FO CSV /NH').read().splitlines():
            if "QQMusic.exe" not in line:
                continue
            parts = [p.strip('"') for p in line.split(",")]
            if len(parts) > 1:
                return int(parts[1])
        return None

    @classmethod
    def _normalize_stem(cls, text: str) -> str:
        stem = cls.QUALITY_SUFFIX_RE.sub("", text)
        stem = stem.lower().replace("_", " ").replace("-", " ")
        return " ".join(stem.split())

    @classmethod
    def _derive_title_hints(cls, sample: Path) -> tuple[str, str]:
        stem = sample.stem
        artist = ""
        title = stem
        if " - " in stem:
            artist, title = stem.split(" - ", 1)
        title = cls.QUALITY_SUFFIX_RE.sub("", title)
        return artist.strip(), title.strip()

    @classmethod
    def _find_source_cache_path(cls, sample: Path) -> Path | None:
        if not cls.CACHE_DIR.exists():
            return None
        artist_hint, title_hint = cls._derive_title_hints(sample)
        target_stem = cls._normalize_stem(f"{artist_hint} {title_hint}".strip())
        candidates = []
        for path in cls.CACHE_DIR.iterdir():
            if not path.is_file():
                continue
            norm = cls._normalize_stem(path.stem)
            if target_stem and (target_stem == norm or title_hint.lower() in norm):
                candidates.append(path)
        if candidates:
            return candidates[0]
        files = [p for p in cls.CACHE_DIR.iterdir() if p.is_file()]
        return files[0] if len(files) == 1 else None

    @classmethod
    def _pick_cover_path(cls) -> Path:
        if cls.PICTURE_DIR.exists():
            default_png = cls.PICTURE_DIR / "Albumdefault.png"
            if default_png.exists():
                return default_png
            preferred = sorted(p for p in cls.PICTURE_DIR.glob("*_4.jpg") if p.is_file())
            if preferred:
                return preferred[0]
            files = [p for p in cls.PICTURE_DIR.iterdir() if p.is_file()]
            if files:
                return files[0]
        return Path("Albumdefault.png")

    @classmethod
    def _run_active_helper(cls, *, source_cache_path: Path, output_path: Path, cover_path: Path) -> dict[str, object]:
        repo_root = Path(__file__).resolve().parents[4]
        helper_script = repo_root / "scripts" / "qqmusic_direct_decrypt_call_test.py"
        python_exe = repo_root / ".venv" / "Scripts" / "python.exe"
        if not python_exe.exists():
            python_exe = Path(sys.executable)
        if not helper_script.exists():
            return {"status": "hook_error", "message": f"helper script missing: {helper_script}"}
        try:
            proc = subprocess.run(
                [
                    str(python_exe),
                    str(helper_script),
                    "--arg0-hex",
                    cls.ACTIVE_ARG0_HEX,
                    "--arg1-hex",
                    cls.ACTIVE_ARG1_HEX,
                    "--source-cache-path",
                    str(source_cache_path),
                    "--output-path",
                    str(output_path),
                    "--cover-path",
                    str(cover_path),
                    "--settle-seconds",
                    "10",
                    "--stable-rounds",
                    "4",
                    "--grace-seconds",
                    "8",
                    "--json-summary",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except subprocess.TimeoutExpired:
            return {"status": "hook_error", "message": "QQ internal direct helper timed out"}
        if proc.returncode != 0:
            return {"status": "hook_error", "message": proc.stderr.strip() or proc.stdout.strip() or f"helper exited with {proc.returncode}"}
        summary_text = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}"
        try:
            summary = json.loads(summary_text)
        except json.JSONDecodeError:
            return {"status": "hook_error", "message": f"invalid helper output: {summary_text}"}
        if summary.get("output_exists"):
            return {"status": "staged", **summary}
        return {"status": "invoke_failed", "message": "helper completed but no output was produced", **summary}

    @classmethod
    def _build_script_source(cls, *, sample_name: str, artist_hint: str, title_hint: str, output_path: str) -> str:
        return f"""
const decryptRva = {cls.DECRYPT_CACHE_FILE_RVA};
const sampleName = {json.dumps(sample_name, ensure_ascii=False)};
const artistHint = {json.dumps(artist_hint, ensure_ascii=False)};
const titleHint = {json.dumps(title_hint, ensure_ascii=False)};
const targetOutputPath = {json.dumps(output_path, ensure_ascii=False)};
let redirectDone = false;
let allocatedOutput = null;

function sendEvent(kind, payload) {{
  send(Object.assign({{ kind, ts: Date.now() / 1000 }}, payload || {{}}));
}}

function normalizeText(s) {{
  if (!s) return '';
  return String(s).toLowerCase().replace(/[_\\-]/g, ' ').replace(/\\s+/g, ' ').trim();
}}

function tryUtf16(ptr) {{
  try {{
    if (!ptr || ptr.isNull()) return null;
    const s = ptr.readUtf16String();
    if (!s || s.length === 0 || s.length > 520) return null;
    return s;
  }} catch (_) {{ return null; }}
}}

function safeReadPointer(ptr) {{
  try {{ return ptr.readPointer(); }} catch (_) {{ return null; }}
}}

function extractStringField(base, off) {{
  if (!base || base.isNull()) return null;
  try {{
    const slot = base.add(off);
    const p1 = safeReadPointer(slot);
    const s1 = tryUtf16(p1);
    if (s1) return s1;
    const direct = tryUtf16(slot);
    if (direct) return direct;
  }} catch (_) {{}}
  return null;
}}

function matchesSample(srcPath, outPath) {{
  const srcNorm = normalizeText(srcPath);
  const outNorm = normalizeText(outPath);
  const sampleNorm = normalizeText(sampleName);
  const titleNorm = normalizeText(titleHint);
  const artistNorm = normalizeText(artistHint);
  if (sampleNorm && (srcNorm.indexOf(sampleNorm) !== -1 || outNorm.indexOf(sampleNorm) !== -1)) return true;
  if (titleNorm && (srcNorm.indexOf(titleNorm) !== -1 || outNorm.indexOf(titleNorm) !== -1)) return true;
  if (artistNorm && (srcNorm.indexOf(artistNorm) !== -1 || outNorm.indexOf(artistNorm) !== -1)) return true;
  return false;
}}

const mod = Process.findModuleByName('QQMusic.dll');
if (!mod) {{
  sendEvent('fatal', {{ reason: 'QQMusic.dll not loaded' }});
}} else {{
  const fnAddr = mod.base.add(decryptRva);
  Interceptor.attach(fnAddr, {{
    onEnter(args) {{
      this.srcObj = args[0];
      this.outObj = args[1];
      this.srcPath = extractStringField(this.srcObj, 0x0);
      this.outPath = extractStringField(this.outObj, 0x0);
      this.coverPath = extractStringField(this.outObj, 0x4);
      if (!redirectDone && matchesSample(this.srcPath, this.outPath)) {{
        allocatedOutput = Memory.allocUtf16String(targetOutputPath);
        this.outObj.writePointer(allocatedOutput);
        redirectDone = true;
        this.redirected = true;
        sendEvent('redirect_applied', {{
          src_path: this.srcPath,
          original_output: this.outPath,
          new_output: targetOutputPath,
          cover_path: this.coverPath,
        }});
      }}
    }},
    onLeave(retval) {{
      if (this.redirected) {{
        sendEvent('redirect_result', {{
          retval: Number(retval.toUInt32()),
          src_path: this.srcPath,
          final_output: targetOutputPath,
          cover_path: this.coverPath,
        }});
      }}
    }}
  }});
}}
"""

    @classmethod
    def _build_active_script_source(cls, *, source_cache_path: str, output_path: str, cover_path: str) -> str:
        return f"""
const decryptRva = {cls.DECRYPT_CACHE_FILE_RVA};
const arg0Hex = {json.dumps(cls.ACTIVE_ARG0_HEX)};
const arg1Hex = {json.dumps(cls.ACTIVE_ARG1_HEX)};
const sourceCachePath = {json.dumps(source_cache_path, ensure_ascii=False)};
const outputPath = {json.dumps(output_path, ensure_ascii=False)};
const coverPath = {json.dumps(cover_path, ensure_ascii=False)};

function sendEvent(kind, payload) {{
  send(Object.assign({{ kind, ts: Date.now() / 1000 }}, payload || {{}}));
}}

function hexToBytes(hex) {{
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {{
    out[i] = parseInt(hex.substr(i * 2, 2), 16);
  }}
  return out;
}}

const mod = Process.findModuleByName('QQMusic.dll');
if (!mod) {{
  sendEvent('fatal', {{ reason: 'QQMusic.dll not loaded' }});
}} else {{
  const fnAddr = mod.base.add(decryptRva);
  const srcObj = Memory.alloc(arg0Hex.length / 2);
  const outObj = Memory.alloc(arg1Hex.length / 2);
  srcObj.writeByteArray(hexToBytes(arg0Hex));
  outObj.writeByteArray(hexToBytes(arg1Hex));
  srcObj.writePointer(Memory.allocUtf16String(sourceCachePath));
  outObj.writePointer(Memory.allocUtf16String(outputPath));
  outObj.add(4).writePointer(Memory.allocUtf16String(coverPath));
  try {{
    const fn = new NativeFunction(fnAddr, 'uint32', ['pointer', 'pointer'], 'stdcall');
    const rv = fn(srcObj, outObj);
    sendEvent('invoke_result', {{ retval: rv }});
  }} catch (e) {{
    sendEvent('invoke_error', {{ error: String(e) }});
  }}
}}
"""

