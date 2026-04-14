import {
  authenticate,
  clearConfig,
  getConfig,
  healthCheck,
  isConfigured,
  listAccounts,
  listPackages,
} from "../lib/api.js";
import { CARRIER_LABELS, detectCarrier } from "../lib/carriers.js";

// ── DOM refs ───────────────────────────────────────────────────────

const setupView = document.getElementById("setup-view");
const mainView = document.getElementById("main-view");

const setupUrl = document.getElementById("setup-url");
const setupPassword = document.getElementById("setup-password");
const setupConnect = document.getElementById("setup-connect");
const setupError = document.getElementById("setup-error");

const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const versionBadge = document.getElementById("version-badge");
const updateBanner = document.getElementById("update-banner");
const updateVersion = document.getElementById("update-version");
const updateLink = document.getElementById("update-link");

const syncPageBtn = document.getElementById("sync-page-btn");
const syncPageText = document.getElementById("sync-page-text");
const accountsList = document.getElementById("accounts-list");
const pkgActive = document.getElementById("pkg-active");
const pkgDelivered = document.getElementById("pkg-delivered");
const syncInterval = document.getElementById("sync-interval");
const openDashboard = document.getElementById("open-dashboard");
const disconnect = document.getElementById("disconnect");

// ── Init ───────────────────────────────────────────────────────────

async function init() {
  if (await isConfigured()) {
    showMainView();
  } else {
    showSetupView();
  }
}

function showSetupView() {
  setupView.classList.remove("hidden");
  mainView.classList.add("hidden");
  setupUrl.focus();
}

async function showMainView() {
  setupView.classList.add("hidden");
  mainView.classList.remove("hidden");

  const { url } = await getConfig();
  const version = chrome.runtime.getManifest().version;
  versionBadge.textContent = `v${version}`;

  // Set dashboard link
  openDashboard.addEventListener("click", (e) => {
    e.preventDefault();
    chrome.tabs.create({ url });
  });

  // Load interval preference
  const { dwmp_sync_interval } = await chrome.storage.local.get("dwmp_sync_interval");
  if (dwmp_sync_interval) syncInterval.value = String(dwmp_sync_interval);

  // Load data in parallel
  await Promise.all([
    loadConnectionStatus(url),
    loadCurrentTab(),
    loadAccounts(),
    loadPackages(),
    loadUpdateBanner(),
  ]);
}

// ── Connection status ──────────────────────────────────────────────

async function loadConnectionStatus(url) {
  try {
    await healthCheck(url);
    statusDot.className = "status-dot connected";
    const host = new URL(url).hostname;
    statusText.textContent = host;
  } catch {
    statusDot.className = "status-dot error";
    statusText.textContent = "Cannot reach server";
  }
}

// ── Current tab detection ──────────────────────────────────────────

async function loadCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url) return;

  const carrier = detectCarrier(tab.url);
  if (carrier) {
    const label = CARRIER_LABELS[carrier] || carrier;
    syncPageBtn.disabled = false;
    syncPageText.textContent = `Sync ${label}`;
  }
}

// ── Accounts list ──────────────────────────────────────────────────

async function loadAccounts() {
  const result = await listAccounts();
  if (!result.ok) {
    setEmptyMessage(
      accountsList,
      result.status === 401 ? "Token expired -- please reconnect" : result.error,
    );
    if (result.status === 401) {
      statusDot.className = "status-dot error";
      statusText.textContent = "Token expired";
    }
    return;
  }

  const accounts = result.data;
  if (accounts.length === 0) {
    setEmptyMessage(accountsList, "No accounts connected. Add them in the DWMP dashboard.");
    return;
  }

  const { dwmp_auto_sync, dwmp_sync_results } = await chrome.storage.local.get([
    "dwmp_auto_sync",
    "dwmp_sync_results",
  ]);
  const autoSync = dwmp_auto_sync || {};
  const syncResults = dwmp_sync_results || {};

  accountsList.textContent = "";
  for (const account of accounts) {
    accountsList.appendChild(
      renderAccountRow(account, autoSync[account.id] || false, syncResults[account.id]),
    );
  }
}

