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
  detectCarrier,
} from "./lib/carriers.js";
import { log } from "./lib/logger.js";

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
  const interval = dwmp_sync_interval || DEFAULT_SYNC_INTERVAL_MIN;
  log.info("sw", "Service worker started, registering alarms", { syncIntervalMin: interval });
  chrome.alarms.create("dwmp-auto-sync", { periodInMinutes: interval });
  chrome.alarms.create("dwmp-update-check", { periodInMinutes: 360 });

  // Run an immediate update check on install
  runUpdateCheck();
}

chrome.alarms.onAlarm.addListener((alarm) => {
  log.info("alarm", `Alarm fired: ${alarm.name}`);
  if (alarm.name === "dwmp-auto-sync") runAutoSync();
  if (alarm.name === "dwmp-update-check") runUpdateCheck();
});

// ── Auto-sync ──────────────────────────────────────────────────────

async function runAutoSync() {
  log.info("sync", "Auto-sync triggered");
  if (syncInProgress) {
    log.warn("sync", "Auto-sync skipped: sync already in progress");
    return;
  }
  if (!(await isConfigured())) {
    log.warn("sync", "Auto-sync skipped: not configured");
    return;
  }

  const { dwmp_auto_sync } = await chrome.storage.local.get("dwmp_auto_sync");
  if (!dwmp_auto_sync) {
    log.warn("sync", "Auto-sync skipped: auto-sync disabled");
    return;
  }

  const enabledIds = Object.entries(dwmp_auto_sync)
    .filter(([, v]) => v)
    .map(([k]) => Number(k));
  if (enabledIds.length === 0) {
    log.warn("sync", "Auto-sync skipped: no enabled accounts");
    return;
  }

  const result = await listAccounts();
  if (!result.ok) {
    log.error("sync", "Auto-sync: failed to list accounts", { error: result.error });
    return;
  }

  log.info("sync", "Auto-sync starting", { enabledAccounts: enabledIds.length });
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
    log.info("sync", "Auto-sync completed", { carriers: [...carriersDone] });
  } finally {
    syncInProgress = false;
  }
}

