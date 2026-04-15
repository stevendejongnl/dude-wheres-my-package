// URL hostname -> carrier name mapping.
// Mirrors _URL_CARRIER_MAP in src/dwmp/api/routes.py so the extension can
// detect which carrier the user is browsing without a server round-trip.
export const URL_CARRIER_MAP = [
  ["amazon.nl", "amazon"],
  ["amazon.com", "amazon"],
  ["amazon.de", "amazon"],
  ["dpdgroup.com", "dpd"],
  ["dpd.nl", "dpd"],
  ["postnl.nl", "postnl"],
  ["dhlecommerce.nl", "dhl"],
  ["dhl.nl", "dhl"],
  ["dhl.com", "dhl"],
];

// Carrier -> tracking/orders page URL for auto-sync tab navigation.
export const CARRIER_SYNC_URLS = {
  amazon: "https://www.amazon.nl/gp/your-account/order-history",
  postnl: "https://jouw.postnl.nl/",
  dhl: "https://my.dhlecommerce.nl/",
  // /incoming triggers the full Keycloak login redirect when not
  // authenticated. The bare /my-parcels portal does a silent SSO check
  // (prompt=none) that never shows the login form.
  dpd: "https://www.dpdgroup.com/nl/mydpd/my-parcels/incoming",
};

// URL patterns that indicate a carrier login page (not yet authenticated).
// Used by the service worker to detect when the sync tab landed on a login
// page instead of the parcels page.
export const CARRIER_LOGIN_PATTERNS = {
  dpd: ["login.dpdgroup.com", "auth/realms"],
  amazon: ["/ap/signin", "/ap/mfa"],
};

// Carrier display names for the UI.
export const CARRIER_LABELS = {
  amazon: "Amazon",
  postnl: "PostNL",
  dhl: "DHL",
  dpd: "DPD",
  gls: "GLS",
};

/**
 * Detect carrier from a URL hostname.
 * Returns the carrier name (e.g. "amazon") or null if unrecognised.
 */
export function detectCarrier(url) {
  try {
    const hostname = new URL(url).hostname;
    for (const [domain, carrier] of URL_CARRIER_MAP) {
      if (hostname === domain || hostname.endsWith("." + domain)) {
        return carrier;
      }
    }
  } catch {
    // malformed URL
  }
  return null;
}

/**
 * Compare two semver strings. Returns true if `a` is newer than `b`.
 */
export function isNewerVersion(a, b) {
  const pa = a.split(".").map(Number);
  const pb = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if ((pa[i] || 0) > (pb[i] || 0)) return true;
    if ((pa[i] || 0) < (pb[i] || 0)) return false;
  }
  return false;
}
