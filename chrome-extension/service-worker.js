import {
  browserPush,
  checkForUpdate,
  getAccountCredentials,
  isConfigured,
  listAccounts,
} from "./lib/api.js";
import { CARRIER_LOGIN_PATTERNS, CARRIER_SYNC_URLS } from "./lib/carriers.js";

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
  const urls = CARRIER_SYNC_URLS[account.carrier];
  if (!urls?.parcels) {
    await storeSyncResult(account.id, false, `No sync URL for ${account.carrier}`);
    return;
  }

  // Carriers with a login URL require stored credentials so the extension
  // can fill in the login form automatically.
  if (urls.login && !account.has_credentials) {
    await storeSyncResult(
      account.id,
      false,
      "No credentials configured -- add them in the DWMP dashboard",
    );
    return;
  }

  let tabId = null;
  let shouldCloseTab = true;

  // Always go through the login URL when one is configured. This guarantees
  // the carrier presents its sign-in form (or skips straight to the
  // destination if a session is already active) — far more reliable than
  // hoping the parcels page redirects to login on cookie expiry.
  const startUrl = urls.login || urls.parcels;

  try {
    // Reuse an existing tab on this carrier domain if one is open
    const domain = new URL(startUrl).hostname;
    const existing = await chrome.tabs.query({ url: `*://*.${domain}/*` });
    if (existing.length > 0) {
      tabId = existing[0].id;
      shouldCloseTab = false;
      await chrome.tabs.update(tabId, { url: startUrl });
    } else {
      const tab = await chrome.tabs.create({ url: startUrl, active: false });
      tabId = tab.id;
    }

    await waitForTabLoad(tabId);
    await waitForUrlStable(tabId);

    // If the tab landed on a login page (URL match OR a login form is
    // visible in the DOM), fill credentials and submit.
    const tabInfo = await chrome.tabs.get(tabId);
    const onLogin =
      isCarrierLoginPage(account.carrier, tabInfo.url) ||
      (await hasLoginForm(tabId));
    if (onLogin) {
      const loggedIn = await handleCarrierLogin(tabId, account);
      if (!loggedIn) {
        await storeSyncResult(account.id, false, "Login failed -- check credentials");
        return;
      }
      await waitForUrlStable(tabId);
    }

    // After login (or if already logged in), navigate to the parcels page
    // so we capture the right HTML — the post-login redirect may land us
    // on a dashboard instead of the parcels list.
    const currentUrl = (await chrome.tabs.get(tabId)).url || "";
    if (urls.login && !currentUrl.startsWith(urls.parcels)) {
      await chrome.tabs.update(tabId, { url: urls.parcels });
      await waitForTabLoad(tabId);
      await waitForUrlStable(tabId);
    }

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

/**
 * After the initial 'complete' fires, carriers can still chain client-side
 * redirects (Keycloak SSO bounce, OAuth callback). Poll the tab URL until it
 * stops changing for ~1.5s before declaring the navigation done.
 */
async function waitForUrlStable(tabId, idleMs = 1500, maxMs = 10_000) {
  let lastUrl = null;
  let lastChange = Date.now();
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    let url;
    try {
      const tab = await chrome.tabs.get(tabId);
      url = tab.url;
    } catch {
      return;
    }
    if (url !== lastUrl) {
      lastUrl = url;
      lastChange = Date.now();
    } else if (Date.now() - lastChange >= idleMs) {
      return;
    }
    await sleep(250);
  }
}

/**
 * Detect a login form by DOM rather than URL. Catches login pages whose URL
 * doesn't match our patterns (e.g. a portal page that embeds the form).
 */
async function hasLoginForm(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const u = document.querySelector("#username, #ap_email, input[name='username'], input[name='email']");
        const p = document.querySelector("#password, #ap_password, input[type='password']");
        return Boolean(u && p) || Boolean(u && document.querySelector("#continue"));
      },
    });
    return Boolean(results?.[0]?.result);
  } catch {
    return false;
  }
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

function isCarrierLoginPage(carrier, url) {
  const patterns = CARRIER_LOGIN_PATTERNS[carrier];
  if (!patterns) return false;
  const lower = (url || "").toLowerCase();
  return patterns.some((p) => lower.includes(p));
}

async function handleCarrierLogin(tabId, account) {
  const result = await getAccountCredentials(account.id);
  if (!result.ok || !result.data?.has_credentials) return false;

  const { username, password } = result.data;

  if (account.carrier === "amazon") {
    return handleAmazonLogin(tabId, username, password);
  }

  // Generic Keycloak-style login (DPD)
  const nav = waitForTabNavigation(tabId, TAB_TIMEOUT_MS);

  await chrome.scripting.executeScript({
    target: { tabId },
    func: (email, pass) => {
      const emailEl =
        document.querySelector("#username") ||
        document.querySelector("input[name='username']");
      const passEl =
        document.querySelector("#password") ||
        document.querySelector("input[name='password']");

      if (emailEl) {
        emailEl.value = email;
        emailEl.dispatchEvent(new Event("input", { bubbles: true }));
      }
      if (passEl) {
        passEl.value = pass;
        passEl.dispatchEvent(new Event("input", { bubbles: true }));
      }

      const btn =
        document.querySelector("#kc-login") ||
        document.querySelector("input[type='submit']") ||
        document.querySelector("button[type='submit']");
      if (btn) btn.click();
    },
    args: [username, password],
  });

  await nav;

  const info = await chrome.tabs.get(tabId);
  return !isCarrierLoginPage(account.carrier, info.url);
}

