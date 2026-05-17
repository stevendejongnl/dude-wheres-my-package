#!/usr/bin/env node
/**
 * Local Playwright test for the PostNL sync flow.
 *
 * Loads the Chrome extension into a real Chrome window, configures it to talk
 * to your DWMP server, triggers a PostNL sync, and streams the extension logs
 * live so you can see exactly where it succeeds or fails.
 *
 * Usage:
 *   DWMP_URL=https://your-server DWMP_TOKEN=your-token node scripts/test-postnl-sync.mjs
 *
 * Optional:
 *   POSTNL_ACCOUNT_ID=19   (defaults to 19)
 *   KEEP_OPEN=1            (leave browser open after sync for manual inspection)
 */
import { chromium } from "playwright";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const extensionPath = path.resolve(__dirname, "../chrome-extension");

const DWMP_URL = process.env.DWMP_URL?.replace(/\/+$/, "");
const DWMP_TOKEN = process.env.DWMP_TOKEN;
const ACCOUNT_ID = Number(process.env.POSTNL_ACCOUNT_ID || "19");
const KEEP_OPEN = process.env.KEEP_OPEN === "1";

if (!DWMP_URL || !DWMP_TOKEN) {
  console.error(
    "Usage: DWMP_URL=https://... DWMP_TOKEN=... node scripts/test-postnl-sync.mjs",
  );
  process.exit(1);
}

// ── Helpers ────────────────────────────────────────────────────────

async function fetchLogs(since, context) {
  const url = new URL(`${DWMP_URL}/api/v1/logs`);
  url.searchParams.set("since", since);
  url.searchParams.set("context", context);
  url.searchParams.set("limit", "200");
  const res = await fetch(url.toString(), {
    headers: { Authorization: `Bearer ${DWMP_TOKEN}` },
  });
  if (!res.ok) throw new Error(`Logs API ${res.status}`);
  return res.json();
}

function formatLog(l) {
  const icon =
    l.level === "error" ? "❌" : l.level === "warn" ? "⚠️ " : "   ";
  const data = l.data && Object.keys(l.data).length ? " " + JSON.stringify(l.data) : "";
  return `${icon} [${l.category}] ${l.message}${data}`;
}

// ── Main ───────────────────────────────────────────────────────────

async function main() {
  console.log(`\nDWMP PostNL sync tester`);
  console.log(`  Server : ${DWMP_URL}`);
  console.log(`  Account: ${ACCOUNT_ID}`);
  console.log(`  Extension: ${extensionPath}\n`);

  // Use a fresh temp profile each run so storage/cookies don't carry over
  const userDataDir = `/tmp/dwmp-playwright-${Date.now()}`;

  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: "chrome",   // use system Chrome — required for extension support
    headless: false,
    args: [
      `--disable-extensions-except=${extensionPath}`,
      `--load-extension=${extensionPath}`,
    ],
  });

  try {
    // ── Wait for extension service worker ──────────────────────────
    let sw = context.serviceWorkers()[0];
    if (!sw) {
      console.log("Waiting for extension service worker...");
      sw = await context.waitForEvent("serviceworker", { timeout: 15_000 });
    }
    const extensionId = new URL(sw.url()).hostname;
    console.log(`Extension loaded  id=${extensionId}\n`);

    // ── Open popup page (extension context for chrome.* API access) ─
    const popupPage = await context.newPage();
    await popupPage.goto(`chrome-extension://${extensionId}/popup/popup.html`, {
      waitUntil: "domcontentloaded",
    });

    // ── Configure extension storage ─────────────────────────────────
    await popupPage.evaluate(
      async ({ url, token, accountId }) => {
        await chrome.storage.local.set({
          dwmp_url: url,
          dwmp_token: token,
          dwmp_auto_sync: { [String(accountId)]: true },
        });
      },
      { url: DWMP_URL, token: DWMP_TOKEN, accountId: ACCOUNT_ID },
    );
    console.log("Extension configured.\n");

    // ── Trigger sync via runtime message ────────────────────────────
    console.log(`Triggering PostNL sync for account ${ACCOUNT_ID}...`);
    const syncStarted = Date.now();
    const syncResult = await popupPage.evaluate(
      (accountId) =>
        new Promise((resolve) => {
          // 90 s timeout — popup won't receive the response until SW finishes
          const timer = setTimeout(() => resolve({ ok: false, error: "timeout" }), 90_000);
          chrome.runtime.sendMessage({ type: "trigger-sync", accountId }, (result) => {
            clearTimeout(timer);
            resolve(result ?? { ok: false, error: "no response" });
          });
        }),
      ACCOUNT_ID,
    );
    const elapsed = ((Date.now() - syncStarted) / 1000).toFixed(1);
    console.log(`\nSync finished in ${elapsed}s:`, syncResult);

    // ── Fetch and display logs from server ──────────────────────────
    const since = new Date(syncStarted - 5_000).toISOString();
    try {
      const logs = await fetchLogs(since, "");
      if (logs.length) {
        console.log(`\n── Extension logs (${logs.length} entries) ──────────────────`);
        for (const l of logs) console.log(formatLog(l));
        console.log("──────────────────────────────────────────────────────────\n");
      } else {
        console.log("(no logs on server — check server connectivity)\n");
      }
    } catch (e) {
      console.error("Could not fetch logs from server:", e.message);
    }

    // ── Summary ─────────────────────────────────────────────────────
    if (syncResult?.ok) {
      console.log(`✅ Sync succeeded — ${syncResult.count ?? 0} package(s) synced`);
    } else {
      console.log(`❌ Sync failed: ${syncResult?.error ?? "unknown error"}`);
    }

    if (KEEP_OPEN) {
      console.log("\nKEEP_OPEN=1 — browser left open. Close it manually.\n");
      await new Promise(() => {}); // hang forever
    }
  } finally {
    if (!KEEP_OPEN) await context.close();
  }
}

main().catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});
