'use strict';

/*
 * KWMusic KWM Decrypt MVP agent
 *
 * RPC:
 * - getenv()
 * - listsymbols()
 * - call_export(attempt)
 *
 * attempt shape:
 * {
 *   symbol: "Music_ExportFileA",
 *   abi: "stdcall" | "cdecl",
 *   signature: "int(char*,char*)",
 *   argEncoding: "ansi" | "utf16",
 *   arg1: "<input kwm abs path>",
 *   arg2: "<output raw abs path>"
 * }
 */

const TARGET_MODULE = 'KwLib.dll';
const SYMBOL_PATTERNS = [
  '*Music_Export*',
  '*Music_CheckIsEncrypted*',
  '*Music_ReadInfoHead*',
  '*Music_GetEncryptByVer*',
  '*KwLib::Entrypt::Decrypt*'
];

function safeCall(fn, fallback) {
  try {
    return fn();
  } catch (_) {
    return fallback;
  }
}

function lower(v) {
  return (v || '').toString().toLowerCase();
}

function ptrToString(p) {
  try {
    if (!p) return '0x0';
    return p.toString();
  } catch (_) {
    return '0x0';
  }
}

function normalizeSymbolName(name) {
  let s = (name || '').toString().trim();
  if (!s) return '';
  const bang = s.indexOf('!');
  if (bang >= 0) s = s.slice(bang + 1);
  const msvc = s.match(/^\?([^@]+)@@/);
  if (msvc && msvc[1]) s = msvc[1];
  s = s.replace(/^_+/, '');
  s = s.replace(/^(\?)+/, '');
  s = s.replace(/@\d+$/, '');
  return lower(s);
}

function wildcardToRegex(pattern) {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  const regexText = '^' + escaped.replace(/\*/g, '.*').replace(/\?/g, '.') + '$';
  return new RegExp(regexText, 'i');
}

function findTargetModule() {
  const mods = safeCall(() => Process.enumerateModules(), []);
  const target = lower(TARGET_MODULE);
  for (let i = 0; i < mods.length; i += 1) {
    const m = mods[i];
    if (lower(m.name) === target) return m;
  }
  return null;
}

function ensureTargetModuleLoaded() {
  let mod = findTargetModule();
  if (mod) return mod;

  if (typeof Module.load === 'function') {
    mod = safeCall(() => Module.load(TARGET_MODULE), null);
    if (mod) return mod;

    const mainExe = safeCall(
      () => Process.enumerateModules().find((m) => lower(m.name).endsWith('.exe')),
      null
    );
    if (mainExe && mainExe.path) {
      const idx = mainExe.path.lastIndexOf('\\');
      if (idx > 0) {
        const full = mainExe.path.slice(0, idx + 1) + TARGET_MODULE;
        mod = safeCall(() => Module.load(full), null);
        if (mod) return mod;
      }
    }
  }

  return findTargetModule();
}

function enumerateTargetExports() {
  const mod = ensureTargetModuleLoaded();
  if (!mod) return [];

  if (typeof mod.enumerateExports === 'function') {
    const ex = safeCall(() => mod.enumerateExports(), []);
    if (Array.isArray(ex)) return ex;
  }
  if (typeof Module.enumerateExports === 'function') {
    const ex = safeCall(() => Module.enumerateExports(mod.name), []);
    if (Array.isArray(ex)) return ex;
  }
  if (typeof Module.enumerateExportsSync === 'function') {
    const ex = safeCall(() => Module.enumerateExportsSync(mod.name), []);
    if (Array.isArray(ex)) return ex;
  }
  return [];
}

