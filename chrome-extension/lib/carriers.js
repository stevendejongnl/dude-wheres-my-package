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

// Carrier -> { login, parcels } URLs for auto-sync tab navigation.
//
// `login` is the URL to open *first* when stored credentials are available —
// this guarantees the carrier shows its sign-in form so the extension can
// fill credentials reliably (instead of relying on possibly-stale cookies
// silently passing an SSO check).
//
// `parcels` is the URL we navigate to *after* login completes, to capture
// the actual parcel/orders HTML that DWMP parses.
//
// For carriers without an extension auto-login flow, `login` is omitted and
// the extension just opens `parcels` directly.
export const CARRIER_SYNC_URLS = {
  amazon: {
    // openid.return_to ensures Amazon redirects to the orders page after
    // a successful login instead of the default account dashboard.
    login: "https://www.amazon.nl/ap/signin?openid.return_to=" +
      encodeURIComponent("https://www.amazon.nl/your-orders/orders") +
      "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select" +
      "&openid.assoc_handle=nlflex&openid.mode=checkid_setup" +
      "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select" +
      "&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0",
    parcels: "https://www.amazon.nl/your-orders/orders",
  },
  dpd: {
    // /nl/mydpd/login always redirects through Keycloak with an explicit
    // login prompt (no prompt=none silent SSO).
    login: "https://www.dpdgroup.com/nl/mydpd/login",
    parcels: "https://www.dpdgroup.com/nl/mydpd/my-parcels/incoming",
  },
  postnl: {
    login: "https://jouw.postnl.nl/account",
    parcels: "https://jouw.postnl.nl/",
  },
  dhl: { parcels: "https://my.dhlecommerce.nl/" },
};

// What to wipe before a fresh login per carrier.
//   cookieDomain   — eTLD+1 for chrome.cookies.getAll({ domain }), which
//                    matches all subdomains via cookie-spec domain matching.
//   storageOrigins — explicit origins for chrome.browsingData.remove;
//                    localStorage / sessionStorage / cache can only be
//                    filtered by full origin URL, not by domain suffix.
export const CARRIER_AUTH_CLEAR = {
  amazon: {
    cookieDomain: "amazon.nl",
    storageOrigins: ["https://www.amazon.nl"],
  },
  dpd: {
    cookieDomain: "dpdgroup.com",
    storageOrigins: ["https://www.dpdgroup.com", "https://login.dpdgroup.com"],
  },
  postnl: {
    cookieDomain: "postnl.nl",
    // login.postnl.nl localStorage stores the remembered email address that
    // PostNL pre-fills on the login form — clearing it causes the form to
    // switch to a multi-step flow (email first, then password) which the
    // login script can't handle. Cookies for login.postnl.nl are still wiped
    // by the eTLD+1 cookie sweep above (cookieDomain: "postnl.nl").
    storageOrigins: ["https://jouw.postnl.nl", "https://www.postnl.nl"],
  },
};

// URL patterns that indicate a carrier login page (not yet authenticated).
// Used as a fallback signal alongside DOM detection.
//
// Amazon's auth namespace spans two path prefixes:
//   - /ap/   — the legacy sign-in flow (#ap_email / #ap_password)
//   - /ax/   — the newer "account claim" flow that fully-logged-out users
//              are redirected to (#ap_email_login → password step).
// Both must be treated as login URLs so the extension doesn't mistake them
// for the post-login destination.
export const CARRIER_LOGIN_PATTERNS = {
  dpd: ["login.dpdgroup.com", "auth/realms"],
  amazon: ["/ap/signin", "/ap/mfa", "/ax/"],
  postnl: ["login.postnl.nl"],
};

// Carrier display names for the UI.
export const CARRIER_LABELS = {
  amazon: "Amazon",
  postnl: "PostNL",
  dhl: "DHL",
  dpd: "DPD",
  gls: "GLS",
  trunkrs: "Trunkrs",
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
