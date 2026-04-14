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
 * Request notification permission after a delay.
 * No-op when the Notification API is unavailable or already decided.
 */
export function requestPermission(delayMs: number = 3000): void {
  if (typeof Notification === "undefined") return;
  if (Notification.permission !== "default") return;
  setTimeout(() => Notification.requestPermission(), delayMs);
}

/**
 * Wire up the htmx afterSwap listener that drives push notifications.
 * Returns a cleanup function that removes the listener.
 */
export function initNotifications(): () => void {
  let lastCount = 0;

  requestPermission();

  const handler = (evt: Event): void => {
    const detail = (evt as CustomEvent).detail;
    if (detail?.target?.id !== "notif-badge") return;

    const count = parseBadgeCount(detail.target);
    const badgeEl = detail.target.querySelector("[data-count]");
    const payload = buildPayload(count, lastCount, badgeEl);

    if (payload && Notification.permission === "granted") {
      new Notification(payload.title, {
        body: payload.body,
        icon: payload.icon,
        tag: payload.tag,
      });
    }

    lastCount = count;
  };

  document.addEventListener("htmx:afterSwap", handler);
  return () => document.removeEventListener("htmx:afterSwap", handler);
}

