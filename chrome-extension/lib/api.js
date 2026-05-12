import { isNewerVersion } from "./carriers.js";
import { log } from "./logger.js";

const GITHUB_REPO = "stevendejongnl/dude-wheres-my-package";

// ── Storage helpers ────────────────────────────────────────────────

export async function getConfig() {
  const { dwmp_url, dwmp_token } = await chrome.storage.local.get([
    "dwmp_url",
    "dwmp_token",
  ]);
  return { url: dwmp_url || "", token: dwmp_token || "" };
}

export async function saveConfig(url, token) {
  await chrome.storage.local.set({
    dwmp_url: url.replace(/\/+$/, ""),
    dwmp_token: token,
  });
}

export async function clearConfig() {
  await chrome.storage.local.remove([
    "dwmp_url",
    "dwmp_token",
    "dwmp_auto_sync",
    "dwmp_sync_results",
    "dwmp_update",
  ]);
}

export async function isConfigured() {
  const { url, token } = await getConfig();
  return Boolean(url && token);
}

// ── Core fetch wrapper ─────────────────────────────────────────────

async function apiCall(method, path, body) {
  const { url, token } = await getConfig();
  if (!url || !token) {
    log.warn("api", `${method} ${path} skipped: not configured`);
    return { ok: false, error: "Not configured", status: 0 };
  }

  const opts = {
    method,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);

  const t0 = Date.now();
  log.debug("api", `${method} ${path}`, { bodySize: body !== undefined ? JSON.stringify(body).length : 0 });

  try {
    const res = await fetch(`${url}${path}`, opts);
    const durationMs = Date.now() - t0;

    if (res.status === 204) {
      log.info("api", `${method} ${path} → 204`, { durationMs });
      return { ok: true, data: null, status: 204 };
    }

    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const error = data?.detail || res.statusText || `Server error (${res.status})`;
      log.error("api", `${method} ${path} → ${res.status}`, { durationMs, error });
      return { ok: false, error, status: res.status };
    }

    log.info("api", `${method} ${path} → ${res.status}`, { durationMs });
    return { ok: true, data, status: res.status };
  } catch (err) {
    log.error("api", `${method} ${path} → network error`, { durationMs: Date.now() - t0, error: err.message });
    return { ok: false, error: err.message, status: 0 };
  }
}

// ── Public API methods ─────────────────────────────────────────────

export async function authenticate(url, password) {
  const base = url.replace(/\/+$/, "");
  log.info("api", "Authenticating", { url: base });
  const res = await fetch(`${base}/api/v1/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    const err = new Error(data?.detail || "Authentication failed");
    log.error("api", "Authentication failed", { status: res.status, error: err.message });
    throw err;
  }
  const { token } = await res.json();
  await saveConfig(base, token);
  log.info("api", "Authentication succeeded");
  return token;
}

export async function healthCheck(url) {
  const base = (url || (await getConfig()).url).replace(/\/+$/, "");
  if (!base) throw new Error("No URL configured");
  const res = await fetch(`${base}/health`);
  if (!res.ok) throw new Error(`Server responded ${res.status}`);
  return res.json();
}

export function listAccounts() {
  return apiCall("GET", "/api/v1/accounts");
}

export function listPackages() {
  return apiCall("GET", "/api/v1/packages");
}

export function browserPush(html, url) {
  return apiCall("POST", "/api/v1/browser-push", { html, url });
}

export function browserPayload(accountId, payload) {
  return apiCall("POST", `/api/v1/accounts/${accountId}/browser-payload`, { payload });
}

export function getAccountCredentials(accountId) {
  return apiCall("GET", `/api/v1/accounts/${accountId}/credentials`);
}

// ── Self-update check ──────────────────────────────────────────────

export async function checkForUpdate() {
  try {
    const res = await fetch(
      `https://api.github.com/repos/${GITHUB_REPO}/releases/latest`,
      { headers: { Accept: "application/vnd.github.v3+json" } },
    );
    if (!res.ok) return null;

    const release = await res.json();
    const latestVersion = release.tag_name.replace(/^v/, "");
    const currentVersion = chrome.runtime.getManifest().version;

    if (isNewerVersion(latestVersion, currentVersion)) {
      const asset = release.assets.find((a) =>
        a.name.startsWith("dwmp-chrome-extension-"),
      );
      return {
        version: latestVersion,
        downloadUrl: asset?.browser_download_url || release.html_url,
        releaseUrl: release.html_url,
      };
    }
  } catch {
    // network error, rate-limited, etc.
  }
  return null;
}
