import {
  browserPush,
  browserPayload,
  checkForUpdate,
  getAccountCredentials,
  isConfigured,
  listAccounts,
} from "./lib/api.js";
import {
  CARRIER_AUTH_CLEAR,
  CARRIER_LOGIN_PATTERNS,
  CARRIER_SYNC_URLS,
} from "./lib/carriers.js";

const DEFAULT_SYNC_INTERVAL_MIN = 60;
const RENDER_WAIT_MS = 5_000;
const TAB_TIMEOUT_MS = 30_000;
const POSTNL_GRAPHQL_URL = "https://jouw.postnl.nl/account/api/graphql";
const POSTNL_TRACK_API_URL = "https://jouw.postnl.nl/track-and-trace/api/trackAndTrace";
const POSTNL_SHIPMENTS_QUERY = `
{
  trackedShipments {
    receiverShipments {
      ...parcelShipment
    }
    senderShipments {
      ...parcelShipment
    }
  }
}

fragment parcelShipment on TrackedShipmentResultType {
  key
  barcode
  title
  delivered
  deliveredTimeStamp
  deliveryWindowFrom
  deliveryWindowTo
  shipmentType
  detailsUrl
  creationDateTime
}
`;

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
    // Wipe all carrier session data before every login-URL sync. Stale cookies
    // and broken SSO state are the most common cause of silent login failures;
    // credentials are always stored at this point so we can re-login cleanly.
    if (urls.login) {
      await clearCarrierSiteData(account.carrier);
    }

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

    // PostNL: fetch the account list + detail payloads in the real browser tab
    // and push the structured payload to the server. That keeps the same
    // browser-authenticated view the user sees, including the richer timeline.
    if (account.carrier === "postnl") {
      let accessToken = null;
      const deadline = Date.now() + 15_000;
      while (Date.now() < deadline) {
        const tabUrl = (await chrome.tabs.get(tabId)).url || "";
        if (!tabUrl.includes("jouw.postnl.nl")) {
          await sleep(500);
          continue;
        }
        let tokenResult;
        try {
          tokenResult = await chrome.scripting.executeScript({
            target: { tabId },
            func: () => sessionStorage.getItem("poa.auth.access_token"),
          });
        } catch {
          await sleep(500);
          continue;
        }
        accessToken = tokenResult?.[0]?.result || null;
        if (accessToken) break;
        await sleep(500);
      }
      if (!accessToken) {
        await storeSyncResult(account.id, false, "Could not extract PostNL token from session");
        return;
      }

      const payloadResult = await fetchPostNLPayload(tabId, accessToken);
      if (!payloadResult.ok) {
        await storeSyncResult(account.id, false, payloadResult.error || "Failed to fetch PostNL data");
        return;
      }

      const syncResult = await browserPayload(account.id, payloadResult.data);
      const count = Array.isArray(syncResult.data) ? syncResult.data.length : 0;
      await storeSyncResult(account.id, syncResult.ok, syncResult.ok ? null : syncResult.error, count);
      return;
    }

    await sleep(RENDER_WAIT_MS);

    let html = await captureTabHtml(tabId);
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

    // Detect carrier error/outage page (e.g. DPD "Technical issue occurred").
    // Clear site data for the carrier domain and retry once with a fresh login —
    // stale cookies are the most common cause of DPD landing on its error page.
    if (isCarrierErrorPage(html)) {
      await clearCarrierSiteData(account.carrier);
      await chrome.tabs.update(tabId, { url: startUrl });
      await waitForTabLoad(tabId);
      await waitForUrlStable(tabId);

      // Re-login after clearing cookies
      const retryTabInfo = await chrome.tabs.get(tabId);
      const retryOnLogin =
        isCarrierLoginPage(account.carrier, retryTabInfo.url) ||
        (await hasLoginForm(tabId));
      if (retryOnLogin) {
        const loggedIn = await handleCarrierLogin(tabId, account);
        if (!loggedIn) {
          await storeSyncResult(account.id, false, "Login failed after clearing site data");
          return;
        }
        await waitForUrlStable(tabId);
      }

      if (urls.login) {
        const afterLoginUrl = (await chrome.tabs.get(tabId)).url || "";
        if (!afterLoginUrl.startsWith(urls.parcels)) {
          await chrome.tabs.update(tabId, { url: urls.parcels });
          await waitForTabLoad(tabId);
          await waitForUrlStable(tabId);
        }
      }

      await sleep(RENDER_WAIT_MS);
      const retryHtml = await captureTabHtml(tabId);
      if (!retryHtml || isCarrierErrorPage(retryHtml)) {
        await storeSyncResult(account.id, false, "Carrier site error -- try again later");
        return;
      }
      html = retryHtml;
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
        // Fast path: Amazon's login forms have stable IDs on the <form>.
        if (document.querySelector("#ap_login_form, form#signIn")) return true;
        const u = document.querySelector(
          "#username, #ap_email, #ap_email_login, input[name='username'], input[name='email']",
        );
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

async function fetchPostNLPayload(tabId, accessToken) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: async (graphqlUrl, trackApiUrl, shipmentsQuery, token) => {
        const toTrackKey = (detailsUrl) => {
          if (!detailsUrl) return null;
          try {
            const pathname = new URL(detailsUrl, window.location.origin).pathname.replace(/\/+$/, "");
            const prefix = "/track-and-trace/";
            if (!pathname.includes(prefix)) return null;
            const slug = pathname.split(prefix)[1];
            const parts = slug.split("/");
            if (parts.length >= 3) {
              return `${parts[0]}-${parts[2].toUpperCase()}-${parts[1]}`;
            }
            return parts[0] || null;
          } catch {
            return null;
          }
        };

        const graphResponse = await fetch(graphqlUrl, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/json",
            "Accept-Language": "nl-NL",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ variables: {}, query: shipmentsQuery }),
          credentials: "include",
        });
        if (!graphResponse.ok) {
          throw new Error(`PostNL GraphQL failed (${graphResponse.status})`);
        }

        const graphData = await graphResponse.json();
        const tracked = graphData?.data?.trackedShipments || {};
        const shipments = [
          ...(tracked.receiverShipments || []),
          ...(tracked.senderShipments || []),
        ];

        const details = [];
        for (const shipment of shipments) {
          if (!shipment?.detailsUrl) continue;
          const trackKey = toTrackKey(shipment.detailsUrl);
          if (!trackKey) continue;

          const detailResponse = await fetch(
            `${trackApiUrl}/${encodeURIComponent(trackKey)}?language=nl`,
            {
              headers: {
                Accept: "application/json",
                "Accept-Language": "nl-NL",
              },
              credentials: "include",
            },
          );
          if (!detailResponse.ok) {
            throw new Error(
              `PostNL detail fetch failed for ${shipment.barcode || shipment.key} (${detailResponse.status})`,
            );
          }
          details.push({
            tracking_number: shipment.barcode || shipment.key,
            data: await detailResponse.json(),
          });
        }

        return { shipments, details };
      },
      args: [
        POSTNL_GRAPHQL_URL,
        POSTNL_TRACK_API_URL,
        POSTNL_SHIPMENTS_QUERY,
        accessToken,
      ],
    });
    return { ok: true, data: results?.[0]?.result || { shipments: [], details: [] } };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