function resolveByExports(symbolName, symbolHints) {
  const exports = enumerateTargetExports();
  if (!exports || exports.length === 0) return null;

  const targetNorm = normalizeSymbolName(symbolName);
  const hintNorm = (Array.isArray(symbolHints) ? symbolHints : [])
    .map((x) => normalizeSymbolName(x))
    .filter((x) => x.length > 0);

  for (let i = 0; i < exports.length; i += 1) {
    const item = exports[i];
    if (!item || !item.name || !item.address) continue;
    const norm = normalizeSymbolName(item.name);
    if (norm === targetNorm) {
      return {
        address: item.address,
        resolvedName: item.name,
        resolvedModule: TARGET_MODULE
      };
    }
  }

  const wantedHints = new Set(hintNorm);
  for (let i = 0; i < exports.length; i += 1) {
    const item = exports[i];
    if (!item || !item.name || !item.address) continue;
    const norm = normalizeSymbolName(item.name);
    if (wantedHints.has(norm)) {
      return {
        address: item.address,
        resolvedName: item.name,
        resolvedModule: TARGET_MODULE
      };
    }
  }
  return null;
}

function resolveByExportChain(symbolName, symbolHints) {
  ensureTargetModuleLoaded();
  const byExports = resolveByExports(symbolName, symbolHints);
  if (byExports) return byExports;
  const targetNorm = normalizeSymbolName(symbolName);
  const hintNorm = new Set(
    (Array.isArray(symbolHints) ? symbolHints : [])
      .map((x) => normalizeSymbolName(x))
      .filter((x) => x.length > 0)
  );

  const candidates = [
    () => (typeof Module.getGlobalExportByName === 'function' ? Module.getGlobalExportByName(symbolName) : null),
    () => (typeof Module.findGlobalExportByName === 'function' ? Module.findGlobalExportByName(symbolName) : null),
    () => (typeof Module.getGlobalExportByName === 'function' ? Module.getGlobalExportByName(`${TARGET_MODULE}!${symbolName}`) : null),
    () => (typeof Module.findGlobalExportByName === 'function' ? Module.findGlobalExportByName(`${TARGET_MODULE}!${symbolName}`) : null),
    () => (typeof DebugSymbol.getFunctionByName === 'function' ? DebugSymbol.getFunctionByName(`${TARGET_MODULE}!${symbolName}`) : null),
    () => (typeof DebugSymbol.getFunctionByName === 'function' ? DebugSymbol.getFunctionByName(symbolName) : null),
    () => {
      if (typeof DebugSymbol.findFunctionsMatching !== 'function') return null;
      const matches = DebugSymbol.findFunctionsMatching(`*${symbolName}`);
      if (!matches || matches.length === 0) return null;
      for (let i = 0; i < matches.length; i += 1) {
        const s = safeCall(() => DebugSymbol.fromAddress(matches[i]), null);
        if (!s || lower(s.moduleName) !== lower(TARGET_MODULE)) continue;
        const norm = normalizeSymbolName(s.name || '');
        if (norm === targetNorm) return matches[i];
      }
      for (let i = 0; i < matches.length; i += 1) {
        const s = safeCall(() => DebugSymbol.fromAddress(matches[i]), null);
        if (!s || lower(s.moduleName) !== lower(TARGET_MODULE)) continue;
        const norm = normalizeSymbolName(s.name || '');
        if (hintNorm.has(norm)) return matches[i];
      }
      return null;
    }
  ];

  for (let i = 0; i < candidates.length; i += 1) {
    const addr = safeCall(candidates[i], null);
    if (addr) {
      return {
        address: addr,
        resolvedName: symbolName,
        resolvedModule: TARGET_MODULE
      };
    }
  }
  return null;
}

