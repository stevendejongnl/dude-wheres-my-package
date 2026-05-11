# Web Push Notifications — Design Spec

**Date:** 2026-05-11  
**Status:** Approved

## Problem

The PWA is pinned to the home screen on iOS and Android. Notifications for package status changes only arrive when the user opens the app, because the current system uses HTMX badge polling (which only runs while the page is open). The user wants real-time background notifications — primarily on delivery, but for all status changes.

## Solution

Implement the Web Push API (VAPID + service worker). The server sends push messages via `pywebpush` when a package status changes; the service worker receives the `push` event and calls `showNotification()`, which fires even when the app is closed.

Works on: Android (Chrome, Firefox), iOS 16.4+ (Safari, installed PWA).

---

## Prerequisites: Broken Tests

Two tests are currently broken and must be fixed before any new work is pushed.

### `test_enrich_package_single_eta_uses_display_format` (views_test.py)

**Cause:** Hardcoded date `2026-05-04` is no longer today; `_format_time()` calls `datetime.now()` internally so the "Today" branch is never hit.

**Fix:** Replace the hardcoded ISO string with `datetime.now(UTC).isoformat()` computed inside the test. Compute the expected `HH:MM` by converting `now_utc` to Amsterdam timezone dynamically, so the assertion is always correct regardless of DST.

### `test_parse_orders_page_delivered` (amazon_test.py)

**Cause:** The delivery date "8 apr." is parsed as April 8 of the current year. As of May 11, that is 33 days ago — past the default 30-day `lookback_days` cutoff.

**Fix:** Call `_parse_orders_page(html, lookback_days=365)` in this test. The test verifies DELIVERED status parsing, not the cutoff filter, so a wider window is semantically correct.

---

## Architecture

```
Tracking service detects status change
  → _update_package_status() adds DB notification row
  → WebPushNotifier.send_all(title, body, url)
      → pywebpush sends via browser relay (Google / Apple APNs)
          → Service worker receives `push` event
              → self.registration.showNotification()
                  fires even when app is closed
```

```
base.html registers /static/sw.js
  → SW fetches VAPID public key from GET /api/v1/push/vapid-public-key
  → pushManager.subscribe({ userVisibleOnly: true, applicationServerKey })
  → POST /api/v1/push/subscribe { endpoint, p256dh, auth }
      → stored in push_subscriptions table
```

---

## Backend

### VAPID keys

Generate once with `vapid --gen`. Store as environment variables:

```
VAPID_PRIVATE_KEY=<base64url>
VAPID_PUBLIC_KEY=<base64url>
VAPID_SUBJECT=mailto:sdejong@cloudsuite.com
```

No dedicated config module — follow the existing `os.environ.get()` pattern used by `TelegramNotifier` and `DB_PATH`.

### DB migration (`storage/repository.py`)

Add a new table to the `SCHEMA` string and a corresponding `ALTER`-free `CREATE TABLE IF NOT EXISTS` in `_migrate()`:

```sql
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint  TEXT NOT NULL UNIQUE,
    p256dh    TEXT NOT NULL,
    auth      TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

No foreign key to `accounts` — subscriptions are device-level. This is a single-user personal app.

Add repository methods:
- `add_push_subscription(endpoint, p256dh, auth)` — upserts by endpoint
- `remove_push_subscription(endpoint)` — deletes by endpoint
- `get_all_push_subscriptions()` → list of dicts

### `src/dwmp/services/web_push_notifier.py`

Mirrors `telegram_notifier.py` in structure. Reads VAPID env vars in `__init__`. Key method:

```python
async def send_all(self, title: str, body: str, url: str) -> None:
    """Send push to every stored subscription. Remove 410-Gone subs."""
```

Uses `pywebpush.webpush()` (sync, wrapped in `asyncio.to_thread`). Silently swallows errors (same pattern as Telegram notifier). Logs on failure. Removes subscriptions that return HTTP 410 (browser unsubscribed).

Add `pywebpush` to `pyproject.toml` dependencies.

### API routes (`src/dwmp/api/routes.py`)

Three new routes:

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET` | `/api/v1/push/vapid-public-key` | none | Returns `{ "publicKey": "..." }` |
| `POST` | `/api/v1/push/subscribe` | session | Body: `{ endpoint, p256dh, auth }`. Upserts. Returns 204. |
| `DELETE` | `/api/v1/push/subscribe` | session | Body: `{ endpoint }`. Deletes. Returns 204. |

### Trigger point (`src/dwmp/services/tracking.py`)

In `_update_package_status()`, after the `add_notification()` call, fire push when `old_status != new_status`:

```python
if old_status != new_status:
    await self._repository.add_notification(...)
    await self._web_push_notifier.send_all(
        title=f"{carrier.title()} — {label}",
        body=description or new_status,
        url="/",
    )
```

`WebPushNotifier` is injected into `TrackingService` at construction time (same pattern as the repository). The scheduler creates `TrackingService` in `app.py` — add `WebPushNotifier()` there alongside the existing `TelegramNotifier()`.

---

## Frontend

### `src/dwmp/static/manifest.json` (new)

```json
{
  "name": "Dude, Where's My Package?",
  "short_name": "DWMP",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#3d5a80",
  "icons": [
    { "src": "/static/icon.png", "sizes": "192x192", "type": "image/png" }
  ]
}
```

Served as a static file. Referenced in `base.html`:
```html
<link rel="manifest" href="/static/manifest.json">
```

### `src/dwmp/static/sw.js` (new service worker)

Minimal push-only SW — no fetch interception.

**`push` event:**
```js
self.addEventListener("push", (event) => {
  const { title, body, url } = event.data.json();
  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: "/static/icon.png",
      data: { url },
    })
  );
});
```

**`notificationclick` event:**
```js
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: "window" }).then((list) => {
      const target = event.notification.data.url;
      const existing = list.find((c) => c.url.includes(target) && "focus" in c);
      return existing ? existing.focus() : clients.openWindow(target);
    })
  );
});
```

### `src/dwmp/static/ts/notifications.ts` (update)

After `Notification.permission === "granted"` and service worker registration:

1. `GET /api/v1/push/vapid-public-key` → `vapidPublicKey`
2. `navigator.serviceWorker.ready` → `registration.pushManager.getSubscription()`
3. If no existing subscription: `pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(vapidPublicKey) })`
4. POST subscription to `/api/v1/push/subscribe`

Include `urlBase64ToUint8Array()` helper (standard VAPID key conversion utility, ~10 lines).

### `base.html` (small addition)

Register SW before the notifications script:
```html
<script>
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/sw.js");
  }
</script>
```

---

## Testing

- Unit test `web_push_notifier.py`: mock `webpush()`, verify send_all calls it per subscription, verify 410 removes the subscription.
- Unit tests for new routes: subscribe adds to DB, delete removes, duplicate subscribe upserts.
- Fix existing broken tests (see Prerequisites).
- Manual end-to-end: install PWA on phone → trigger a sync → verify notification arrives on lock screen.

---

## Out of scope

- Per-user / per-device subscription management UI
- Notification history in the SW (beyond what the app drawer already shows)
- Silent background sync via SW (not needed — server-side poller handles sync)
