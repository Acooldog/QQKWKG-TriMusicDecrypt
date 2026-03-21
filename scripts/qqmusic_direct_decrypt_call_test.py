import argparse
import json
import os
import sys
import time
from pathlib import Path

import frida


QQ_RVAS = {
    "decrypt_cache_file": 0x1845C0,
}


def find_qqmusic_pid() -> int | None:
    for line in os.popen('tasklist /FI "IMAGENAME eq QQMusic.exe" /FO CSV /NH').read().splitlines():
        if "QQMusic.exe" not in line:
            continue
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) > 1:
            return int(parts[1])
    return None


def load_candidate(log_path: Path) -> tuple[str, str]:
    for line in log_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("kind") == "internal_enter" and rec.get("name") == "QQMusic.dll+decrypt_cache_file":
            cand = rec.get("replay_candidate") or {}
            a0 = cand.get("arg0_source_path_field", {}).get("bytes_hex")
            a1 = cand.get("arg1_output_path_field", {}).get("bytes_hex")
            if a0 and a1:
                return a0, a1
    raise RuntimeError(f"no decrypt_cache_file replay candidate found in {log_path}")


def build_script_source(config: dict) -> str:
    payload = json.dumps(config, ensure_ascii=False)
    return f"""
const config = {payload};

function sendEvent(kind, payload) {{
  send(Object.assign({{ kind, ts: Date.now() / 1000 }}, payload || {{}}));
}}

function hookCreateFile(moduleName) {{
  const mod = Process.findModuleByName(moduleName);
  if (!mod) return;
  const addr = mod.findExportByName('CreateFileW');
  if (!addr) return;
  Interceptor.attach(addr, {{
    onEnter(args) {{
      this.path = null;
      try {{
        this.path = args[0].readUtf16String();
      }} catch (_) {{}}
      if (!this.path) return;
      const lower = this.path.toLowerCase();
      if (
        lower.indexOf('qqmusiccache') === -1 &&
        lower.indexOf('direct-call-test') === -1 &&
        lower.indexOf('music') === -1
      ) {{
        this.path = null;
        return;
      }}
      sendEvent('CreateFileW_enter', {{
        module: moduleName,
        path: this.path,
        access: args[1].toUInt32(),
        creation: args[4].toUInt32(),
      }});
    }},
    onLeave(retval) {{
      if (!this.path) return;
      sendEvent('CreateFileW_leave', {{
        module: moduleName,
        path: this.path,
        retval: retval.toString(),
      }});
    }},
  }});
}}

function hexToBytes(hex) {{
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {{
    out[i] = parseInt(hex.substr(i * 2, 2), 16);
  }}
  return out;
}}

if (config.trace_files) {{
  hookCreateFile('KernelBase.dll');
  hookCreateFile('KERNEL32.DLL');
}}

const mod = Process.findModuleByName('QQMusic.dll');
if (!mod) {{
  sendEvent('fatal', {{ reason: 'QQMusic.dll not loaded' }});
}} else {{
  const fnAddr = mod.base.add(config.rva);
  sendEvent('target', {{
    name: config.name,
    address: fnAddr.toString(),
    base: mod.base.toString(),
    rva: '0x' + config.rva.toString(16),
  }});

  const srcObj = Memory.alloc(config.arg0_size);
  const outObj = Memory.alloc(config.arg1_size);
  srcObj.writeByteArray(hexToBytes(config.arg0_hex));
  outObj.writeByteArray(hexToBytes(config.arg1_hex));

  const cachePath = Memory.allocUtf16String(config.source_cache_path);
  const outPath = Memory.allocUtf16String(config.output_path);
  const coverPath = Memory.allocUtf16String(config.cover_path);

  srcObj.writePointer(cachePath);
  outObj.writePointer(outPath);
  outObj.add(4).writePointer(coverPath);

  sendEvent('objects_ready', {{
    src: srcObj.toString(),
    out: outObj.toString(),
    source_cache_path: config.source_cache_path,
    output_path: config.output_path,
    cover_path: config.cover_path,
  }});

  try {{
    const fn = new NativeFunction(fnAddr, 'uint32', ['pointer', 'pointer'], 'stdcall');
    const rv = fn(srcObj, outObj);
    sendEvent('invoke_result', {{ retval: rv }});
  }} catch (e) {{
    sendEvent('invoke_error', {{ error: String(e) }});
  }}
}}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Foreground direct call experiment for QQ decrypt_cache_file.")
    parser.add_argument("--candidate-log")
    parser.add_argument("--arg0-hex")
    parser.add_argument("--arg1-hex")
    parser.add_argument("--source-cache-path", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--cover-path", required=True)
    parser.add_argument("--settle-seconds", type=float, default=8.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--stable-rounds", type=int, default=4)
    parser.add_argument("--grace-seconds", type=float, default=6.0)
    parser.add_argument("--trace-files", action="store_true")
    parser.add_argument("--json-summary", action="store_true")
    parser.add_argument(
        "--log-dir",
        default=str(Path(__file__).resolve().parents[1] / "_log" / "direct_call_test"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pid = find_qqmusic_pid()
    if not pid:
        print("QQMusic.exe is not running", file=sys.stderr)
        return 2

    if args.arg0_hex and args.arg1_hex:
        arg0_hex, arg1_hex = args.arg0_hex, args.arg1_hex
    elif args.candidate_log:
        arg0_hex, arg1_hex = load_candidate(Path(args.candidate_log))
    else:
        raise SystemExit("either --candidate-log or --arg0-hex/--arg1-hex is required")

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    started_at = int(time.time())
    log_path = log_dir / f"decrypt_cache_file_test_{started_at}.jsonl"
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    cfg = {
        "name": "decrypt_cache_file",
        "rva": QQ_RVAS["decrypt_cache_file"],
        "arg0_hex": arg0_hex,
        "arg1_hex": arg1_hex,
        "arg0_size": len(arg0_hex) // 2,
        "arg1_size": len(arg1_hex) // 2,
        "source_cache_path": args.source_cache_path,
        "output_path": str(output_path),
        "cover_path": args.cover_path,
        "trace_files": bool(args.trace_files),
    }

    session = None
    fh = None
    last_size = -1
    stable_rounds = 0
    deadline = time.time() + max(args.settle_seconds, 0.0)
    try:
        device = frida.get_local_device()
        session = device.attach(pid)
        script = session.create_script(build_script_source(cfg))
        fh = log_path.open("w", encoding="utf-8")

        def on_message(message, data):
            record = message["payload"] if message["type"] == "send" else message
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()

        script.on("message", on_message)
        script.load()
        while time.time() < deadline:
            if output_path.exists():
                size = output_path.stat().st_size
                if size > 0 and size == last_size:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                    last_size = size
                if stable_rounds >= max(args.stable_rounds, 1):
                    break
            time.sleep(max(args.poll_interval, 0.1))
        if args.grace_seconds > 0:
            time.sleep(args.grace_seconds)
    finally:
        if fh:
            fh.close()
        if session:
            try:
                session.detach()
            except Exception:
                pass

    summary = {
        "log": str(log_path),
        "output_exists": output_path.exists(),
        "output_size": output_path.stat().st_size if output_path.exists() else 0,
        "output_path": str(output_path),
        "pid": pid,
    }
    if args.json_summary:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"log: {log_path}")
        print(f"output_exists: {output_path.exists()}")
        if output_path.exists():
            print(f"output_size: {output_path.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
