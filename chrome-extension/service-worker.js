import { browserPush, checkForUpdate, isConfigured, listAccounts } from "./lib/api.js";
import { CARRIER_SYNC_URLS } from "./lib/carriers.js";

const DEFAULT_SYNC_INTERVAL_MIN = 60;
const RENDER_WAIT_MS = 5_000;
const TAB_TIMEOUT_MS = 30_000;

let syncInProgress = false;

// ── Alarm setup ────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(setupAlarms);
chrome.runtime.onStartup.addListener(setupAlarms);

async function setupAlarms() {
  const { dwmp_sync_interval } = await chrome.storage.local.get("dwmp_sync_interval");
  chrome.alarms.create("dwmp-auto-sync", {
    periodInMinutes: dwmp_sync_interval || DEFAULT_SYNC_INTERVAL_MIN,
  });
  chrome.alarms.create("dwmp-update-check", { periodInMinutes: 360 });

  // Run an immediate update check on install
  runUpdateCheck();
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "dwmp-auto-sync") runAutoSync();
  if (alarm.name === "dwmp-update-check") runUpdateCheck();
});

// ── Auto-sync ──────────────────────────────────────────────────────

async function runAutoSync() {
  if (syncInProgress) return;
  if (!(await isConfigured())) return;

  const { dwmp_auto_sync } = await chrome.storage.local.get("dwmp_auto_sync");
  if (!dwmp_auto_sync) return;

  const enabledIds = Object.entries(dwmp_auto_sync)
    .filter(([, v]) => v)
    .map(([k]) => Number(k));
  if (enabledIds.length === 0) return;

  const result = await listAccounts();
  if (!result.ok) return;

  syncInProgress = true;
  try {
    // Deduplicate by carrier -- one tab per carrier, not per account
    const carriersDone = new Set();
    for (const account of result.data) {
      if (!enabledIds.includes(account.id)) continue;
      if (carriersDone.has(account.carrier)) continue;
      carriersDone.add(account.carrier);

      await syncCarrierViaTab(account);
    }
  } finally {
    syncInProgress = false;
  }
}

async function syncCarrierViaTab(account) {
  const url = CARRIER_SYNC_URLS[account.carrier];
  if (!url) {
    await storeSyncResult(account.id, false, `No sync URL for ${account.carrier}`);
    return;
  }

  let tabId = null;
  let shouldCloseTab = true;

  try {
    // Reuse an existing tab on this carrier domain if one is open
    const domain = new URL(url).hostname;
    const existing = await chrome.tabs.query({ url: `*://*.${domain}/*` });
    if (existing.length > 0) {
      tabId = existing[0].id;
      shouldCloseTab = false;
      // Reload to get fresh data
      await chrome.tabs.reload(tabId);
    } else {
      const tab = await chrome.tabs.create({ url, active: false });
      tabId = tab.id;
    }

    await waitForTabLoad(tabId);
    await sleep(RENDER_WAIT_MS);

    const html = await captureTabHtml(tabId);
    if (!html) {
      await storeSyncResult(account.id, false, "Could not capture page content");
      return;
    }

    // Detect Cloudflare challenge page
    if (isCloudflareChallenge(html)) {
      // Make the tab visible so the user can solve the captcha
      await chrome.tabs.update(tabId, { active: true });
      shouldCloseTab = false;
      await storeSyncResult(account.id, false, "Cloudflare challenge -- please solve manually");
      return;
    }

    const pageUrl = (await chrome.tabs.get(tabId)).url;
    const pushResult = await browserPush(html, pageUrl);

    if (pushResult.ok) {
      await storeSyncResult(account.id, true, null, pushResult.data?.length || 0);
    } else {
      await storeSyncResult(account.id, false, pushResult.error);
    }
  } catch (err) {
    await storeSyncResult(account.id, false, err.message);
  } finally {
    if (shouldCloseTab && tabId !== null) {
      chrome.tabs.remove(tabId).catch(() => {});
    }
  }
}

function waitForTabLoad(tabId) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("Tab load timeout"));
    }, TAB_TIMEOUT_MS);

    function listener(id, info) {
      if (id !== tabId || info.status !== "complete") return;
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timeout);
      resolve();
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function captureTabHtml(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => document.documentElement.outerHTML,
  });
  return results?.[0]?.result || null;
}

function isCloudflareChallenge(html) {
  const lower = html.substring(0, 2000).toLowerCase();
  return (
    lower.includes("<title>just a moment") ||
    lower.includes("checking your browser") ||
    lower.includes("cf-challenge")
  );
}

async function storeSyncResult(accountId, ok, error, count) {
  const { dwmp_sync_results } = await chrome.storage.local.get("dwmp_sync_results");
  const results = dwmp_sync_results || {};
  results[accountId] = {
    ok,
    error: error || null,
    count: count ?? 0,
    timestamp: new Date().toISOString(),
  };
  await chrome.storage.local.set({ dwmp_sync_results: results });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ── Update check ───────────────────────────────────────────────────

async function runUpdateCheck() {
  const update = await checkForUpdate();
  if (update) {
    await chrome.storage.local.set({ dwmp_update: update });
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#00b894" });
  } else {
    await chrome.storage.local.remove("dwmp_update");
    chrome.action.setBadgeText({ text: "" });
  }
}

// ── Message handler (popup ↔ service worker) ───────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "sync-current-tab") {
    handleSyncCurrentTab().then(sendResponse);
    return true; // keep channel open for async
  }

  if (msg.type === "trigger-sync") {
    handleTriggerSync(msg.accountId).then(sendResponse);
    return true;
  }

  if (msg.type === "update-auto-sync") {
    handleUpdateAutoSync(msg.accountId, msg.enabled).then(sendResponse);
    return true;
  }

  if (msg.type === "update-sync-interval") {
    chrome.alarms.create("dwmp-auto-sync", { periodInMinutes: msg.interval });
    chrome.storage.local.set({ dwmp_sync_interval: msg.interval });
    sendResponse({ ok: true });
    return false;
  }

  if (msg.type === "check-update") {
    runUpdateCheck().then(() => sendResponse({ ok: true }));
    return true;
  }
});

async function handleSyncCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) return { ok: false, error: "No active tab" };

  try {
    const html = await captureTabHtml(tab.id);
    if (!html) return { ok: false, error: "Could not capture page content" };

    const result = await browserPush(html, tab.url);
    return result;
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

async function handleTriggerSync(accountId) {
  if (syncInProgress) return { ok: false, error: "Sync already in progress" };

  const result = await listAccounts();
  if (!result.ok) return result;

  const account = result.data.find((a) => a.id === accountId);
  if (!account) return { ok: false, error: "Account not found" };

  syncInProgress = true;
  try {
    await syncCarrierViaTab(account);
    const { dwmp_sync_results } = await chrome.storage.local.get("dwmp_sync_results");
    return dwmp_sync_results?.[accountId] || { ok: true };
  } finally {
    syncInProgress = false;
  }
}

async function handleUpdateAutoSync(accountId, enabled) {
  const { dwmp_auto_sync } = await chrome.storage.local.get("dwmp_auto_sync");
  const prefs = dwmp_auto_sync || {};
  prefs[accountId] = enabled;
  await chrome.storage.local.set({ dwmp_auto_sync: prefs });
  return { ok: true };
}
