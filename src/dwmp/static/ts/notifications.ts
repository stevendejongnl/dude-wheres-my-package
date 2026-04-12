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
 * Build the notification payload from a count delta.
 * Returns `null` when no notification should be shown.
 */
export function buildPayload(
  newCount: number,
  oldCount: number,
): NotificationPayload | null {
  if (newCount <= oldCount || oldCount < 0) return null;

  const diff = newCount - oldCount;
  return {
    title: "Dude, Where's My Package?",
    body:
      diff === 1
        ? "A package status has changed"
        : `${diff} package updates`,
    icon: "/static/icon-64.png",
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
    const payload = buildPayload(count, lastCount);

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