async function syncCarrierViaTab(account, opts = {}) {
  const _syncStart = Date.now();
  log.info("sync", `syncCarrierViaTab: ${account.carrier}`, {
    accountId: account.id,
    reusingTab: opts.tabId != null,
  });

  const urls = CARRIER_SYNC_URLS[account.carrier];
  if (!urls?.parcels) {
    log.error("sync", `No sync URL configured for ${account.carrier}`);
    await storeSyncResult(account.id, false, `No sync URL for ${account.carrier}`);
    return;
  }

  // Guard against two browser instances (e.g. desktop + laptop) triggering
  // a login-based sync concurrently. If the server already recorded a sync
  // within the last half-interval, the other instance beat us to it — skip.
  // This avoids both instances clearing site data and fighting over the same
  // Akamai bot-challenge on the same PostNL session.
  if (urls.login && account.last_synced && !opts.tabId) {
    const { dwmp_sync_interval } = await chrome.storage.local.get("dwmp_sync_interval");
    const intervalMs = (dwmp_sync_interval || DEFAULT_SYNC_INTERVAL_MIN) * 60_000;
    const ageMs = Date.now() - new Date(account.last_synced).getTime();
    if (ageMs < intervalMs / 2) {
      log.info("sync", `${account.carrier}: skipping — synced ${Math.round(ageMs / 1000)}s ago by another instance`);
      return;
    }
  }

  // Carriers with a login URL require stored credentials so the extension
  // can fill in the login form automatically.
  if (urls.login && !account.has_credentials) {
    log.warn("sync", `${account.carrier}: no credentials configured`);
    await storeSyncResult(
      account.id,
      false,
      "No credentials configured -- add them in the DWMP dashboard",
    );
    return;
  }

  // opts.tabId: caller-supplied tab to reuse (current-tab sync path).
  // When set we never close the tab, never wipe site data, and skip the
  // tab-open/navigate-to-startUrl block entirely.
  const callerTabId = opts.tabId ?? null;
  let tabId = callerTabId;
  let shouldCloseTab = callerTabId === null;

  // Always go through the login URL when one is configured. This guarantees
  // the carrier presents its sign-in form (or skips straight to the
  // destination if a session is already active) — far more reliable than
  // hoping the parcels page redirects to login on cookie expiry.
  const startUrl = urls.login || urls.parcels;

  try {
    if (callerTabId === null) {
      // Wipe all carrier session data before every login-URL sync. Stale cookies
      // and broken SSO state are the most common cause of silent login failures;
      // credentials are always stored at this point so we can re-login cleanly.
      if (urls.login) {
        await clearCarrierSiteData(account.carrier);
      }

      // When a login URL is configured we always clear site data and force a
      // fresh login — reusing an existing tab would log the user out of their
      // active browser session, so always open a dedicated tab in that case.
      const domain = new URL(startUrl).hostname;
      const existing = urls.login
        ? []
        : await chrome.tabs.query({ url: `*://*.${domain}/*` });
      if (existing.length > 0) {
        tabId = existing[0].id;
        shouldCloseTab = false;
        await chrome.tabs.update(tabId, { url: startUrl });
      } else if (urls.login) {
        // Carriers with a login flow use an unfocused popup window rather than
        // an inactive background tab. Background tabs suppress requestAnimationFrame,
        // which prevents Akamai's bot-detection script from completing its
        // fingerprinting pass — blocking all subsequent API requests. A popup
        // window (even unfocused) has an active rendering pipeline, so RAF fires
        // and Akamai accepts the session. The window is closed after the sync.
        const win = await chrome.windows.create({
          url: startUrl,
          type: "popup",
          focused: false,
          width: 480,
          height: 640,
        });
        tabId = win.tabs[0].id;
      } else {
        const tab = await chrome.tabs.create({ url: startUrl, active: false });
        tabId = tab.id;
      }
    }

    // When reusing the caller's tab it's already loaded — skip the load wait
    // (which only resolves on a new navigation event and would time out).
    if (callerTabId === null) {
      await waitForTabLoad(tabId);
      await waitForUrlStable(tabId);
    }

    // If the tab landed on a login page (URL match OR a login form is
    // visible in the DOM), fill credentials and submit.
    const tabInfo = await chrome.tabs.get(tabId);
    const onLogin =
      isCarrierLoginPage(account.carrier, tabInfo.url) ||
      (await hasLoginForm(tabId));
    log.info("sync", `${account.carrier}: login page detected: ${onLogin}`, { url: tabInfo.url });
    if (onLogin) {
      const loggedIn = await handleCarrierLogin(tabId, account);
      if (!loggedIn) {
        log.error("sync", `${account.carrier}: login failed`);
        await storeSyncResult(account.id, false, "Login failed -- check credentials");
        return;
      }
      log.info("sync", `${account.carrier}: login succeeded`);
      await waitForUrlStable(tabId);
    }

    // PostNL: read the access token from sessionStorage after the OIDC callback
    // sets it on jouw.postnl.nl/account/login. Navigate to the parcels page only
    // after the token is secured — navigating away first loses the token.
    if (account.carrier === "postnl") {
      log.info("postnl", "stage", { stage: "token-loop-start" });
      // We navigate directly to login.postnl.nl (bypassing jouw.postnl.nl/account
      // and its Akamai bot-challenge). The outer isCarrierLoginPage check above
      // handles the initial login. This loop catches any unexpected re-auth
      // redirects and waits for poa.auth.access_token to appear in sessionStorage
      // after the OAuth callback completes on jouw.postnl.nl/account/login.
      let loginHandled = onLogin;
      let accessToken = null;
      let storageDiagLogged = false;
      const deadline = Date.now() + 60_000;
      while (Date.now() < deadline) {
        const tabUrl = (await chrome.tabs.get(tabId)).url || "";

        if ((isCarrierLoginPage("postnl", tabUrl) || (await hasLoginForm(tabId))) && !loginHandled) {
          loginHandled = true;
          log.info("postnl", "stage", { stage: "login-start", url: tabUrl });
          const loggedIn = await handleCarrierLogin(tabId, account);
          if (!loggedIn) {
            const finalUrl = (await chrome.tabs.get(tabId)).url || "";
            log.error("postnl", "Login failed in token loop", { url: finalUrl });
            await storeSyncResult(account.id, false, "Login failed -- check credentials");
            return;
          }
          log.info("postnl", "stage", { stage: "login-done" });
          await waitForUrlStable(tabId);
          continue;
        }
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
        if (!accessToken && !storageDiagLogged) {
          storageDiagLogged = true;
          const diagResult = await chrome.scripting.executeScript({
            target: { tabId },
            func: () => ({
              sessionKeys: Object.keys(sessionStorage),
              localAuthKeys: Object.keys(localStorage).filter(k => k.includes("token") || k.includes("auth") || k.includes("poa")),
            }),
          }).catch(() => null);
          log.info("postnl", "Token not found — storage keys", diagResult?.[0]?.result ?? {});
        }
        if (accessToken) break;
        await sleep(500);
      }
      if (!accessToken) {
        const finalUrl = (await chrome.tabs.get(tabId)).url || "";
        const diagResult = await chrome.scripting.executeScript({
          target: { tabId },
          func: () => ({
            sessionKeys: Object.keys(sessionStorage),
            localAuthKeys: Object.keys(localStorage).filter(k => k.includes("token") || k.includes("auth") || k.includes("poa")),
          }),
        }).catch(() => null);
        log.error("postnl", "Token extraction timed out", {
          finalUrl,
          loginHandled,
          ...(diagResult?.[0]?.result ?? {}),
        });
        await storeSyncResult(account.id, false, "Could not extract PostNL token from session");
        return;
      }
      log.info("postnl", "stage", { stage: "token-captured" });

      // Token secured — now navigate to the parcels page for the payload fetch.
      const postnlUrl = (await chrome.tabs.get(tabId)).url || "";
      if (!postnlUrl.startsWith(urls.parcels)) {
        await chrome.tabs.update(tabId, { url: urls.parcels });
        await waitForTabLoad(tabId);
        await waitForUrlStable(tabId);
      }

      log.info("postnl", "Token acquired, fetching payload", { tabId });
      const payloadResult = await fetchPostNLPayload(tabId, accessToken);
      if (!payloadResult.ok) {
        log.error("postnl", "Payload fetch failed", { error: payloadResult.error });
        await storeSyncResult(account.id, false, payloadResult.error || "Failed to fetch PostNL data");
        return;
      }
      log.info("postnl", "Payload fetched", {
        shipments: payloadResult.data?.shipments?.length ?? 0,
        details: payloadResult.data?.details?.length ?? 0,
      });

      const syncResult = await browserPayload(account.id, payloadResult.data);
      const count = Array.isArray(syncResult.data) ? syncResult.data.length : 0;
      await storeSyncResult(account.id, syncResult.ok, syncResult.ok ? null : syncResult.error, count);
      return;
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

    let html = await captureTabHtml(tabId);
    if (!html) {
      log.error("sync", `${account.carrier}: captureTabHtml returned empty`);
      await storeSyncResult(account.id, false, "Could not capture page content");
      return;
    }
    log.debug("sync", `${account.carrier}: captured HTML`, { htmlBytes: html.length });

    // Detect Cloudflare challenge page
    if (isCloudflareChallenge(html)) {
      log.warn("sync", `${account.carrier}: Cloudflare challenge detected`);
      // Make the tab visible so the user can solve the captcha
      await chrome.tabs.update(tabId, { active: true });
      shouldCloseTab = false;
      await storeSyncResult(account.id, false, "Cloudflare challenge -- please solve manually");
      return;
    }

    // Detect carrier error/outage page (e.g. DPD "Technical issue occurred").
    // Clear site data for the carrier domain and retry once with a fresh login —
    // stale cookies are the most common cause of DPD landing on its error page.
    // When reusing the caller's tab we skip site-data wipe to preserve the
    // user's active session and surface the error instead.
    if (isCarrierErrorPage(html)) {
      log.warn("sync", `${account.carrier}: carrier error page detected`);
      if (callerTabId !== null) {
        await storeSyncResult(account.id, false, "Carrier site error -- try again later");
        return;
      }
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
    log.info("sync", `${account.carrier}: pushing HTML to DWMP`, { htmlBytes: html.length, url: pageUrl });
    const pushResult = await browserPush(html, pageUrl);

    if (pushResult.ok) {
      const count = pushResult.data?.length || 0;
      log.info("sync", `${account.carrier}: browser-push succeeded`, { packages: count, durationMs: Date.now() - _syncStart });
      await storeSyncResult(account.id, true, null, count);
    } else {
      log.error("sync", `${account.carrier}: browser-push failed`, { error: pushResult.error });
      await storeSyncResult(account.id, false, pushResult.error);
    }
  } catch (err) {
    log.error("sync", `${account.carrier}: unhandled error`, { error: err.message, stack: err.stack });
    await storeSyncResult(account.id, false, err.message);
  } finally {
    if (shouldCloseTab && tabId !== null) {
      // If we opened a popup window for this sync, close the whole window.
      // Otherwise just remove the tab (background-tab path or caller-reuse path).
      chrome.tabs.get(tabId).then((tab) => {
        if (tab.windowId) {
          chrome.windows.get(tab.windowId).then((win) => {
            if (win.type === "popup") {
              chrome.windows.remove(win.id).catch(() => {});
            } else {
              chrome.tabs.remove(tabId).catch(() => {});
            }
          }).catch(() => chrome.tabs.remove(tabId).catch(() => {}));
        } else {
          chrome.tabs.remove(tabId).catch(() => {});
        }
      }).catch(() => {});
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
        // PostNL Capture (CDC) screenset — present on triggerlogin and login.postnl.nl.
        if (
          document.querySelector(
            "#capture_signIn_signInEmailAddress, [data-capturefield='signInEmailAddress']",
          )
        )
          return true;
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
  log.info("postnl", "Injecting payload fetch script", { tabId });
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
    const data = results?.[0]?.result || { shipments: [], details: [] };
    log.info("postnl", "Injected fetch complete", { shipments: data.shipments?.length ?? 0, details: data.details?.length ?? 0 });
    return { ok: true, data };
  } catch (err) {
    log.error("postnl", "Injected fetch threw", { error: err.message });
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
  log.info("sync", `Clearing site data for ${carrier}`);
  const config = CARRIER_AUTH_CLEAR[carrier];
  if (!config) return;

  // Cookies: enumerate by eTLD+1 — covers all subdomains via cookie-spec
  // domain matching (e.g. Keycloak SSO cookies on .dpdgroup.com).
  // Skip cookies that must be preserved across syncs (e.g. Akamai bot tokens).
  const preserved = new Set(config.preserveCookies || []);
  const cookies = await chrome.cookies.getAll({ domain: config.cookieDomain });
  await Promise.all(
    cookies
      .filter((c) => !preserved.has(c.name))
      .map((c) => {
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

/**
 * Poll until the PostNL CDC login form has rendered in the tab.
 * Returns "email" when the email field is present, "password-only" when only
 * the password field is visible (remembered-email flow), or null on timeout.
 */
async function waitForPostNLLoginForm(tabId, maxMs = 15_000) {
  // Wait for the SAP CDC (Gigya) screenset to render the login form.
  // The page always has hidden 2FA inputs (type=text) — only match the
  // CDC-specific selectors to avoid false positives.
  // Returns "both" when single-step (email + password visible simultaneously),
  // "email" when only email visible (two-step flow), "password-only", or null.
  log.debug("login", "Waiting for PostNL login form", { tabId });
  const deadline = Date.now() + maxMs;
  let emailFoundAt = null;
  let domDumped = false;
  while (Date.now() < deadline) {
    try {
      const result = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          const emailEl =
            document.querySelector("#capture_signIn_signInEmailAddress") ||
            document.querySelector("[data-capturefield='signInEmailAddress']");
          const passEl =
            document.querySelector("#capture_signIn_currentPassword") ||
            document.querySelector("input[type='password']");
          if (emailEl && passEl) return "both";
          if (emailEl) return "email";
          if (passEl && passEl.offsetParent !== null) return "password-only";
          return null;
        },
      });
      const layout = result?.[0]?.result;
      if (layout === "both" || layout === "password-only") return layout;
      if (layout === "email") {
        if (!emailFoundAt) emailFoundAt = Date.now();
        // Give the password field 3 s to appear alongside the email field
        // before concluding it's a genuine two-step flow.
        if (Date.now() - emailFoundAt > 3_000) return "email";
      }
      // After 5s with no match, dump DOM inputs to aid debugging
      if (!domDumped && Date.now() > deadline - maxMs + 5_000) {
        domDumped = true;
        chrome.scripting.executeScript({
          target: { tabId },
          func: () => ({
            inputs: Array.from(document.querySelectorAll("input")).map((el) => ({
              id: el.id, type: el.type, dataCap: el.getAttribute("data-capturefield"), visible: el.offsetParent !== null,
            })),
            url: location.href,
            bodySnippet: document.body?.innerText?.substring(0, 200),
          }),
        }).then(([{ result: dump }]) => {
          if (dump) log.info("login", "PostNL: DOM dump (no form found yet)", dump);
        }).catch(() => {});
      }
    } catch {
      // scripting not yet available (page still loading)
    }
    await sleep(250);
  }
  return null;
}

async function handlePostNLLogin(tabId, username, password) {
  // PostNL uses SAP CDC (Capture/Gigya) screensets. The live form shows both
  // the email field (#capture_signIn_signInEmailAddress) and the password
  // field (#capture_signIn_currentPassword) simultaneously — single-step.
  //
  // The screenset renders asynchronously after URL stabilisation — wait for
  // either field to appear before attempting to fill.
  const formLayout = await waitForPostNLLoginForm(tabId);
  log.info("login", `PostNL login form layout: ${formLayout ?? "not found"}`, { tabId });
  if (!formLayout) return false;

  const fillAndSubmit = (email, pass) => {
    const emailEl =
      document.querySelector("#capture_signIn_signInEmailAddress") ||
      document.querySelector("[data-capturefield='signInEmailAddress']");
    const passEl =
      document.querySelector("#capture_signIn_currentPassword") ||
      document.querySelector("input[type='password']");
    if (emailEl) {
      emailEl.value = email;
      emailEl.dispatchEvent(new Event("input", { bubbles: true }));
      emailEl.dispatchEvent(new Event("change", { bubbles: true }));
      emailEl.dispatchEvent(new Event("blur", { bubbles: true }));
    }
    if (passEl) {
      passEl.value = pass;
      passEl.dispatchEvent(new Event("input", { bubbles: true }));
      passEl.dispatchEvent(new Event("change", { bubbles: true }));
    }
    const btn =
      document.querySelector("button[type='submit']") ||
      document.querySelector("input[type='submit']");
    if (btn) btn.click();
    const allInputs = Array.from(document.querySelectorAll("input")).map(
      (el) => ({ id: el.id, type: el.type, dataCap: el.getAttribute("data-capturefield"), visible: el.offsetParent !== null })
    );
    return { hasEmail: !!emailEl, hasPassword: !!passEl, allInputs };
  };

  const [{ result: fields }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: fillAndSubmit,
    args: [username, password],
  });
  log.info("login", "PostNL: fillAndSubmit result", { hasEmail: fields?.hasEmail, hasPassword: fields?.hasPassword, inputs: fields?.allInputs });

  if (!fields?.hasPassword && formLayout !== "both") {
    // Email-only step — wait for the password field to appear and submit again.
    log.info("login", "PostNL: email-only step, waiting for password field", { tabId });
    await waitForPostNLLoginForm(tabId);
    const mid = await chrome.tabs.get(tabId);
    if (isCarrierLoginPage("postnl", mid.url)) {
      await chrome.scripting.executeScript({
        target: { tabId },
        func: (pass) => {
          const passEl =
            document.querySelector("#capture_signIn_currentPassword") ||
            document.querySelector("input[type='password']");
          if (passEl) {
            passEl.value = pass;
            passEl.dispatchEvent(new Event("input", { bubbles: true }));
            passEl.dispatchEvent(new Event("change", { bubbles: true }));
            const btn =
              document.querySelector("button[type='submit']") ||
              document.querySelector("input[type='submit']");
            if (btn) btn.click();
          }
        },
        args: [password],
      });
    }
  }

  await waitForUrlStable(tabId, 2000, 15000);
  const info = await chrome.tabs.get(tabId);
  const success = !isCarrierLoginPage("postnl", info.url);
  log.info("login", `PostNL login ${success ? "succeeded" : "failed"}`, { finalUrl: info.url });
  return success;
}

async function handleCarrierLogin(tabId, account) {
  log.info("login", `handleCarrierLogin: ${account.carrier}`, { accountId: account.id, tabId });
  const result = await getAccountCredentials(account.id);
  if (!result.ok || !result.data?.has_credentials) {
    log.warn("login", `No credentials available for ${account.carrier}`, { accountId: account.id });
    return false;
  }

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
        document.querySelector("input[name='username']") ||
        document.querySelector("input[name='email']") ||
        document.querySelector("input[type='email']");
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
  log.info("login", "Amazon login started", { tabId });
  // Dismiss the EU cookie consent banner up front (it can overlay the form).
  await dismissAmazonCookieBanner(tabId);

  const MAX_STEPS = 5;
  for (let step = 0; step < MAX_STEPS; step++) {
    const state = await detectAmazonLoginState(tabId);

    log.info("login", `Amazon login step ${step}: state=${state}`, { tabId });

    if (state === "captcha" || state === "mfa") {
      log.warn("login", `Amazon login requires manual action: ${state}`);
      await chrome.tabs.update(tabId, { active: true });
      return false;
    }

    // No recognizable form on the page: either we've reached the post-login
    // destination, or we're stuck on a page we don't know how to fill.
    if (state === "none") {
      const info = await chrome.tabs.get(tabId);
      const success = !isCarrierLoginPage("amazon", info.url);
      log.info("login", `Amazon login state=none, ${success ? "succeeded" : "failed"}`, { url: info.url });
      return success;
    }

    const nav = waitForTabNavigation(tabId, TAB_TIMEOUT_MS);
    const submitted = await fillAndSubmitAmazonStep(tabId, state, email, password);
    if (!submitted) {
      log.error("login", `Amazon login: fillAndSubmit failed for state=${state}`);
      await chrome.tabs.update(tabId, { active: true });
      return false;
    }
    try { await nav; } catch { return false; }
    await waitForUrlStable(tabId);
    // Cookie banner sometimes re-renders on the next page.
    await dismissAmazonCookieBanner(tabId);
  }

  log.warn("login", "Amazon login: ran out of steps");
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
  log.info("sync-result", `Account ${accountId}: ${ok ? "ok" : "failed"}`, {
    accountId,
    ok,
    error: error || null,
    count: count ?? 0,
  });
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
  log.debug("update", "Checking for extension update");
  const update = await checkForUpdate();
  if (update) {
    log.info("update", "Update available", { version: update.version });
    await chrome.storage.local.set({ dwmp_update: update });
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#00b894" });
  } else {
    log.debug("update", "Extension is up to date");
    await chrome.storage.local.remove("dwmp_update");
    chrome.action.setBadgeText({ text: "" });
  }
}

// ── Message handler (popup ↔ service worker) ───────────────────────

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  log.debug("message", `Received: ${msg.type}`);

  if (msg.type === "sync-current-tab") {
    handleSyncCurrentTab().then(sendResponse);
    return true; // keep channel open for async
  }

  if (msg.type === "trigger-sync") {
    handleTriggerSync(msg.accountId).then(sendResponse);
    return true;
  }

  if (msg.type === "update-auto-sync") {
    log.info("message", "update-auto-sync", { accountId: msg.accountId, enabled: msg.enabled });
    handleUpdateAutoSync(msg.accountId, msg.enabled).then(sendResponse);
    return true;
  }

  if (msg.type === "update-sync-interval") {
    log.info("message", "update-sync-interval", { interval: msg.interval });
    chrome.alarms.create("dwmp-auto-sync", { periodInMinutes: msg.interval });
    chrome.storage.local.set({ dwmp_sync_interval: msg.interval });
    sendResponse({ ok: true });
    return false;
  }

  if (msg.type === "check-update") {
    runUpdateCheck().then(() => sendResponse({ ok: true }));
    return true;
  }

  if (msg.type === "get-log-buffer") {
    sendResponse(log.getBuffer());
    return false;
  }

  // Test-only: configure extension storage from an automated test harness.
  // Callers cannot use chrome.storage directly (CDP evaluation context lacks it)
  // but CAN reach this handler via chrome.runtime.sendMessage from the SW target.
  if (msg.type === "test-set-config") {
    chrome.storage.local.set({
      dwmp_url: msg.url,
      dwmp_token: msg.token,
      ...(msg.accountId !== undefined
        ? { dwmp_auto_sync: { [String(msg.accountId)]: true } }
        : {}),
    }).then(() => sendResponse({ ok: true }));
    return true;
  }
});

async function handleSyncCurrentTab() {
  log.info("sync", "Sync current tab requested");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) {
    log.warn("sync", "Sync current tab: no active tab");
    return { ok: false, error: "No active tab" };
  }

  const carrier = detectCarrier(tab.url);
  if (!carrier) {
    log.warn("sync", "Sync current tab: not on a carrier site", { url: tab.url });
    return { ok: false, error: "Not on a carrier site" };
  }
  log.info("sync", `Sync current tab: detected carrier ${carrier}`, { tabId: tab.id, url: tab.url });

  const accounts = await listAccounts();
  if (!accounts.ok) return accounts;

  const account = accounts.data.find((a) => a.carrier === carrier);
  if (!account) return { ok: false, error: `No ${carrier} account configured` };

  if (syncInProgress) return { ok: false, error: "Sync already in progress" };
  syncInProgress = true;
  try {
    await syncCarrierViaTab(account, { tabId: tab.id });
    const { dwmp_sync_results } = await chrome.storage.local.get("dwmp_sync_results");
    return dwmp_sync_results?.[account.id] || { ok: true };
  } finally {
    syncInProgress = false;
  }
}

async function handleTriggerSync(accountId) {
  log.info("sync", `Trigger sync requested for account ${accountId}`);
  if (syncInProgress) {
    log.warn("sync", "Trigger sync skipped: already in progress");
    return { ok: false, error: "Sync already in progress" };
  }

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