function scanByPatterns() {
  ensureTargetModuleLoaded();
  const out = [];
  const seen = new Set();
  const exports = enumerateTargetExports();
  for (let i = 0; i < SYMBOL_PATTERNS.length; i += 1) {
    const pattern = SYMBOL_PATTERNS[i];
    const re = wildcardToRegex(pattern);
    for (let j = 0; j < exports.length; j += 1) {
      const e = exports[j];
      if (!e || !e.name || !e.address) continue;
      if (!re.test(e.name)) continue;
      const key = ptrToString(e.address);
      if (seen.has(key)) continue;
      out.push({
        pattern,
        address: key,
        symbol: e.name,
        module: TARGET_MODULE,
        type: e.type || '?'
      });
      seen.add(key);
    }
  }

  if (out.length === 0) {
    const targetMod = lower(TARGET_MODULE);
    const matcher = typeof DebugSymbol.findFunctionsMatching === 'function' ? DebugSymbol.findFunctionsMatching : null;
    if (!matcher) return out;
    for (let i = 0; i < SYMBOL_PATTERNS.length; i += 1) {
      const pattern = SYMBOL_PATTERNS[i];
      const addrs = safeCall(() => matcher(pattern), []);
      for (let j = 0; j < addrs.length; j += 1) {
        const a = addrs[j];
        const key = ptrToString(a);
        if (seen.has(key)) continue;
        const sym = safeCall(() => DebugSymbol.fromAddress(a), null);
        if (!sym) continue;
        if (lower(sym.moduleName) !== targetMod) continue;
        out.push({
          pattern,
          address: key,
          symbol: sym.name || key,
          module: sym.moduleName || '?'
        });
        seen.add(key);
      }
    }
  }
  out.sort((x, y) => x.address.localeCompare(y.address));
  return out;
}

function parseSignature(signatureText) {
  const fallback = { retType: 'int', argTypes: ['pointer', 'pointer'] };
  if (!signatureText) return fallback;

  const text = signatureText.replace(/\s+/g, '').toLowerCase();
  if (text.startsWith('int(char*,char*)')) {
    return { retType: 'int', argTypes: ['pointer', 'pointer'] };
  }
  if (text.startsWith('int(wchar_t*,wchar_t*)')) {
    return { retType: 'int', argTypes: ['pointer', 'pointer'] };
  }
  return fallback;
}

function buildPtrArg(textValue, encoding) {
  if (encoding === 'utf16') return Memory.allocUtf16String(textValue);
  return Memory.allocAnsiString(textValue);
}

function buildMsvcStringObject24(textValue, wide) {
  const text = (textValue || '').toString();
  const obj = Memory.alloc(24);
  for (let i = 0; i < 24; i += 4) {
    obj.add(i).writeU32(0);
  }

  if (wide) {
    const buf = Memory.allocUtf16String(text);
    obj.writePointer(buf);
    obj.add(16).writeU32(text.length);
    obj.add(20).writeU32(text.length + 1);
  } else {
    const buf = Memory.allocAnsiString(text);
    obj.writePointer(buf);
    obj.add(16).writeU32(text.length);
    obj.add(20).writeU32(text.length + 1);
  }
  return obj;
}

function buildMsvcStringObject12(textValue, wide) {
  const text = (textValue || '').toString();
  const obj = Memory.alloc(12);
  const dataPtr = wide ? Memory.allocUtf16String(text) : Memory.allocAnsiString(text);
  obj.writePointer(dataPtr);
  obj.add(4).writeU32(text.length);
  obj.add(8).writeU32(text.length + 1);
  return obj;
}

function readMsvcStringObject24(objPtr, wide) {
  try {
    const size = objPtr.add(16).readU32();
    const res = objPtr.add(20).readU32();
    if (size > 0x100000) return null;
    const inlineLimit = wide ? 8 : 16;
    let dataPtr = objPtr;
    if (res >= inlineLimit) {
      dataPtr = objPtr.readPointer();
    }
    if (wide) {
      return dataPtr.readUtf16String(size);
    }
    return dataPtr.readAnsiString(size);
  } catch (_) {
    return null;
  }
}

function readMsvcStringObject12(objPtr, wide) {
  try {
    const size = objPtr.add(4).readU32();
    if (size > 0x100000) return null;
    const dataPtr = objPtr.readPointer();
    if (wide) return dataPtr.readUtf16String(size);
    return dataPtr.readAnsiString(size);
  } catch (_) {
    return null;
  }
}