function isCloudflareChallenge(html) {
  const lower = html.substring(0, 2000).toLowerCase();
  return (
    lower.includes("<title>just a moment") ||
    lower.includes("checking your browser") ||
    lower.includes("cf-challenge")
  );
}

async function clearCarrierSiteData(carrier) {
  const config = CARRIER_AUTH_CLEAR[carrier];
  if (!config) return;

  // Cookies: enumerate by eTLD+1 — covers all subdomains via cookie-spec
  // domain matching (e.g. Keycloak SSO cookies on .dpdgroup.com).
  const cookies = await chrome.cookies.getAll({ domain: config.cookieDomain });
  await Promise.all(
    cookies.map((c) => {
      const host = c.domain.replace(/^\./, "");
      const url = `${c.secure ? "https" : "http"}://${host}${c.path}`;
      return chrome.cookies.remove({ url, name: c.name, storeId: c.storeId });
    }),
  );

  // Storage / cache: browsingData has no domain-suffix filter, so use the
  // explicit per-carrier origin list.
  if (config.storageOrigins?.length) {
    await chrome.browsingData.remove(
      { origins: config.storageOrigins },
      { cache: true, localStorage: true },
    );
  }
}

function isCarrierErrorPage(html) {
  const lower = html.substring(0, 3000).toLowerCase();
  return (
    lower.includes("technical issue occurred") ||
    lower.includes("technisch probleem opgetreden") ||
    lower.includes("technische storing")
  );
}

