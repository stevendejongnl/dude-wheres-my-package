# Web Push Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add true background push notifications to the PWA so the user's phone receives delivery alerts even when the app is closed.

**Architecture:** A `WebPushNotifier` service wraps `pywebpush` and stores subscriptions in a new `push_subscriptions` SQLite table. When `_update_package_status` detects a status change, it calls `notifier.send_all()`, which pushes to all stored browser subscriptions via the VAPID Web Push protocol. The frontend service worker receives the `push` event and fires `showNotification()`.

**Tech Stack:** `pywebpush` (server-side push + VAPID), `py_vapid` (key generation, bundled with pywebpush), aiosqlite (existing), TypeScript (existing notifications.ts), vanilla JS service worker.

---

## Task 0: Fix broken tests (prerequisite)

**Files:**
- Modify: `src/dwmp/api/views_test.py:474-489`
- Modify: `src/dwmp/carriers/amazon_test.py:128-146`

- [ ] **Step 1: Fix `test_enrich_package_single_eta_uses_display_format`**

The test hardcodes `2026-05-04` which is no longer today. Replace with a dynamically computed timestamp so the "Today" branch is always exercised. Note: `datetime` is not yet imported in this test file — add it.

Replace lines 474–489 in `src/dwmp/api/views_test.py`:

```python
def test_enrich_package_single_eta_uses_display_format():
    from datetime import UTC, datetime

    tz = ZoneInfo("Europe/Amsterdam")
    original_tz = _views_module._DISPLAY_TZ
    _views_module._DISPLAY_TZ = tz
    try:
        now_utc = datetime.now(UTC).replace(second=0, microsecond=0)
        pkg = {
            "carrier": "postnl",
            "tracking_number": "3STEST",
            "tracking_url": None,
            "estimated_delivery": now_utc.isoformat(),
            "events": [],
        }
        _enrich_package(pkg)
        expected_hm = now_utc.astimezone(tz).strftime("%H:%M")
        assert pkg["estimated_delivery_hm"] == expected_hm
        assert pkg["estimated_delivery_display"] == f"Today {expected_hm}"
    finally:
        _views_module._DISPLAY_TZ = original_tz
```

- [ ] **Step 2: Fix `test_parse_orders_page_delivered`**

The fixture date "8 apr." is now >30 days ago and is filtered by the default lookback cutoff. The test verifies DELIVERED status parsing, not the cutoff filter — pass `lookback_days=365` explicitly.

Change line 142 in `src/dwmp/carriers/amazon_test.py` from:
```python
    results = carrier._parse_orders_page(html)
```
to:
```python
    results = carrier._parse_orders_page(html, lookback_days=365)
```

- [ ] **Step 3: Run tests and confirm both pass**

```bash
uv run pytest src/dwmp/api/views_test.py::test_enrich_package_single_eta_uses_display_format src/dwmp/carriers/amazon_test.py::test_parse_orders_page_delivered -v
```

Expected: both PASS.

- [ ] **Step 4: Commit**

```bash
git add src/dwmp/api/views_test.py src/dwmp/carriers/amazon_test.py
git commit -m "fix(tests): compute date dynamically in eta display test; widen lookback in delivered test"
```

---

## Task 1: Add `push_subscriptions` table and repository methods

**Files:**
- Modify: `src/dwmp/storage/repository.py`
- Test: `src/dwmp/storage/repository_test.py`

- [ ] **Step 1: Write failing tests**

Open `src/dwmp/storage/repository_test.py` and add these tests at the end of the file:

