from __future__ import annotations

import argparse
import json
import pathlib
import tempfile
import time

import frida

from src.Infrastructure.platforms.qq.runtime.qqmusic_decrypt import QQMusicDecryptor


QQMUSIC_COMMON_OFFSETS = {
    "SetEkey": 0x00013582,
    "ReadExtend": 0x0001DF5C,
    "GetVkeyCache": 0x0001DD5C,
}


def find_qqmusic_process():
    device = frida.get_local_device()
    for proc in device.enumerate_processes():
        if "qqmusic" in proc.name.lower():
            return device, proc
    raise RuntimeError("QQMusic.exe is not running")


def build_hook_script() -> str:
    offsets_json = json.dumps(QQMUSIC_COMMON_OFFSETS)
    return r'''
function hexBytes(ptrValue, size) {
  try {
    return Array.from(new Uint8Array(Memory.readByteArray(ptr(ptrValue), size)))
      .map(b => ('0' + b.toString(16)).slice(-2)).join('');
  } catch (e) {
    return '';
  }
}

function utf16At(p) {
  try { return ptr(p).readUtf16String(); } catch (e) { return null; }
}

function ansiAt(p) {
  try { return ptr(p).readCString(); } catch (e) { return null; }
}

function likelyStringsFromBlob(blobHex) {
  const out = [];
  try {
    const bytes = blobHex.match(/../g).map(h => parseInt(h, 16));
    const buf = new Uint8Array(bytes);
    for (let i = 0; i < buf.length - 4; i += 2) {
      let chars = [];
      let j = i;
      while (j + 1 < buf.length) {
        const code = buf[j] | (buf[j + 1] << 8);
        if (code === 0) break;
        if (code < 0x20 || code > 0x7e) {
          chars = [];
          break;
        }
        chars.push(String.fromCharCode(code));
        j += 2;
      }
      if (chars.length >= 6) {
        const s = chars.join('');
        if (out.indexOf(s) === -1) out.push(s);
      }
    }
  } catch (e) {}
  return out;
}

function dumpStringObject(objPtr) {
  const result = { object: ptr(objPtr).toString(), candidates: {} };
  const base = ptr(objPtr);
  const directUtf16 = utf16At(base);
  if (directUtf16) result.candidates.direct_utf16 = directUtf16;
  const directAnsi = ansiAt(base);
  if (directAnsi) result.candidates.direct_ansi = directAnsi;
  const offsets = [0x0, 0x4, 0x8, 0x10, 0x14, 0x18, 0x24, 0x28, 0x38];
  offsets.forEach(function (off) {
    try {
      const p = base.add(off).readPointer();
      const u = utf16At(p);
      const a = ansiAt(p);
      if (u) result.candidates['ptr_' + off.toString(16) + '_utf16'] = u;
      if (a) result.candidates['ptr_' + off.toString(16) + '_ansi'] = a;
    } catch (e) {}
  });
  return result;
}

const targetModule = Process.findModuleByName('QQMusicCommon.dll');
if (!targetModule) {
  send({ type: 'fatal', message: 'QQMusicCommon.dll not found' });
} else {
  send({ type: 'module', base: targetModule.base.toString() });

  const base = targetModule.base;
  const offsets = %OFFSETS_JSON%;
  const readExtend = base.add(offsets.ReadExtend);
  const getVkeyCache = base.add(offsets.GetVkeyCache);
  const setEkey = base.add(offsets.SetEkey);

  Interceptor.attach(readExtend, {
    onEnter(args) {
      this.handle = args[0];
      this.outStruct = args[1];
    },
    onLeave(retval) {
      const ok = !retval.isNull() && retval.toInt32() !== 0;
      if (!ok) return;
      const blob = hexBytes(this.outStruct, 0xb0);
      send({
        type: 'ReadExtend',
        handle: ptr(this.handle).toString(),
        out_struct: ptr(this.outStruct).toString(),
        blob_hex: blob,
        strings: likelyStringsFromBlob(blob),
      });
    }
  });

  Interceptor.attach(getVkeyCache, {
    onEnter(args) {
      this.thisPtr = this.context.ecx;
      send({
        type: 'GetVkeyCache_enter',
        this_ptr: ptr(this.thisPtr).toString(),
        arg0_dump: dumpStringObject(args[0]),
        arg1_ptr: ptr(args[1]).toString(),
      });
    },
    onLeave(retval) {
      send({
        type: 'GetVkeyCache_leave',
        retval: ptr(retval).toString(),
      });
    }
  });

  Interceptor.attach(setEkey, {
    onEnter(args) {
      let key = '';
      try {
        key = args[0].readCString(args[1].toInt32());
      } catch (e) {}
      send({
        type: 'SetEkey',
        len: args[1].toInt32(),
        key: key,
        this_ptr: ptr(this.context.ecx).toString(),
      });
    }
  });
}
'''.replace('%OFFSETS_JSON%', offsets_json)


def main() -> int:
    parser = argparse.ArgumentParser(description='Probe ReadExtend/GetVkeyCache/SetEkey during QQ old-chain decrypts.')
    parser.add_argument('--sample', action='append', required=True, help='Sample path; pass multiple times')
    parser.add_argument('--output', default=r'D:\A_python\QQKWKG-TriMusicDecrypt\_log\twod_vkey_lookup_probe.json')
    args = parser.parse_args()

    output_path = pathlib.Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    device, proc = find_qqmusic_process()
    session = device.attach(proc.pid)

    events: list[dict] = []
    script = session.create_script(build_hook_script())

    def on_message(message, data):
        if message.get('type') == 'send':
            payload = message.get('payload', {})
            payload['_ts'] = time.time()
            events.append(payload)
        else:
            events.append({'type': 'script_error', 'payload': message, '_ts': time.time()})

    script.on('message', on_message)
    script.load()

    decryptor = QQMusicDecryptor(session)
    results = []
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix='qqprobe_', dir=str(output_path.parent)))

    for sample_text in args.sample:
        sample = pathlib.Path(sample_text)
        dest = tmp_dir / (f'sample_{len(results):02d}.out')
        before = len(events)
        ok = False
        error = None
        try:
            ok = decryptor.decrypt(str(sample), str(dest))
        except Exception as exc:
            error = repr(exc)
        sample_events = events[before:]
        results.append({
            'sample': str(sample),
            'ok': ok,
            'error': error,
            'output_exists': dest.exists(),
            'output_size': dest.stat().st_size if dest.exists() else 0,
            'events': sample_events,
        })

    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(output_path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
