'use strict';

/*
 * 180s export-behavior call tracer
 * - Capture all call targets inside KW core modules.
 * - Capture Music_Export* argument/return samples.
 */

const CORE_MODULES = [
  'kwmusic.exe',
  'kwlib.dll',
  'kwmusicdll.dll',
  'kwdatadef.dll'
];

const EXPORT_PATTERNS = [
  /music_export/i,
  /music_checkisencrypted/i,
  /music_readinfohead/i
];

const EVENT_BUFFER_LIMIT = 80000;
const SAMPLE_BUFFER_LIMIT = 2000;
const SAMPLE_HISTORY_LIMIT = 12000;

const coreRanges = [];
const followedThreads = new Set();
const hookedExports = new Set();
const functionCounter = new Map();
const eventBuffer = [];
const sampleBuffer = [];
const sampleHistory = [];

const stats = {
  totalCalls: 0,
  tracedCalls: 0,
  droppedEvents: 0,
  parseErrors: 0,
  followErrors: 0,
  exportSamples: 0
};

function lower(v) {
  return (v || '').toString().toLowerCase();
}

function nowIso() {
  return new Date().toISOString();
}

function safeCall(fn, fallback) {
  try {
    return fn();
  } catch (_) {
    return fallback;
  }
}

function ptrToString(p) {
  try {
    return p ? p.toString() : '0x0';
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

function guessAbiBySymbol(name) {
  const text = (name || '').toString();
  if (text.includes('@@YG')) return 'stdcall';
  if (text.includes('@@YA')) return 'cdecl';
  return 'cdecl';
}

function readAnsi(p) {
  return safeCall(() => p.readAnsiString(), null);
}

function readUtf16(p) {
  return safeCall(() => p.readUtf16String(), null);
}

function readHex(p, len) {
  const n = Math.max(0, Math.min(64, len | 0));
  if (n <= 0) return '';
  return safeCall(() => {
    const bytes = Memory.readByteArray(p, n);
    if (!bytes) return '';
    const arr = new Uint8Array(bytes);
    return Array.from(arr).map((x) => x.toString(16).padStart(2, '0')).join('');
  }, '');
}

function readU32(p) {
  return safeCall(() => p.toUInt32(), 0);
}

function refreshCoreRanges() {
  coreRanges.length = 0;
  const mods = safeCall(() => Process.enumerateModules(), []);
  mods.forEach((m) => {
    if (CORE_MODULES.includes(lower(m.name))) {
      coreRanges.push({
        name: m.name,
        base: m.base,
        end: m.base.add(m.size)
      });
    }
  });
}

function findCoreByAddress(addr) {
  for (let i = 0; i < coreRanges.length; i += 1) {
    const r = coreRanges[i];
    if (addr.compare(r.base) >= 0 && addr.compare(r.end) < 0) return r;
  }
  return null;
}

function safeSymbol(addr) {
  const sym = safeCall(() => DebugSymbol.fromAddress(addr), null);
  if (!sym) return addr.toString();
  return sym.name || addr.toString();
}

function incCounter(key) {
  const prev = functionCounter.get(key) || 0;
  functionCounter.set(key, prev + 1);
}

function pushEvent(ev) {
  if (eventBuffer.length >= EVENT_BUFFER_LIMIT) {
    stats.droppedEvents += 1;
    return;
  }
  eventBuffer.push(ev);
}

function pushSample(sample) {
  stats.exportSamples += 1;
  if (sampleBuffer.length >= SAMPLE_BUFFER_LIMIT) {
    sampleBuffer.shift();
  }
  sampleBuffer.push(sample);
  sampleHistory.push(sample);
  if (sampleHistory.length > SAMPLE_HISTORY_LIMIT) {
    sampleHistory.shift();
  }
}

function onCallTarget(tid, target) {
  stats.totalCalls += 1;
  const mod = findCoreByAddress(target);
  if (!mod) return;

  stats.tracedCalls += 1;
  const symbol = safeSymbol(target);
  const event = {
    time: nowIso(),
    tid,
    module: mod.name,
    address: ptrToString(target),
    symbol
  };
  pushEvent(event);
  incCounter(`${mod.name}!${symbol}`);
}

function parseStalkerEvents(tid, events) {
  const parsed = safeCall(() => Stalker.parse(events, { annotate: true, stringify: false }), null);
  if (!parsed) {
    stats.parseErrors += 1;
    return;
  }

  for (let i = 0; i < parsed.length; i += 1) {
    const row = parsed[i];
    if (!row || row.length < 3) continue;
    const type = row[0];
    if (type !== 'call') continue;
    const to = safeCall(() => ptr(row[2]), null);
    if (!to) continue;
    onCallTarget(tid, to);
  }
}

function followThread(tid) {
  if (followedThreads.has(tid)) return;
  try {
    Stalker.follow(tid, {
      events: { call: true, ret: false, exec: false, block: false, compile: false },
      onReceive(events) {
        parseStalkerEvents(tid, events);
      }
    });
    followedThreads.add(tid);
  } catch (e) {
    stats.followErrors += 1;
    console.log(`[trace_all_calls_export] follow thread failed tid=${tid} err=${e}`);
  }
}

function followAllThreads() {
  const threads = safeCall(() => Process.enumerateThreads(), []);
  threads.forEach((t) => followThread(t.id));
}

function shouldHookExport(name) {
  const text = lower(name);
  for (let i = 0; i < EXPORT_PATTERNS.length; i += 1) {
    if (EXPORT_PATTERNS[i].test(text)) return true;
  }
  return false;
}

function hookExportFunctions() {
  const mod = coreRanges.find((m) => lower(m.name) === 'kwlib.dll');
  if (!mod) return;

  const moduleObj = safeCall(() => Process.findModuleByName(mod.name), null);
  if (!moduleObj) return;
  const exports = safeCall(() => moduleObj.enumerateExports(), []);

  exports.forEach((e) => {
    if (!e || !e.name || !e.address) return;
    if (e.type !== 'function') return;
    if (!shouldHookExport(e.name)) return;
    const key = `${mod.name}!${e.name}@${ptrToString(e.address)}`;
    if (hookedExports.has(key)) return;

    try {
      Interceptor.attach(e.address, {
        onEnter(args) {
          this.__sample = {
            time: nowIso(),
            tid: this.threadId || 0,
            module: mod.name,
            symbol: e.name,
            symbol_norm: normalizeSymbolName(e.name),
            abi_guess: guessAbiBySymbol(e.name),
            address: ptrToString(e.address),
            arg0_ptr: ptrToString(args[0]),
            arg1_ptr: ptrToString(args[1]),
            arg2_ptr: ptrToString(args[2]),
            arg3_ptr: ptrToString(args[3]),
            arg0_ansi: readAnsi(args[0]),
            arg0_utf16: readUtf16(args[0]),
            arg1_obj_hex_24: readHex(args[1], 24),
            arg2_u32: readU32(args[2]),
            arg3_u32: readU32(args[3])
          };
        },
        onLeave(retval) {
          if (!this.__sample) return;
          this.__sample.retval = ptrToString(retval);
          pushSample(this.__sample);
        }
      });
      hookedExports.add(key);
    } catch (err) {
      console.log(`[trace_all_calls_export] hook failed ${key} err=${err}`);
    }
  });
}

function mapTopFunctions(limit) {
  const top = Array.from(functionCounter.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, Math.max(1, limit | 0))
    .map(([name, count]) => ({ name, count }));
  return top;
}

function flushData() {
  const events = eventBuffer.splice(0, eventBuffer.length);
  const samples = sampleBuffer.splice(0, sampleBuffer.length);
  return {
    events,
    samples,
    stats: {
      totalCalls: stats.totalCalls,
      tracedCalls: stats.tracedCalls,
      droppedEvents: stats.droppedEvents,
      parseErrors: stats.parseErrors,
      followErrors: stats.followErrors,
      exportSamples: stats.exportSamples,
      uniqueFunctions: functionCounter.size,
      followedThreads: followedThreads.size
    }
  };
}

function resetAll() {
  eventBuffer.length = 0;
  sampleBuffer.length = 0;
  sampleHistory.length = 0;
  functionCounter.clear();
  stats.totalCalls = 0;
  stats.tracedCalls = 0;
  stats.droppedEvents = 0;
  stats.parseErrors = 0;
  stats.followErrors = 0;
  stats.exportSamples = 0;
  return true;
}

refreshCoreRanges();
followAllThreads();
hookExportFunctions();
setInterval(() => {
  refreshCoreRanges();
  followAllThreads();
  hookExportFunctions();
}, 1500);

console.log('[trace_all_calls_export] loaded');
console.log(`[trace_all_calls_export] core modules=${coreRanges.map((x) => x.name).join(', ')}`);

rpc.exports = {
  getstats() {
    return {
      totalCalls: stats.totalCalls,
      tracedCalls: stats.tracedCalls,
      droppedEvents: stats.droppedEvents,
      parseErrors: stats.parseErrors,
      followErrors: stats.followErrors,
      exportSamples: stats.exportSamples,
      uniqueFunctions: functionCounter.size,
      followedThreads: followedThreads.size,
      topFunctions: mapTopFunctions(30)
    };
  },
  flush() {
    return flushData();
  },
  getsamples(limit) {
    const n = Math.max(1, Math.min(2000, limit | 0));
    return sampleHistory.slice(-n);
  },
  reset() {
    return resetAll();
  }
};