```python
@pytest.mark.asyncio
async def test_push_subscription_add_and_list(tmp_path):
    repo = PackageRepository(tmp_path / "test.db")
    await repo.init()

    await repo.add_push_subscription(
        endpoint="https://push.example.com/v1/abc",
        p256dh="dGVzdGtleQ==",
        auth="dGVzdGF1dGg=",
    )
    subs = await repo.get_all_push_subscriptions()
    assert len(subs) == 1
    assert subs[0]["endpoint"] == "https://push.example.com/v1/abc"
    assert subs[0]["p256dh"] == "dGVzdGtleQ=="
    assert subs[0]["auth"] == "dGVzdGF1dGg="
    await repo.close()


@pytest.mark.asyncio
async def test_push_subscription_upsert(tmp_path):
    repo = PackageRepository(tmp_path / "test.db")
    await repo.init()

    await repo.add_push_subscription("https://push.example.com/v1/abc", "key1", "auth1")
    await repo.add_push_subscription("https://push.example.com/v1/abc", "key2", "auth2")
    subs = await repo.get_all_push_subscriptions()
    assert len(subs) == 1
    assert subs[0]["p256dh"] == "key2"
    await repo.close()


@pytest.mark.asyncio
async def test_push_subscription_remove(tmp_path):
    repo = PackageRepository(tmp_path / "test.db")
    await repo.init()

    await repo.add_push_subscription("https://push.example.com/v1/abc", "key", "auth")
    await repo.remove_push_subscription("https://push.example.com/v1/abc")
    subs = await repo.get_all_push_subscriptions()
    assert subs == []
    await repo.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest src/dwmp/storage/repository_test.py::test_push_subscription_add_and_list -v
```

Expected: FAIL with `AttributeError: 'PackageRepository' object has no attribute 'add_push_subscription'`.

- [ ] **Step 3: Add the DB table to the SCHEMA string**

In `src/dwmp/storage/repository.py`, append to the `SCHEMA` string (before the closing `"""`):

```python
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint   TEXT NOT NULL UNIQUE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Add the three repository methods**

In `src/dwmp/storage/repository.py`, add these methods to the `PackageRepository` class (after the notification methods, before the end of the class):

```python
    # --- Push subscription methods ---

    async def add_push_subscription(
        self, endpoint: str, p256dh: str, auth: str
    ) -> None:
        await self.db.execute(
            """INSERT INTO push_subscriptions (endpoint, p256dh, auth)
               VALUES (?, ?, ?)
               ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth""",
            (endpoint, p256dh, auth),
        )
        await self.db.commit()

    async def remove_push_subscription(self, endpoint: str) -> None:
        await self.db.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        )
        await self.db.commit()

    async def get_all_push_subscriptions(self) -> list[dict]:
        cursor = await self.db.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 5: Run tests and confirm they pass**

```bash
uv run pytest src/dwmp/storage/repository_test.py::test_push_subscription_add_and_list src/dwmp/storage/repository_test.py::test_push_subscription_upsert src/dwmp/storage/repository_test.py::test_push_subscription_remove -v
```

Expected: all PASS.

- [ ] **Step 6: Run the full test suite to check for regressions**

```bash
uv run pytest src/ -v
```

Expected: all existing tests pass (plus 3 new ones).

- [ ] **Step 7: Commit**

```bash
git add src/dwmp/storage/repository.py src/dwmp/storage/repository_test.py
git commit -m "feat(db): add push_subscriptions table and repository methods"
```

---

## Task 2: Install pywebpush and write `WebPushNotifier`

**Files:**
- Create: `src/dwmp/services/web_push_notifier.py`
- Create: `src/dwmp/services/web_push_notifier_test.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Install pywebpush**

```bash
uv add pywebpush
```

Verify `pyproject.toml` now lists `pywebpush` under `dependencies`.

- [ ] **Step 2: Write failing tests**

Create `src/dwmp/services/web_push_notifier_test.py`:

```python
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dwmp.services.web_push_notifier import WebPushNotifier
from dwmp.storage.repository import PackageRepository


def _make_notifier(tmp_path, monkeypatch):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "fake-private")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "fake-public")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:test@example.com")
    repo = MagicMock(spec=PackageRepository)
    return WebPushNotifier(repository=repo)


def test_enabled_when_vapid_keys_set(tmp_path, monkeypatch):
    notifier = _make_notifier(tmp_path, monkeypatch)
    assert notifier.enabled is True


def test_disabled_when_vapid_keys_missing():
    notifier = WebPushNotifier(
        repository=MagicMock(spec=PackageRepository),
        vapid_private_key="",
        vapid_public_key="",
        vapid_subject="",
    )
    assert notifier.enabled is False


def test_public_key_property(tmp_path, monkeypatch):
    notifier = _make_notifier(tmp_path, monkeypatch)
    assert notifier.public_key == "fake-public"