function toNativeAbi(abi) {
  const name = lower(abi || '');
  if (name === 'cdecl') return 'mscdecl';
  if (name === 'stdcall') return 'stdcall';
  return null;
}

function callExportRecovered(payload) {
  if (!payload || typeof payload !== 'object') {
    return { ok: false, returnValue: null, error: 'payload_missing' };
  }

  const symbol = (payload.symbol || '').toString().trim();
  const abi = toNativeAbi(payload.abi || 'cdecl');
  const inputPath = (payload.inputPath || '').toString();
  const outputPath = (payload.outputPath || '').toString();
  const flagsHint = parseInt((payload.flags || 0), 10) >>> 0;
  const argLayout = Array.isArray(payload.argLayout) ? payload.argLayout : [];
  const symbolHints = Array.isArray(payload.symbolHints) ? payload.symbolHints : [];
  const layoutVariant = lower(payload.layoutVariant || 'msvc24');

  if (!symbol) return { ok: false, returnValue: null, error: 'symbol_missing' };
  if (!inputPath || !outputPath) return { ok: false, returnValue: null, error: 'path_missing' };
  if (!abi) return { ok: false, returnValue: null, error: 'abi_unsupported' };
  if (argLayout.length === 0) return { ok: false, returnValue: null, error: 'arg_layout_missing' };

  const resolved = resolveByExportChain(symbol, symbolHints);
  if (!resolved || !resolved.address) {
    return { ok: false, returnValue: null, error: `symbol_not_found:${symbol}` };
  }

  const argTypes = [];
  const argValues = [];
  const keepAlive = [];
  let outputObjPtr = null;
  let outputIsWide = false;

  try {
    for (let i = 0; i < argLayout.length; i += 1) {
      const item = argLayout[i] || {};
      const kind = lower(item.kind || '');
      if (kind === 'char_ptr') {
        const p = Memory.allocAnsiString(inputPath);
        keepAlive.push(p);
        argTypes.push('pointer');
        argValues.push(p);
      } else if (kind === 'wchar_ptr') {
        const p = Memory.allocUtf16String(inputPath);
        keepAlive.push(p);
        argTypes.push('pointer');
        argValues.push(p);
      } else if (kind === 'std_string_ref_msvc') {
        const p = layoutVariant === 'msvc12'
          ? buildMsvcStringObject12(outputPath, false)
          : buildMsvcStringObject24(outputPath, false);
        keepAlive.push(p);
        argTypes.push('pointer');
        argValues.push(p);
        outputObjPtr = p;
        outputIsWide = false;
      } else if (kind === 'std_wstring_ref_msvc') {
        const p = layoutVariant === 'msvc12'
          ? buildMsvcStringObject12(outputPath, true)
          : buildMsvcStringObject24(outputPath, true);
        keepAlive.push(p);
        argTypes.push('pointer');
        argValues.push(p);
        outputObjPtr = p;
        outputIsWide = true;
      } else if (kind === 'u32' || kind === 'uint32') {
        argTypes.push('uint32');
        argValues.push(flagsHint);
      } else if (kind === 'pointer_zero') {
        argTypes.push('pointer');
        argValues.push(ptr('0x0'));
      } else {
        return { ok: false, returnValue: null, error: `arg_kind_unsupported:${kind}` };
      }
    }
  } catch (e) {
    return { ok: false, returnValue: null, error: `arg_build_failed:${e}` };
  }

  try {
    const fn = new NativeFunction(resolved.address, 'int', argTypes, abi);
    const rv = fn.apply(null, argValues);
    const rvNum = typeof rv === 'number' ? rv : parseInt(rv.toString(), 10);
    const outputString = outputObjPtr
      ? (layoutVariant === 'msvc12'
        ? readMsvcStringObject12(outputObjPtr, outputIsWide)
        : readMsvcStringObject24(outputObjPtr, outputIsWide))
      : null;
    return {
      ok: true,
      returnValue: Number.isFinite(rvNum) ? rvNum : rv.toString(),
      error: null,
      resolvedAddress: ptrToString(resolved.address),
      resolvedSymbol: resolved.resolvedName || symbol,
      usedAbi: abi,
      argTypes,
      layoutVariant,
      outputString
    };
  } catch (e) {
    const outputString = outputObjPtr
      ? (layoutVariant === 'msvc12'
        ? readMsvcStringObject12(outputObjPtr, outputIsWide)
        : readMsvcStringObject24(outputObjPtr, outputIsWide))
      : null;
    return {
      ok: false,
      returnValue: null,
      error: e.toString(),
      resolvedAddress: ptrToString(resolved.address),
      resolvedSymbol: resolved.resolvedName || symbol,
      usedAbi: abi,
      argTypes,
      layoutVariant,
      outputString
    };
  }
}

