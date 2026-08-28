/**
 * Web Push notifications — subscribe the browser to background package
 * status alerts (VAPID). Registers the service worker, requests
 * notification permission if not yet decided, and pushes the resulting
 * subscription to the server.
 */

import { getBasePath } from "./version-check";

export function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  const output = new Uint8Array(new ArrayBuffer(rawData.length));
  for (let i = 0; i < rawData.length; i++) output[i] = rawData.charCodeAt(i);
  return output;
}

/** Subscribe this browser to push, unless it already has a subscription. */
export async function subscribeToPush(
  registration: ServiceWorkerRegistration,
): Promise<void> {
  try {
    const existing = await registration.pushManager.getSubscription();
    if (existing) return;

    const res = await fetch(`${getBasePath()}/api/v1/push/vapid-public-key`);
    if (!res.ok) return;
    const { publicKey } = await res.json();
    if (!publicKey) return;

    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    const { endpoint, keys } = subscription.toJSON() as {
      endpoint: string;
      keys: { p256dh: string; auth: string };
    };

    await fetch(`${getBasePath()}/api/v1/push/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint, p256dh: keys.p256dh, auth: keys.auth }),
    });
  } catch (err) {
    console.warn("Push subscription failed:", err);
  }
}

/**
 * Register the service worker and, once notification permission is
 * granted (requesting it if not yet decided), subscribe to push.
 */
export async function initPushNotifications(): Promise<void> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    return;
  }

  const registration = await navigator.serviceWorker.register(`${getBasePath()}/sw.js`);

  let permission = Notification.permission;
  if (permission === "default") {
    permission = await Notification.requestPermission();
  }
  if (permission !== "granted") return;

  await subscribeToPush(registration);
}