@pytest.mark.asyncio
async def test_send_all_calls_webpush_per_subscription(monkeypatch):
    repo = MagicMock(spec=PackageRepository)
    repo.get_all_push_subscriptions = AsyncMock(return_value=[
        {"endpoint": "https://push.example.com/1", "p256dh": "key1", "auth": "auth1"},
        {"endpoint": "https://push.example.com/2", "p256dh": "key2", "auth": "auth2"},
    ])
    repo.remove_push_subscription = AsyncMock()

    notifier = WebPushNotifier(
        repository=repo,
        vapid_private_key="priv",
        vapid_public_key="pub",
        vapid_subject="mailto:test@example.com",
    )

    with patch("dwmp.services.web_push_notifier.asyncio.to_thread") as mock_thread:
        mock_thread.return_value = None
        await notifier.send_all("Title", "Body", "/")

    assert mock_thread.call_count == 2
    repo.remove_push_subscription.assert_not_called()


@pytest.mark.asyncio
async def test_send_all_removes_expired_subscription(monkeypatch):
    from pywebpush import WebPushException

    repo = MagicMock(spec=PackageRepository)
    repo.get_all_push_subscriptions = AsyncMock(return_value=[
        {"endpoint": "https://push.example.com/gone", "p256dh": "key", "auth": "auth"},
    ])
    repo.remove_push_subscription = AsyncMock()

    notifier = WebPushNotifier(
        repository=repo,
        vapid_private_key="priv",
        vapid_public_key="pub",
        vapid_subject="mailto:test@example.com",
    )

    fake_response = MagicMock()
    fake_response.status_code = 410
    exc = WebPushException("Gone", response=fake_response)

    with patch("dwmp.services.web_push_notifier.asyncio.to_thread", side_effect=exc):
        await notifier.send_all("Title", "Body", "/")

    repo.remove_push_subscription.assert_awaited_once_with("https://push.example.com/gone")


