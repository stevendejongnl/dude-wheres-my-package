const BUFFER_MAX = 500;
const FLUSH_INTERVAL_MS = 2000;
const FLUSH_AT = 100;
const FALLBACK_KEY = "dwmp_logs_fallback";
const FALLBACK_MAX = 200;

const CONTEXT =
  typeof ServiceWorkerGlobalScope !== "undefined" && self instanceof ServiceWorkerGlobalScope
    ? "sw"
    : "popup";

const _buffer = [];
let _flushTimer = null;

function _entry(level, category, message, data) {
  return {
    ts: new Date().toISOString(),
    level,
    category,
    message,
    data: data !== undefined ? data : null,
    context: CONTEXT,
  };
}

function _enqueue(e) {
  _buffer.push(e);
  if (_buffer.length > BUFFER_MAX) _buffer.shift();
  if (_buffer.length >= FLUSH_AT) {
    _scheduleFlush(0);
  } else if (!_flushTimer) {
    _scheduleFlush(FLUSH_INTERVAL_MS);
  }
}

function _scheduleFlush(delay) {
  if (_flushTimer) clearTimeout(_flushTimer);
  _flushTimer = setTimeout(_flush, delay);
}

async function _flush() {
  _flushTimer = null;
  if (_buffer.length === 0) return;
  const entries = _buffer.splice(0);
  await _sendToServer(entries);
}

async function _sendToServer(entries) {
  try {
    const { dwmp_url, dwmp_token } = await chrome.storage.local.get(["dwmp_url", "dwmp_token"]);
    if (!dwmp_url || !dwmp_token) {
      await _fallbackStore(entries);
      return;
    }
    const res = await fetch(`${dwmp_url}/api/v1/logs`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${dwmp_token}`,
      },
      body: JSON.stringify({ entries }),
    });
    if (!res.ok) await _fallbackStore(entries);
  } catch {
    await _fallbackStore(entries);
  }
}

async function _fallbackStore(entries) {
  try {
    const stored = await chrome.storage.local.get(FALLBACK_KEY);
    const existing = stored[FALLBACK_KEY] || [];
    const combined = [...existing, ...entries].slice(-FALLBACK_MAX);
    await chrome.storage.local.set({ [FALLBACK_KEY]: combined });
  } catch {
    // storage also failed — silent drop
  }
}

export const log = {
  debug: (category, message, data) => _enqueue(_entry("debug", category, message, data)),
  info:  (category, message, data) => _enqueue(_entry("info",  category, message, data)),
  warn:  (category, message, data) => _enqueue(_entry("warn",  category, message, data)),
  error: (category, message, data) => _enqueue(_entry("error", category, message, data)),

  getBuffer() {
    return [..._buffer];
  },

  flush: _flush,
};

// In the service worker, patch console.* to capture stray errors from injected scripts
if (CONTEXT === "sw") {
  const _orig = {
    log:   console.log.bind(console),
    info:  console.info.bind(console),
    warn:  console.warn.bind(console),
    error: console.error.bind(console),
  };
  console.log   = (...a) => { _orig.log(...a);   _enqueue(_entry("debug", "console", a.map(String).join(" "))); };
  console.info  = (...a) => { _orig.info(...a);  _enqueue(_entry("info",  "console", a.map(String).join(" "))); };
  console.warn  = (...a) => { _orig.warn(...a);  _enqueue(_entry("warn",  "console", a.map(String).join(" "))); };
  console.error = (...a) => { _orig.error(...a); _enqueue(_entry("error", "console", a.map(String).join(" "))); };
}
