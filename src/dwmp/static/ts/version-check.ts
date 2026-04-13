/**
 * Auto-reload when a new application version is deployed.
 *
 * Periodically polls /health and compares the reported version
 * against the version baked into the page.  When they differ the
 * page reloads so users always see the latest UI.
 */

const CHECK_INTERVAL_MS = 60_000; // 1 minute

/**
 * Read the reverse-proxy prefix from the <meta name="dwmp-base"> tag injected by base.html.
 * Empty string when no proxy (k8s/direct port) — calls then resolve to absolute paths as before.
 */
export function getBasePath(): string {
  const meta = document.querySelector('meta[name="dwmp-base"]');
  return meta?.getAttribute("content")?.trim() ?? "";
}

/**
 * Read the version string shown in the page's version badge.
 * Returns `null` when the badge element is missing.
 */
export function getPageVersion(): string | null {
  const badge = document.querySelector(".version-badge");
  return badge?.textContent?.trim() ?? null;
}

/**
 * Fetch the running server version from /health.
 * Returns `null` on network errors.
 */
export async function fetchServerVersion(): Promise<string | null> {
  try {
    const res = await fetch(`${getBasePath()}/health`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.version ? `v${data.version}` : null;
  } catch {
    return null;
  }
}

/**
 * Compare two version strings. Returns true when they differ
 * and both are non-null (meaning an update is available).
 */
export function isNewVersion(
  pageVersion: string | null,
  serverVersion: string | null,
): boolean {
  if (!pageVersion || !serverVersion) return false;
  return pageVersion !== serverVersion;
}

/**
 * Start polling for version changes. Reloads the page when a
 * new version is detected.
 */
export function initVersionCheck(): void {
  const pageVersion = getPageVersion();
  if (!pageVersion) return;

  setInterval(async () => {
    const serverVersion = await fetchServerVersion();
    if (isNewVersion(pageVersion, serverVersion)) {
      window.location.reload();
    }
  }, CHECK_INTERVAL_MS);
}