@pytest.mark.asyncio
async def test_send_all_no_op_when_disabled():
    repo = MagicMock(spec=PackageRepository)
    repo.get_all_push_subscriptions = AsyncMock(return_value=[])

    notifier = WebPushNotifier(
        repository=repo,
        vapid_private_key="",
        vapid_public_key="",
        vapid_subject="",
    )

    with patch("dwmp.services.web_push_notifier.asyncio.to_thread") as mock_thread:
        await notifier.send_all("Title", "Body", "/")

    mock_thread.assert_not_called()
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest src/dwmp/services/web_push_notifier_test.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'dwmp.services.web_push_notifier'`.

- [ ] **Step 4: Create `web_push_notifier.py`**

Create `src/dwmp/services/web_push_notifier.py`:

```python
"""Web Push notification service for package status change events.

Sends browser push notifications to all stored subscriptions using the
VAPID Web Push protocol (RFC 8030). Silently no-ops when VAPID credentials
are not configured. Automatically removes subscriptions that return 410 Gone
(browser has unsubscribed).

VAPID keys are read from env vars VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, and
VAPID_SUBJECT. Generate them once with:

    uv run python -c "
    from py_vapid import Vapid
    v = Vapid()
    v.generate_keys()
    print('VAPID_PUBLIC_KEY=' + v.public_key_urlsafe)
    print('VAPID_PRIVATE_KEY=' + v.private_key_urlsafe)
    "
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from pywebpush import WebPushException, webpush

from dwmp.storage.repository import PackageRepository

logger = logging.getLogger(__name__)


class WebPushNotifier:
    def __init__(
        self,
        repository: PackageRepository,
        vapid_private_key: str | None = None,
        vapid_public_key: str | None = None,
        vapid_subject: str | None = None,
    ) -> None:
        self._repository = repository
        self._private_key = (
            vapid_private_key
            if vapid_private_key is not None
            else os.environ.get("VAPID_PRIVATE_KEY", "")
        )
        self._public_key = (
            vapid_public_key
            if vapid_public_key is not None
            else os.environ.get("VAPID_PUBLIC_KEY", "")
        )
        self._subject = (
            vapid_subject
            if vapid_subject is not None
            else os.environ.get("VAPID_SUBJECT", "")
        )

    @property
    def enabled(self) -> bool:
        return bool(self._private_key and self._public_key and self._subject)

    @property
    def public_key(self) -> str:
        return self._public_key

    async def add_subscription(
        self, endpoint: str, p256dh: str, auth: str
    ) -> None:
        await self._repository.add_push_subscription(endpoint, p256dh, auth)

    async def remove_subscription(self, endpoint: str) -> None:
        await self._repository.remove_push_subscription(endpoint)

    async def send_all(self, title: str, body: str, url: str) -> None:
        if not self.enabled:
            return
        subscriptions = await self._repository.get_all_push_subscriptions()
        payload = json.dumps({"title": title, "body": body, "url": url})
        expired: list[str] = []
        for sub in subscriptions:
            subscription_info = {
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            }
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=self._private_key,
                    vapid_claims={"sub": self._subject},
                )
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code == 410:
                    expired.append(sub["endpoint"])
                else:
                    logger.warning("Web push failed for %s: %s", sub["endpoint"][:40], exc)
            except Exception as exc:
                logger.warning("Unexpected push error for %s: %s", sub["endpoint"][:40], exc)
        for endpoint in expired:
            await self._repository.remove_push_subscription(endpoint)
```

- [ ] **Step 5: Run tests and confirm they pass**

```bash
uv run pytest src/dwmp/services/web_push_notifier_test.py -v
```

Expected: all PASS.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest src/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/dwmp/services/web_push_notifier.py src/dwmp/services/web_push_notifier_test.py
git commit -m "feat(push): add WebPushNotifier service with pywebpush"
```

---

## Task 3: Wire `WebPushNotifier` into `TrackingService`

**Files:**
- Modify: `src/dwmp/services/tracking.py`
- Modify: `src/dwmp/api/dependencies.py`
- Modify: `src/dwmp/api/app.py`

No new test file — existing tracking_service tests continue to pass by injecting `notifier=None`.

- [ ] **Step 1: Add `notifier` parameter to `TrackingService.__init__`**

In `src/dwmp/services/tracking.py`, change:

```python
from dwmp.storage.repository import PackageRepository
```
to:
```python
from dwmp.services.web_push_notifier import WebPushNotifier
from dwmp.storage.repository import PackageRepository
```

Then change the `__init__` signature from:

```python
    def __init__(
        self,
        repository: PackageRepository,
        carriers: dict[str, CarrierBase],
    ) -> None:
        self._repository = repository
        self._carriers = carriers
```

to:

```python
    def __init__(
        self,
        repository: PackageRepository,
        carriers: dict[str, CarrierBase],
        notifier: WebPushNotifier | None = None,
    ) -> None:
        self._repository = repository
        self._carriers = carriers
        self._notifier = notifier
```

- [ ] **Step 2: Call `send_all` in `_update_package_status`**

In `src/dwmp/services/tracking.py`, change the `_update_package_status` method body from:

```python
        if old_status != new_status:
            await self._repository.add_notification(
                package_id=pkg_id,
                old_status=old_status,
                new_status=new_status,
                tracking_number=tracking_number,
                carrier=carrier,
                label=label,
                description=description,
            )
```

to:

```python
        if old_status != new_status:
            await self._repository.add_notification(
                package_id=pkg_id,
                old_status=old_status,
                new_status=new_status,
                tracking_number=tracking_number,
                carrier=carrier,
                label=label,
                description=description,
            )
            if self._notifier:
                display_carrier = carrier.upper()
                title = f"{display_carrier} — {label or tracking_number}"
                body = description or new_status
                await self._notifier.send_all(title, body, "/")
```

- [ ] **Step 3: Add `WebPushNotifier` to `dependencies.py`**

In `src/dwmp/api/dependencies.py`, add the import and factory:

```python
from dwmp.services.web_push_notifier import WebPushNotifier
```

Add after `get_repository()`:

```python
@lru_cache
def get_web_push_notifier() -> WebPushNotifier:
    return WebPushNotifier(repository=get_repository())
```

Change `get_tracking_service()` to pass the notifier:

```python
@lru_cache
def get_tracking_service() -> TrackingService:
    return TrackingService(
        repository=get_repository(),
        notifier=get_web_push_notifier(),
        carriers={
            "amazon": Amazon(),
            "postnl": PostNL(),
            "dhl": DHL(),
            "dpd": DPD(),
            "gls": GLS(),
            "trunkrs": Trunkrs(),
        },
    )
```

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest src/ -v
```

Expected: all tests pass. (Existing `TrackingService` tests pass because `notifier` defaults to `None` and the `if self._notifier` guard skips the push call.)

- [ ] **Step 5: Commit**

```bash
git add src/dwmp/services/tracking.py src/dwmp/api/dependencies.py
git commit -m "feat(push): wire WebPushNotifier into TrackingService"
```

---

## Task 4: API routes for push subscription management

**Files:**
- Modify: `src/dwmp/api/routes.py`
- Modify: `src/dwmp/api/routes_test.py`

- [ ] **Step 1: Write failing tests**

The test file uses a `client` fixture (lines 93-97) and an `app` fixture that overrides dependencies (lines 81-90). Add a `push_app` fixture and three tests at the end of `src/dwmp/api/routes_test.py`:

```python
# --- Push subscription routes ---

@pytest.fixture
def push_app(repo):
    from dwmp.api.dependencies import get_web_push_notifier
    from dwmp.services.web_push_notifier import WebPushNotifier
    application = create_app()
    service = TrackingService(
        repository=repo,
        carriers={"postnl": StubPostNLCarrier(), "dpd": StubCredCarrier()},
    )
    notifier = WebPushNotifier(
        repository=repo,
        vapid_private_key="fake-priv",
        vapid_public_key="fake-pub",
        vapid_subject="mailto:test@example.com",
    )
    application.dependency_overrides[get_repository] = lambda: repo
    application.dependency_overrides[get_tracking_service] = lambda: service
    application.dependency_overrides[get_web_push_notifier] = lambda: notifier
    return application


@pytest.fixture
async def push_client(push_app):
    transport = ASGITransport(app=push_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_get_vapid_public_key(push_client):
    response = await push_client.get("/api/v1/push/vapid-public-key")
    assert response.status_code == 200
    assert response.json()["publicKey"] == "fake-pub"


async def test_push_subscribe(push_client):
    payload = {
        "endpoint": "https://push.example.com/v1/test",
        "p256dh": "dGVzdGtleQ==",
        "auth": "dGVzdGF1dGg=",
    }
    response = await push_client.post("/api/v1/push/subscribe", json=payload)
    assert response.status_code == 204


async def test_push_unsubscribe(push_client):
    payload = {
        "endpoint": "https://push.example.com/v1/test",
        "p256dh": "dGVzdGtleQ==",
        "auth": "dGVzdGF1dGg=",
    }
    await push_client.post("/api/v1/push/subscribe", json=payload)
    response = await push_client.delete(
        "/api/v1/push/subscribe",
        json={"endpoint": "https://push.example.com/v1/test"},
    )
    assert response.status_code == 204
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest src/dwmp/api/routes_test.py::test_get_vapid_public_key -v
```

Expected: FAIL with 404 or similar.

- [ ] **Step 3: Add request models and routes to `routes.py`**

In `src/dwmp/api/routes.py`, add these imports at the top alongside the existing ones:

```python
from dwmp.api.dependencies import get_web_push_notifier
from dwmp.services.web_push_notifier import WebPushNotifier
```

Add request models alongside the existing models:

```python
class PushSubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


class PushUnsubscribeRequest(BaseModel):
    endpoint: str
```

Add three new routes (place them near the end of `routes.py`, before any catch-all routes):

```python
@router.get("/push/vapid-public-key")
async def get_vapid_public_key(
    notifier: WebPushNotifier = Depends(get_web_push_notifier),
) -> dict:
    return {"publicKey": notifier.public_key}


@router.post("/push/subscribe", status_code=204)
async def push_subscribe(
    body: PushSubscribeRequest,
    notifier: WebPushNotifier = Depends(get_web_push_notifier),
) -> None:
    await notifier.add_subscription(body.endpoint, body.p256dh, body.auth)


@router.delete("/push/subscribe", status_code=204)
async def push_unsubscribe(
    body: PushUnsubscribeRequest,
    notifier: WebPushNotifier = Depends(get_web_push_notifier),
) -> None:
    await notifier.remove_subscription(body.endpoint)
```

- [ ] **Step 4: Run the new tests**

```bash
uv run pytest src/dwmp/api/routes_test.py::test_get_vapid_public_key src/dwmp/api/routes_test.py::test_push_subscribe src/dwmp/api/routes_test.py::test_push_unsubscribe -v
```

Expected: all PASS.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest src/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/dwmp/api/routes.py src/dwmp/api/routes_test.py
git commit -m "feat(push): add /push/vapid-public-key, /push/subscribe, /push/unsubscribe routes"
```

---

## Task 5: Generate VAPID keys and add to environment

**Files:**
- Modify: `.envrc` (gitignored — add VAPID vars for local dev)
- Modify: `kubernetes/deployment.yaml` (add env vars for production)

> **Note:** This task has no automated tests — it is a one-time ops task.

- [ ] **Step 1: Generate VAPID keys**

Run:

```bash
uv run python -c "
from py_vapid import Vapid
v = Vapid()
v.generate_keys()
print('VAPID_PUBLIC_KEY=' + v.public_key_urlsafe)
print('VAPID_PRIVATE_KEY=' + v.private_key_urlsafe)
print('VAPID_SUBJECT=mailto:sdejong@cloudsuite.com')
"
```

Copy the three output lines.

- [ ] **Step 2: Add to local `.envrc`**

Append the three lines to `.envrc` (this file is gitignored).

- [ ] **Step 3: Add to Kubernetes deployment**

In `kubernetes/deployment.yaml`, add the three env vars to the container's `env:` section:

```yaml
- name: VAPID_PUBLIC_KEY
  valueFrom:
    secretKeyRef:
      name: dwmp-secrets
      key: vapid-public-key
- name: VAPID_PRIVATE_KEY
  valueFrom:
    secretKeyRef:
      name: dwmp-secrets
      key: vapid-private-key
- name: VAPID_SUBJECT
  value: "mailto:sdejong@cloudsuite.com"
```

Then create the Kubernetes secret (run once on the cluster):

```bash
kubectl create secret generic dwmp-secrets \
  --from-literal=vapid-public-key='<YOUR_PUBLIC_KEY>' \
  --from-literal=vapid-private-key='<YOUR_PRIVATE_KEY>' \
  --namespace=<your-namespace>
```

- [ ] **Step 4: Verify the API returns the key**

Start the server locally (`uvicorn dwmp.api.app:app --reload`) and check:

```bash
curl http://localhost:8000/api/v1/push/vapid-public-key
```

Expected: `{"publicKey": "<your key>"}`.

- [ ] **Step 5: Commit deployment change**

```bash
git add kubernetes/deployment.yaml
git commit -m "feat(push): add VAPID env vars to k8s deployment"
```

---

## Task 6: Add `manifest.json` and register service worker in `base.html`

**Files:**
- Create: `src/dwmp/static/manifest.json`
- Modify: `src/dwmp/templates/base.html`

- [ ] **Step 1: Create `manifest.json`**

Create `src/dwmp/static/manifest.json`:

```json
{
  "name": "Dude, Where's My Package?",
  "short_name": "DWMP",
  "description": "Package tracking for PostNL, Amazon, DHL, DPD, and more.",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#3d5a80",
  "icons": [
    {
      "src": "/static/icon.png",
      "sizes": "192x192",
      "type": "image/png"
    }
  ]
}
```

- [ ] **Step 2: Find the `<head>` in `base.html` and add manifest + SW registration**

In `src/dwmp/templates/base.html`, find the existing `<meta name="theme-color"` tag and add the manifest link directly after it:

```html
<link rel="manifest" href="/static/manifest.json">
```

Find the closing `</body>` tag and add the SW registration script just before it:

```html
<script>
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/sw.js");
  }
</script>
```

- [ ] **Step 3: Verify manifest is accessible**

Start the server and open:

```
http://localhost:8000/static/manifest.json
```

Expected: the JSON is returned.

Also open Chrome DevTools → Application → Manifest and verify it loads without errors.

- [ ] **Step 4: Commit**

```bash
git add src/dwmp/static/manifest.json src/dwmp/templates/base.html
git commit -m "feat(pwa): add manifest.json and register service worker"
```

---

## Task 7: Create the push-only service worker

**Files:**
- Create: `src/dwmp/static/sw.js`

- [ ] **Step 1: Create `sw.js`**

Create `src/dwmp/static/sw.js`:

```js
self.addEventListener("push", (event) => {
  if (!event.data) return;
  const { title, body, url } = event.data.json();
  event.waitUntil(
    self.registration.showNotification(title, {
      body: body,
      icon: "/static/icon.png",
      badge: "/static/icon.png",
      data: { url: url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((list) => {
        const target = event.notification.data.url;
        const existing = list.find(
          (c) => new URL(c.url).pathname === target && "focus" in c
        );
        return existing ? existing.focus() : clients.openWindow(target);
      })
  );
});
```

- [ ] **Step 2: Verify SW registers in DevTools**

Start the server, open the app in Chrome, go to DevTools → Application → Service Workers. Confirm `sw.js` appears with status "activated and is running".

- [ ] **Step 3: Commit**

```bash
git add src/dwmp/static/sw.js
git commit -m "feat(pwa): add push-only service worker"
```

---

## Task 8: Update `notifications.ts` to subscribe to Web Push

**Files:**
- Modify: `src/dwmp/static/ts/notifications.ts`

- [ ] **Step 1: Read `notifications.ts` and understand the existing permission-request flow**

Open `src/dwmp/static/ts/notifications.ts`. Find the section that calls `Notification.requestPermission()` or checks `Notification.permission`. The new push subscription code goes in the same place — after permission is confirmed `"granted"`.

- [ ] **Step 2: Add the `urlBase64ToUint8Array` helper and push subscription logic**

At the end of `src/dwmp/static/ts/notifications.ts`, add:

```typescript
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

async function subscribeToPush(registration: ServiceWorkerRegistration): Promise<void> {
  try {
    const existing = await registration.pushManager.getSubscription();
    if (existing) return;

    const res = await fetch("/api/v1/push/vapid-public-key");
    if (!res.ok) return;
    const { publicKey } = await res.json();

    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    const { endpoint, keys } = subscription.toJSON() as {
      endpoint: string;
      keys: { p256dh: string; auth: string };
    };

    await fetch("/api/v1/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint, p256dh: keys.p256dh, auth: keys.auth }),
    });
  } catch (err) {
    console.warn("Push subscription failed:", err);
  }
}
```

- [ ] **Step 3: Call `subscribeToPush` after notification permission is granted**

In the same file, find the block where `Notification.permission === "granted"` is handled (or where `requestPermission()` resolves to `"granted"`). Add the call to `subscribeToPush` there:

```typescript
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.ready.then((registration) => {
    subscribeToPush(registration);
  });
}
```

- [ ] **Step 4: Build the TypeScript**

```bash
npm run build
```

Or if there is a separate TypeScript compile step:
```bash
npx tsc --noEmit  # type-check only
```

Check `src/dwmp/static/js/notifications.js` was regenerated without errors.

- [ ] **Step 5: Commit**

```bash
git add src/dwmp/static/ts/notifications.ts src/dwmp/static/js/notifications.js
git commit -m "feat(pwa): subscribe to Web Push after notification permission granted"
```

---

## Task 9: End-to-end verification

- [ ] **Step 1: Run full test suite — final check**

```bash
uv run pytest src/ -v
```

Expected: all tests pass, no failures.

- [ ] **Step 2: Manual smoke test on desktop**

1. Start the server: `uvicorn dwmp.api.app:app --reload`
2. Open `http://localhost:8000` in Chrome.
3. Open DevTools → Application → Push Messaging.
4. Confirm the subscription appeared after you granted notification permission.
5. Use the DevTools "Push" test button to send a test push. Confirm a browser notification fires.

- [ ] **Step 3: Manual smoke test on mobile**

1. Open the app in Safari on iOS (or Chrome on Android).
2. Add to Home Screen.
3. Open the installed PWA.
4. Grant notification permission when prompted.
5. Close the app completely.
6. Trigger a sync (or use DevTools push test via desktop). Confirm a notification appears on the lock screen.

- [ ] **Step 4: Push to remote**

```bash
git pull --rebase && git push
```

---

## Appendix: Test fixture reference (`routes_test.py`)

- `repo` (fixture, lines 73-78): creates a `PackageRepository` backed by a `tmp_path` SQLite DB.
- `app` (fixture, lines 81-90): creates the FastAPI app and overrides `get_repository` + `get_tracking_service`.
- `client` (fixture, lines 93-97): async HTTPX client wrapping `app`.
- Task 4 adds `push_app` / `push_client` fixtures that additionally override `get_web_push_notifier`.