function callExport(attempt) {
  if (!attempt || typeof attempt !== 'object') {
    return { ok: false, returnValue: null, error: 'attempt_missing' };
  }

  const symbol = (attempt.symbol || '').toString().trim();
  const abi = lower(attempt.abi || '');
  const signature = (attempt.signature || '').toString().trim();
  const argEncoding = lower(attempt.argEncoding || 'ansi');
  const symbolHints = Array.isArray(attempt.symbolHints) ? attempt.symbolHints : [];
  const arg1 = (attempt.arg1 || '').toString();
  const arg2 = (attempt.arg2 || '').toString();

  if (!symbol) return { ok: false, returnValue: null, error: 'symbol_missing' };
  if (!arg1 || !arg2) return { ok: false, returnValue: null, error: 'args_missing' };
  if (abi !== 'stdcall' && abi !== 'cdecl') {
    return { ok: false, returnValue: null, error: `abi_unsupported:${abi}` };
  }
  const nativeAbi = abi === 'cdecl' ? 'mscdecl' : 'stdcall';

  const resolved = resolveByExportChain(symbol, symbolHints);
  if (!resolved || !resolved.address) {
    return { ok: false, returnValue: null, error: `symbol_not_found:${symbol}` };
  }

  const spec = parseSignature(signature);
  try {
    const fn = new NativeFunction(resolved.address, spec.retType, spec.argTypes, nativeAbi);
    const p1 = buildPtrArg(arg1, argEncoding === 'utf16' ? 'utf16' : 'ansi');
    const p2 = buildPtrArg(arg2, argEncoding === 'utf16' ? 'utf16' : 'ansi');
    const rv = fn(p1, p2);
    const rvNum = typeof rv === 'number' ? rv : parseInt(rv.toString(), 10);
    return {
      ok: true,
      returnValue: Number.isFinite(rvNum) ? rvNum : rv.toString(),
      error: null,
      resolvedAddress: ptrToString(resolved.address),
      resolvedSymbol: resolved.resolvedName || symbol
    };
  } catch (e) {
    return {
      ok: false,
      returnValue: null,
      error: e.toString(),
      resolvedAddress: ptrToString(resolved.address),
      resolvedSymbol: resolved.resolvedName || symbol
    };
  }
}

rpc.exports = {
  getenv() {
    const targetModule = findTargetModule();
    return {
      pid: Process.id,
      arch: Process.arch,
      platform: Process.platform,
      pointerSize: Process.pointerSize,
      targetModule: targetModule
        ? {
            name: targetModule.name,
            base: ptrToString(targetModule.base),
            size: targetModule.size,
            path: targetModule.path
          }
        : null
    };
  },

  listsymbols() {
    return {
      module: TARGET_MODULE,
      symbols: scanByPatterns()
    };
  },

  call_export(attempt) {
    return callExport(attempt);
  },

  callExport(attempt) {
    return callExport(attempt);
  },

  call_export_recovered(payload) {
    return callExportRecovered(payload);
  },

  callExportRecovered(payload) {
    return callExportRecovered(payload);
  }
};