function isCarrierLoginPage(carrier, url) {
  const patterns = CARRIER_LOGIN_PATTERNS[carrier];
  if (!patterns) return false;
  const lower = (url || "").toLowerCase();
  return patterns.some((p) => lower.includes(p));
}

async function handlePostNLLogin(tabId, username, password) {
  await chrome.scripting.executeScript({
    target: { tabId },
    func: (email, pass) => {
      // PostNL OIDC login form at login.postnl.nl — text + password inputs
      const inputs = document.querySelectorAll(
        "input[type='text'], input[type='email'], input:not([type])"
      );
      const passEl = document.querySelector("input[type='password']");
      const emailEl = inputs[0];
      if (emailEl) {
        emailEl.value = email;
        emailEl.dispatchEvent(new Event("input", { bubbles: true }));
      }
      if (passEl) {
        passEl.value = pass;
        passEl.dispatchEvent(new Event("input", { bubbles: true }));
      }
      const btn =
        document.querySelector("button[type='submit']") ||
        document.querySelector("input[type='submit']");
      if (btn) btn.click();
    },
    args: [username, password],
  });

  await waitForUrlStable(tabId, 2000, 15000);
  const info = await chrome.tabs.get(tabId);
  return !isCarrierLoginPage("postnl", info.url);
}