/**
 * Amazon sign-in handler.  Amazon actually serves three different entry
 * points in practice, and the chrome extension must cope with all of them:
 *
 *   1. Fresh sign-in with a split form — ``#ap_email`` only, click
 *      ``#continue``, then ``#ap_password`` on the next page.
 *   2. Fresh sign-in with a single-page form — ``#ap_email`` and
 *      ``#ap_password`` both visible; submit via ``#signInSubmit``.
 *   3. Recognized user — Amazon already knows the email, page opens on
 *      ``#ap_password`` only.
 *
 * CAPTCHA (``#auth-captcha-guess``) and MFA (``#auth-mfa-otpcode``) both
 * fall through: we surface the tab so the user can solve it.  Also
 * dismisses the EU cookie-consent banner which otherwise overlays the form.
 */
async function handleAmazonLogin(tabId, email, password) {
  // Step 0: dismiss the EU cookie consent banner if present.
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const accept = document.querySelector("#sp-cc-accept");
        if (accept) accept.click();
      },
    });
  } catch {
    // ignore
  }

  // Detect which Amazon form variant we're on.
  let variant;
  try {
    const result = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        if (document.querySelector("#auth-captcha-guess")) return "captcha";
        if (document.querySelector("#auth-mfa-otpcode")) return "mfa";
        const emailEl = document.querySelector("#ap_email");
        const passEl = document.querySelector("#ap_password");
        const emailVisible = emailEl && emailEl.offsetParent !== null;
        const passVisible = passEl && passEl.offsetParent !== null;
        if (emailVisible && passVisible) return "combined";
        if (emailVisible) return "email-only";
        if (passVisible) return "password-only";
        return "unknown";
      },
    });
    variant = result?.[0]?.result || "unknown";
  } catch {
    variant = "unknown";
  }

  // CAPTCHA or MFA: surface the tab to the user.
  if (variant === "captcha" || variant === "mfa") {
    await chrome.tabs.update(tabId, { active: true });
    return false;
  }

  // Combined form — fill both, submit once.
  if (variant === "combined") {
    const nav = waitForTabNavigation(tabId, TAB_TIMEOUT_MS);
    await chrome.scripting.executeScript({
      target: { tabId },
      func: (e, p) => {
        const setVal = (el, v) => {
          if (!el) return;
          el.value = v;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        };
        setVal(document.querySelector("#ap_email"), e);
        setVal(document.querySelector("#ap_password"), p);
        const btn = document.querySelector("#signInSubmit") ||
                    document.querySelector("input[type='submit']");
        if (btn) btn.click();
      },
      args: [email, password],
    });
    try { await nav; } catch { return false; }
  } else if (variant === "email-only") {
    // Split form: fill email → continue → fill password → submit.
    const nav1 = waitForTabNavigation(tabId, TAB_TIMEOUT_MS);
    await chrome.scripting.executeScript({
      target: { tabId },
      func: (e) => {
        const el = document.querySelector("#ap_email");
        if (el) {
          el.value = e;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }
        const btn = document.querySelector("#continue") ||
                    document.querySelector("input[id='continue']");
        if (btn) btn.click();
      },
      args: [email],
    });
    try { await nav1; } catch { return false; }

    // Wait for the password field to appear on the next page.
    const passReady = await waitForSelector(tabId, "#ap_password", 10_000);
    if (!passReady) return false;

    const nav2 = waitForTabNavigation(tabId, TAB_TIMEOUT_MS);
    await chrome.scripting.executeScript({
      target: { tabId },
      func: (p) => {
        const el = document.querySelector("#ap_password");
        if (el) {
          el.value = p;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }
        const btn = document.querySelector("#signInSubmit") ||
                    document.querySelector("input[type='submit']");
        if (btn) btn.click();
      },
      args: [password],
    });
    try { await nav2; } catch { return false; }
  } else if (variant === "password-only") {
    // Amazon remembered the email; just submit the password.
    const nav = waitForTabNavigation(tabId, TAB_TIMEOUT_MS);
    await chrome.scripting.executeScript({
      target: { tabId },
      func: (p) => {
        const el = document.querySelector("#ap_password");
        if (el) {
          el.value = p;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }
        const btn = document.querySelector("#signInSubmit") ||
                    document.querySelector("input[type='submit']");
        if (btn) btn.click();
      },
      args: [password],
    });
    try { await nav; } catch { return false; }
  } else {
    // Unknown variant — no recognizable form fields, bail out.
    await chrome.tabs.update(tabId, { active: true });
    return false;
  }

  // After submitting, Amazon may redirect to MFA, CAPTCHA, or approval.
  // Check the final state; surface the tab if still on a login-related page.
  await waitForUrlStable(tabId);
  const info = await chrome.tabs.get(tabId);
  if (isCarrierLoginPage("amazon", info.url)) {
    await chrome.tabs.update(tabId, { active: true });
    return false;
  }
  return true;
}

/**
 * Poll for a CSS selector to appear in the tab.  Returns true if found
 * within timeoutMs, false otherwise.
 */
async function waitForSelector(tabId, selector, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const result = await chrome.scripting.executeScript({
        target: { tabId },
        func: (sel) => Boolean(document.querySelector(sel)),
        args: [selector],
      });
      if (result?.[0]?.result) return true;
    } catch {
      return false;
    }
    await sleep(300);
  }
  return false;
}

function waitForTabNavigation(tabId, timeout) {
  return new Promise((resolve, reject) => {
    let sawLoading = false;
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("Login navigation timeout"));
    }, timeout);

    function listener(id, changeInfo) {
      if (id !== tabId) return;
      if (changeInfo.status === "loading") sawLoading = true;
      if (sawLoading && changeInfo.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        clearTimeout(timer);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
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
