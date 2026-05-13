/**
 * Browser push notifications for package status changes.
 *
 * Listens to htmx badge-poll swaps on #notif-badge, compares the new
 * unread count with the previous one, and fires a browser Notification
 * when the count increases.
 */

export interface NotificationPayload {
  title: string;
  body: string;
  icon: string;
  tag: string;
}

/**
 * Read the reverse-proxy prefix from the <meta name="dwmp-base"> tag.
 * Mirrors getBasePath() in version-check.ts — kept local so this module
 * stays standalone-importable.
 */
function getBasePath(): string {
  const meta = document.querySelector('meta[name="dwmp-base"]');
  return meta?.getAttribute("content")?.trim() ?? "";
}

/**
 * Build the notification payload from a count delta and badge metadata.
 * Returns `null` when no notification should be shown.
 */
export function buildPayload(
  newCount: number,
  oldCount: number,
  badgeEl?: Element | null,
): NotificationPayload | null {
  if (newCount <= oldCount || oldCount < 0) return null;

  const diff = newCount - oldCount;
  const ds = badgeEl instanceof HTMLElement ? badgeEl.dataset : {};
  const carrier = ds.carrier ?? "";
  const tracking = ds.tracking ?? "";
  const newStatus = ds.newStatus ?? "";
  const description = ds.description ?? "";
  const label = ds.label ?? "";

  let body: string;
  if (diff === 1 && carrier) {
    const name = label || tracking;
    body = `${carrier.toUpperCase()}: ${name} → ${newStatus}`;
    if (description) body += `\n${description}`;
  } else if (diff > 1) {
    body = `${diff} package updates`;
  } else {
    body = "A package status has changed";
  }

  return {
    title: "Dude, Where's My Package?",
    body,
    icon: `${getBasePath()}/static/icon-64.png`,
    tag: "dwmp-update",
  };
}

/**
 * Parse the unread count from a badge element inside a container.
 * Returns 0 when the element is missing or has no data-count attribute.
 */
export function parseBadgeCount(container: Element): number {
  const el = container.querySelector("[data-count]");
  if (!el) return 0;
  const raw = (el as HTMLElement).dataset.count;
  const n = parseInt(raw ?? "", 10);
  return isNaN(n) ? 0 : n;
}

/**
 * Convert a URL-safe base64 string to a Uint8Array (required by pushManager.subscribe).
 */
export function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return new Uint8Array([...raw].map((c) => c.charCodeAt(0)));
}

/**
 * Subscribe this browser to Web Push and register the subscription with the server.
 * No-op when service workers or push are unsupported.
 */
export async function subscribeToPush(): Promise<void> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;

  try {
    const keyRes = await fetch(`${getBasePath()}/api/v1/push/vapid-public-key`);
    if (!keyRes.ok) return;
    const { publicKey } = await keyRes.json();

    const registration = await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    }

    const { endpoint, keys } = subscription.toJSON() as {
      endpoint: string;
      keys: { p256dh: string; auth: string };
    };
    await fetch(`${getBasePath()}/api/v1/push/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint, p256dh: keys.p256dh, auth: keys.auth }),
    });
  } catch {
    // Non-critical — push is optional
  }
}

/**
 * Show the push-banner if the user hasn't decided on notifications yet.
 * If already granted, silently re-subscribe (handles re-installs / cleared subs).
 * iOS requires Notification.requestPermission() inside a user gesture, so we
 * never auto-prompt — the banner button provides the required gesture context.
 */
export async function requestPermission(): Promise<void> {
  // Notification API absent = regular Safari on iOS (not an installed home-screen app).
  // Show a banner nudging the user to install via Share → Add to Home Screen.
  if (typeof Notification === "undefined") {
    // Only show the install nudge on iOS/iPadOS where the API is gated behind PWA install.
    const isIOS = /iP(hone|ad|od)/.test(navigator.userAgent);
    if (!isIOS) return;
    const banner = document.getElementById("push-banner");
    const msg = document.getElementById("push-banner-msg");
    const btn = document.getElementById("push-banner-btn");
    if (!banner) return;
    if (msg) msg.textContent = "Add to Home Screen to enable push notifications.";
    if (btn) btn.style.display = "none";
    banner.style.display = "flex";
    return;
  }
  if (Notification.permission === "granted") {
    await subscribeToPush();
    return;
  }
  if (Notification.permission !== "default") return;
  const banner = document.getElementById("push-banner");
  if (banner) banner.style.display = "flex";
}

/**
 * Called when the user taps the push-banner enable button.
 * Runs inside a user gesture so iOS allows the permission dialog.
 */
export async function enablePushFromBanner(): Promise<void> {
  const banner = document.getElementById("push-banner");
  if (banner) banner.style.display = "none";
  if (typeof Notification === "undefined") return;
  const result = await Notification.requestPermission();
  if (result === "granted") await subscribeToPush();
}

/**
 * Wire up the htmx afterSwap listener that drives push notifications.
 * Returns a cleanup function that removes the listener.
 */
const STORAGE_KEY = "dwmp-notif-last-count";

export function initNotifications(): () => void {
  const stored = parseInt(localStorage.getItem(STORAGE_KEY) ?? "", 10);
  let lastCount = isNaN(stored) ? 0 : stored;

  requestPermission();

  const handler = (evt: Event): void => {
    const detail = (evt as CustomEvent).detail;
    if (detail?.target?.id !== "notif-badge") return;

    const count = parseBadgeCount(detail.target);
    const badgeEl = detail.target.querySelector("[data-count]");
    const payload = buildPayload(count, lastCount, badgeEl);

    if (payload && Notification.permission === "granted") {
      // Route through the service worker so notifications work on iOS PWA,
      // where new Notification() from the main thread is silently ignored.
      if ("serviceWorker" in navigator) {
        navigator.serviceWorker.ready.then((reg) =>
          reg.showNotification(payload.title, {
            body: payload.body,
            icon: payload.icon,
            tag: payload.tag,
          })
        );
      }
    }

    lastCount = count;
    localStorage.setItem(STORAGE_KEY, String(count));
  };

  document.addEventListener("htmx:afterSwap", handler);
  return () => document.removeEventListener("htmx:afterSwap", handler);
}