async function handleCarrierLogin(tabId, account) {
  const result = await getAccountCredentials(account.id);
  if (!result.ok || !result.data?.has_credentials) return false;

  const { username, password } = result.data;

  if (account.carrier === "amazon") {
    return handleAmazonLogin(tabId, username, password);
  }

  if (account.carrier === "postnl") {
    return handlePostNLLogin(tabId, username, password);
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
 * Amazon sign-in handler.  Amazon routes logged-out users through several
 * entry points that vary by cookie state, region, and A/B flag:
 *
 *   - /ap/signin "combined"     — #ap_email + #ap_password visible together
 *   - /ap/signin "email-only"   — #ap_email + #continue, password on next page
 *   - /ap/signin "password-only"— Amazon remembered the email, only #ap_password
 *   - /ax/claim  "ax-email"     — new claim flow: #ap_email_login inside
 *                                 #ap_login_form, then /ap/signin for password
 *
 * Rather than branch per entry point, we run a detect → fill → submit →
 * wait-for-nav loop. Each iteration re-inspects the current DOM, so the
 * handler naturally walks multi-page flows (e.g. /ax/claim → /ap/signin)
 * without having to encode the transition graph.
 *
 * CAPTCHA (``#auth-captcha-guess``) and MFA (``#auth-mfa-otpcode``) are
 * terminal for automation — we surface the tab so the user can solve it.
 */
async function handleAmazonLogin(tabId, email, password) {
  // Dismiss the EU cookie consent banner up front (it can overlay the form).
  await dismissAmazonCookieBanner(tabId);

  const MAX_STEPS = 5;
  for (let step = 0; step < MAX_STEPS; step++) {
    const state = await detectAmazonLoginState(tabId);

    if (state === "captcha" || state === "mfa") {
      await chrome.tabs.update(tabId, { active: true });
      return false;
    }

    // No recognizable form on the page: either we've reached the post-login
    // destination, or we're stuck on a page we don't know how to fill.
    if (state === "none") {
      const info = await chrome.tabs.get(tabId);
      return !isCarrierLoginPage("amazon", info.url);
    }

    const nav = waitForTabNavigation(tabId, TAB_TIMEOUT_MS);
    const submitted = await fillAndSubmitAmazonStep(tabId, state, email, password);
    if (!submitted) {
      await chrome.tabs.update(tabId, { active: true });
      return false;
    }
    try { await nav; } catch { return false; }
    await waitForUrlStable(tabId);
    // Cookie banner sometimes re-renders on the next page.
    await dismissAmazonCookieBanner(tabId);
  }

  // Ran out of steps — probably looping on the same form. Surface for the user.
  await chrome.tabs.update(tabId, { active: true });
  return false;
}

async function dismissAmazonCookieBanner(tabId) {
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
}

/**
 * Classify the current Amazon auth page into a state the step submitter
 * knows how to fill. `offsetParent !== null` is a cheap "actually visible"
 * check that accounts for `display: none` ancestors — important because
 * Amazon keeps a hidden ``#ap_email`` around on password-only pages.
 */
async function detectAmazonLoginState(tabId) {
  try {
    const result = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const visible = (el) => Boolean(el && el.offsetParent !== null);
        if (document.querySelector("#auth-captcha-guess")) return "captcha";
        if (document.querySelector("#auth-mfa-otpcode")) return "mfa";

        // New /ax/claim flow uses #ap_email_login inside #ap_login_form.
        if (visible(document.querySelector("#ap_email_login"))) return "ax-email";

        const emailVisible = visible(document.querySelector("#ap_email"));
        const passVisible = visible(document.querySelector("#ap_password"));
        if (emailVisible && passVisible) return "combined";
        if (emailVisible) return "email-only";
        if (passVisible) return "password-only";
        return "none";
      },
    });
    return result?.[0]?.result || "none";
  } catch {
    return "none";
  }
}

async function fillAndSubmitAmazonStep(tabId, state, email, password) {
  try {
    const result = await chrome.scripting.executeScript({
      target: { tabId },
      func: (stateArg, e, p) => {
        const setVal = (el, v) => {
          if (!el) return false;
          el.value = v;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
          return true;
        };
        const clickFirst = (selectors) => {
          for (const sel of selectors) {
            const btn = document.querySelector(sel);
            if (btn) { btn.click(); return true; }
          }
          return false;
        };

        if (stateArg === "ax-email") {
          if (!setVal(document.querySelector("#ap_email_login"), e)) return false;
          // The /ax/claim submit button has no stable id; scope the lookup
          // to #ap_login_form so we don't accidentally click the cookie
          // banner's accept button or similar.
          const form = document.querySelector("#ap_login_form");
          const btn = form?.querySelector(
            "input[type='submit'], button[type='submit']",
          );
          if (btn) { btn.click(); return true; }
          if (form) { form.submit(); return true; }
          return false;
        }

        if (stateArg === "combined") {
          setVal(document.querySelector("#ap_email"), e);
          setVal(document.querySelector("#ap_password"), p);
          return clickFirst(["#signInSubmit", "input[type='submit']"]);
        }

        if (stateArg === "email-only") {
          if (!setVal(document.querySelector("#ap_email"), e)) return false;
          return clickFirst(["#continue", "input[id='continue']", "input[type='submit']"]);
        }

        if (stateArg === "password-only") {
          if (!setVal(document.querySelector("#ap_password"), p)) return false;
          return clickFirst(["#signInSubmit", "input[type='submit']"]);
        }

        return false;
      },
      args: [state, email, password],
    });
    return Boolean(result?.[0]?.result);
  } catch {
    return false;
  }
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