function renderAccountRow(account, autoSyncEnabled, syncResult) {
  const row = document.createElement("div");
  row.className = "account-row";
  row.dataset.accountId = account.id;

  const carrier = account.carrier;
  const label = CARRIER_LABELS[carrier] || carrier;

  // Carrier badge
  const badge = document.createElement("span");
  badge.className = `carrier-badge ${carrier}`;
  badge.textContent = label;

  // Account info
  const info = document.createElement("div");
  info.className = "account-info";

  const name = document.createElement("div");
  name.className = "account-name";

  const dot = document.createElement("span");
  dot.className = `status-dot ${account.status === "connected" ? "connected" : "error"}`;
  name.appendChild(dot);
  name.appendChild(document.createTextNode(" " + (account.username || carrier)));

  const meta = document.createElement("div");
  meta.className = "account-meta";
  meta.textContent = formatSyncMeta(account, syncResult);

  info.appendChild(name);
  info.appendChild(meta);

  // Actions
  const actions = document.createElement("div");
  actions.className = "account-actions";

  // Auto-sync toggle
  const toggleLabel = document.createElement("label");
  toggleLabel.className = "toggle";
  toggleLabel.title = "Auto-sync";

  const toggleInput = document.createElement("input");
  toggleInput.type = "checkbox";
  toggleInput.checked = autoSyncEnabled;

  const toggleSlider = document.createElement("span");
  toggleSlider.className = "toggle-slider";

  toggleLabel.appendChild(toggleInput);
  toggleLabel.appendChild(toggleSlider);

  toggleInput.addEventListener("change", () => {
    chrome.runtime.sendMessage({
      type: "update-auto-sync",
      accountId: account.id,
      enabled: toggleInput.checked,
    });
  });

  // Sync button
  const syncBtn = document.createElement("button");
  syncBtn.className = "btn btn-ghost";
  syncBtn.textContent = "Sync";

  syncBtn.addEventListener("click", async () => {
    syncBtn.disabled = true;
    syncBtn.textContent = "";
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    syncBtn.appendChild(spinner);

    const result = await chrome.runtime.sendMessage({
      type: "trigger-sync",
      accountId: account.id,
    });

    if (result?.ok) {
      syncBtn.textContent = `${result.count || 0} pkg`;
      meta.textContent = "Just now";
    } else {
      syncBtn.textContent = "Failed";
      meta.textContent = result?.error || "Sync failed";
    }

    setTimeout(() => {
      syncBtn.disabled = false;
      syncBtn.textContent = "Sync";
    }, 3000);
  });

  actions.appendChild(toggleLabel);
  actions.appendChild(syncBtn);

  row.appendChild(badge);
  row.appendChild(info);
  row.appendChild(actions);

  return row;
}

function formatSyncMeta(account, syncResult) {
  if (syncResult?.timestamp) {
    const ago = timeAgo(syncResult.timestamp);
    if (syncResult.ok) return `Synced ${ago} (${syncResult.count} pkg)`;
    return `Failed ${ago}: ${syncResult.error}`;
  }
  if (account.last_synced) return `Last sync: ${timeAgo(account.last_synced)}`;
  if (account.status === "auth_failed") return "Authentication failed";
  return "Never synced";
}

// ── Packages summary ───────────────────────────────────────────────

async function loadPackages() {
  const result = await listPackages();
  if (!result.ok) return;

  const packages = result.data;
  const active = packages.filter(
    (p) => p.current_status !== "delivered" && p.current_status !== "returned",
  ).length;
  const delivered = packages.filter(
    (p) => p.current_status === "delivered" || p.current_status === "returned",
  ).length;

  pkgActive.textContent = active;
  pkgDelivered.textContent = delivered;
}

// ── Update banner ──────────────────────────────────────────────────

async function loadUpdateBanner() {
  const { dwmp_update } = await chrome.storage.local.get("dwmp_update");
  if (dwmp_update?.version) {
    updateVersion.textContent = dwmp_update.version;
    updateLink.href = dwmp_update.downloadUrl;
    updateBanner.classList.remove("hidden");
  }
}

// ── Setup flow ─────────────────────────────────────────────────────

setupConnect.addEventListener("click", async () => {
  const url = setupUrl.value.trim();
  const password = setupPassword.value;

  if (!url) return showSetupError("Server URL is required");
  if (!password) return showSetupError("Password is required");

  setupConnect.disabled = true;
  setupConnect.textContent = "Connecting...";
  hideSetupError();

  try {
    await healthCheck(url);
    await authenticate(url, password);
    showMainView();
  } catch (err) {
    showSetupError(err.message);
  } finally {
    setupConnect.disabled = false;
    setupConnect.textContent = "Connect";
  }
});

setupPassword.addEventListener("keydown", (e) => {
  if (e.key === "Enter") setupConnect.click();
});

setupUrl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") setupPassword.focus();
});

function showSetupError(msg) {
  setupError.textContent = msg;
  setupError.classList.add("visible");
}

function hideSetupError() {
  setupError.classList.remove("visible");
}

// ── Sync current page ──────────────────────────────────────────────

syncPageBtn.addEventListener("click", async () => {
  syncPageBtn.disabled = true;
  syncPageText.textContent = "Syncing...";
  syncPageBtn.className = "sync-page-btn";

  const result = await chrome.runtime.sendMessage({ type: "sync-current-tab" });

  if (result?.ok) {
    const count = result.data?.length ?? 0;
    syncPageText.textContent = `Synced ${count} package${count === 1 ? "" : "s"}`;
    syncPageBtn.classList.add("success");
  } else {
    syncPageText.textContent = result?.error || "Sync failed";
    syncPageBtn.classList.add("error");
  }

  setTimeout(async () => {
    syncPageBtn.className = "sync-page-btn";
    syncPageBtn.disabled = true;
    syncPageText.textContent = "Not on a carrier site";
    await loadCurrentTab();
  }, 3000);
});

// ── Settings ───────────────────────────────────────────────────────

syncInterval.addEventListener("change", () => {
  chrome.runtime.sendMessage({
    type: "update-sync-interval",
    interval: Number(syncInterval.value),
  });
});

// ── Disconnect ─────────────────────────────────────────────────────

disconnect.addEventListener("click", async (e) => {
  e.preventDefault();
  await clearConfig();
  chrome.action.setBadgeText({ text: "" });
  showSetupView();
});

// ── Utilities ──────────────────────────────────────────────────────

function timeAgo(isoString) {
  const diff = Date.now() - new Date(isoString).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function setEmptyMessage(container, text) {
  container.textContent = "";
  const div = document.createElement("div");
  div.className = "empty";
  div.textContent = text;
  container.appendChild(div);
}

// ── Boot ───────────────────────────────────────────────────────────

init();
