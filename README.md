# Dude, Where's My Package?

Package tracking service for Dutch carriers. Runs as a container with a REST API and background poller.

**[Website](https://stevendejongnl.github.io/dude-wheres-my-package/)** | **[GitHub](https://github.com/stevendejongnl/dude-wheres-my-package)** | **[Chrome Extension](https://github.com/stevendejongnl/dude-wheres-my-package/releases/latest)**

## Carrier Setup

### Amazon

**Auth type:** `browser_push` — sync runs **only via the DWMP Chrome extension**.

Amazon's bot detection reliably blocks headless logins from server IPs
(CAPTCHA, push-MFA, OTP walls). After trying every server-side workaround we
settled on the only path that actually keeps working: the extension does the
login on your own browser using credentials you've stored on the account, then
pushes the rendered orders HTML back to DWMP via `POST /browser-push`.

**How to connect:**

1. Install the [DWMP Chrome extension](https://github.com/stevendejongnl/dude-wheres-my-package/releases/latest).
2. Open the DWMP web UI → **Accounts** → **Add Amazon**.
3. Enter your Amazon email, password, and (if MFA is enabled) the TOTP
   secret from your authenticator app's setup screen. Click **Save**.
4. Open the extension's popup and click **Sync** — it opens an Amazon tab,
   signs in using the stored credentials, and pushes the orders page back.

DWMP never talks to Amazon itself — there's no Playwright running on the
server, no CAPTCHA to solve, no cookie hijacking. The credentials stored on
the account are only ever read by the extension you installed.

**MFA:** TOTP (authenticator app) is supported. Push / SMS / approval-based
MFA is not — switch to TOTP in your Amazon security settings if your account
uses push-based approval.

### PostNL

**Auth type:** `manual_token` (browser login required)

PostNL uses Akamai Identity Cloud with no public API registration. Tokens must be captured from the browser after logging in.

**Steps:**

1. Open https://jouw.postnl.nl/account/mijn-pakketten in your browser
2. Log in with your PostNL account
3. Open DevTools (F12) > **Application** tab > **Session Storage** > `jouw.postnl.nl`
4. Find the key `akamai:1e450c3d-5bbb-4f34-9264-dd51fa9fd066:oidc-tokens`
5. Copy the `access_token` and `refresh_token` values
6. Connect:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/accounts/token \
  -H "Content-Type: application/json" \
  -d '{
    "carrier": "postnl",
    "access_token": "<paste access_token>",
    "refresh_token": "<paste refresh_token>"
  }'
```

7. Sync your packages:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/accounts/<id>/sync
```

**API:** GraphQL at `jouw.postnl.nl/account/api/graphql` — returns all tracked shipments (sent and received).

**Token lifetime:** Short-lived (~5 min). The refresh token can extend the session. If sync fails with `auth_failed`, repeat the browser login.

### DHL

**Auth type:** `credentials` (email + password)

DHL eCommerce NL (`my.dhlecommerce.nl`) uses cookie-based sessions with email/password login.

**Steps:**

1. Create a Mijn DHL account at https://my.dhlecommerce.nl/account/sign-up (if you don't have one)
2. Connect:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/accounts/credentials \
  -H "Content-Type: application/json" \
  -d '{
    "carrier": "dhl",
    "username": "<your email>",
    "password": "<your password>"
  }'
```

3. Sync your packages:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/accounts/<id>/sync
```

**API:** REST at `my.dhlecommerce.nl/receiver-parcel-api/parcels` — returns incoming and outgoing parcels. The eCommerce account API typically returns only ~2 events per package.

**DHL Unified Tracking API (optional):** If you set the `DHL_API_KEY` environment variable, dwmp uses the [DHL Unified Tracking API](https://developer.dhl.com) to fetch rich event timelines with 6+ events per package (pickup, sorting, customs, delivery attempts, etc.). Register for free at https://developer.dhl.com to get an API key. Without it, the app falls back to Playwright scraping of the eCommerce portal.

### DPD

**Auth type:** `browser_push` — sync runs **only via the DWMP Chrome extension**.

DPD routes logins through Keycloak SSO at `login.dpdgroup.com` and protects
the parcels page with Cloudflare. Every server-side workaround we tried
(headless Playwright, cookie replay, manual HTML paste) eventually breaks —
Cloudflare binds `cf_clearance` to the TLS fingerprint of the issuing browser,
so cookies captured elsewhere don't replay from the DWMP pod's IP.

**How to connect:**

1. Install the [DWMP Chrome extension](https://github.com/stevendejongnl/dude-wheres-my-package/releases/latest).
2. Open the DWMP web UI → **Accounts** → **Add DPD**.
3. Enter your DPD email + password and click **Save**.
4. Open the extension's popup and click **Sync** — it opens a DPD tab,
   signs in through Keycloak as *you*, and pushes the parcels HTML back
   to DWMP via `POST /browser-push`.

DWMP never talks to DPD itself — no Playwright on the server, no Keycloak
dance, no cookie hijacking. The stored credentials are only ever read by
the extension you installed.

**Public tracking** stays server-side for packages whose account is no
longer active: DPD lets guests view tracking details after verifying the
delivery postal code, and DWMP's `track()` path uses a Playwright guest
flow to hit that endpoint. That's why `postal_code` is required on each
DPD package — without it the public-track fallback has nothing to verify.

### GLS

**Auth type:** `manual_token` (no account needed — public tracking)

GLS Netherlands (`gls-info.nl`) provides public parcel tracking. No login required — just the tracking number and postal code.

**Steps:**

Add a GLS parcel directly:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/packages \
  -H "Content-Type: application/json" \
  -d '{
    "tracking_number": "<your GLS tracking number>",
    "carrier": "gls",
    "postal_code": "<delivery postal code>"
  }'
```

**Important:** The `postal_code` field is **required** for GLS — the API won't return tracking data without it.

Refresh to fetch the latest tracking status:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/packages/<id>/refresh
```

**API:** REST at `apm.gls.nl/api/tracktrace/v1` — returns detailed scan events with depot names, timestamps, and delivery confirmation.

**Note:** GLS account sync is not supported. Parcels are tracked individually using the public API.

### Manual Tracking (any carrier)

You can also track individual packages without an account:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/packages \
  -H "Content-Type: application/json" \
  -d '{
    "tracking_number": "3SXXXX123456789",
    "carrier": "postnl",
    "label": "New headphones",
    "postal_code": "1234AB"
  }'
```

## Quick Start

```bash
uv sync --all-extras
uv run pytest
uv run uvicorn dwmp.api.app:app --reload
```

Then visit <http://localhost:8000/> for the web UI (package list, account setup,
notifications), or hit the JSON API directly — see below.

## Development

Tests live next to the code (`*_test.py`) — `pytest` discovers them via the
`tool.pytest.ini_options` in `pyproject.toml`. Run the full suite with:

```bash
uv run pytest src/dwmp
uv run ruff check src/dwmp
```

### Seeding a demo database

`dwmp.testing` is the canonical store for sample accounts, packages, and
notifications — one place every test, Playwright run, or manual QA session can
reach for representative data. It covers every carrier and every notification
variant the UI renders (including the `auth_failed` alert card).

Seed a fresh SQLite DB for local visual testing:

```bash
uv run python -m dwmp.testing --db /tmp/dwmp-demo.db

DB_PATH=/tmp/dwmp-demo.db POLL_INTERVAL_MINUTES=99999 \
    uv run uvicorn dwmp.api.app:app --port 8087
```

Or use it from a test:

```python
from dwmp.testing import seed_all, SAMPLE_NOTIFICATIONS

async def test_something(repo):
    ids = await seed_all(repo)
    assert len(ids["notifications"]) == len(SAMPLE_NOTIFICATIONS)
```

Granular helpers (`seed_accounts`, `seed_packages`, `seed_notifications`) plus
raw `SAMPLE_*` constants are exported from the same module if you need a
tailored subset.

### Landing-page mockups

The mockups iframed from the gh-pages landing site
(`stevendejongnl.github.io/dude-wheres-my-package`) are **generated** from
the real Jinja templates seeded with the fixtures above — the landing
site never carries a hand-maintained copy of the app HTML.

```bash
uv run python -m dwmp.mockups --out /path/to/gh-pages/mockups
```

This writes `dashboard.html`, `notifications.html`, and `timeline.html`
into the target directory. The generator boots the FastAPI app in-process
against a throwaway SQLite DB, seeds via `dwmp.testing.seed_all`, hits
the real routes with an ASGI transport, then post-processes the HTML:

- strips the HTMX CDN script + the browser-push glue (no backend, so
  they'd flap trying to call home);
- pre-opens the notifications drawer with its fetched body for the
  notifications mockup;
- expands the first seeded card's body for the timeline mockup (with
  five synthetic PostNL tracking events);
- repoints `/static/...` asset URLs at the gh-pages-side `assets/`
  folder so the iframes render the icon/favicon offline.

`extension.html` stays hand-crafted for now — the Chrome-extension popup
is client-side rendered (`popup.js` fetches from the live API), which
doesn't slot into this server-side pipeline.

The release workflow (`.github/workflows/release.yml`) runs the generator
against the cloned `gh-pages` checkout on every released version, so the
mockups stay byte-identical to production with zero manual upkeep.

## Web UI

Dwmp ships a server-rendered HTML UI at `/`:

- `/` — package list with the "Track a package" modal, per-package **Refresh** and **Delete** buttons, and collapsible **Details** sections showing postal code, tracking URL, estimated delivery, source, and timestamps
- `/accounts` — connected accounts with inline add/edit/test/sync flows and a delivery postal-code field on every carrier form (enables public-tracking fallback once account sessions expire). Amazon/DPD accounts are extension-driven and show credential fields only — no server-side login button
- `/notifications` — status-change history with unread badge and two card variants: **package updates** (carrier chip, tracking number, the new status as a single pill, the delivery description, and a `was {old status} · {time}` meta line — whole card clicks through to the package list) and **carrier alerts** (red-bordered card with ⚠ icon and an inline **Reconnect →** button that deep-links to the right carrier row on `/accounts`, for when a sync fails with `auth_failed`). Rich browser push notifications mirror the data so the native banner shows carrier, status, and event description.
- `/login` / `/logout` — password gate (only enabled when `PASSWORD_HASH` is set)

Form submissions show a loading overlay (blur + spinner) to prevent double-clicks.

All UI routes sit behind the same auth middleware as the API.

## Authentication

If the `PASSWORD_HASH` environment variable is set, every route except
`/health`, `/login`, `/api/v1/auth/token`, `/static`, `/docs`, `/openapi.json`
and `/redoc` requires auth. If it's unset, the app is open — fine for a
trusted LAN, not fine for the public internet.

Browsers get a cookie session via `/login`. The DWMP Chrome extension
exchanges the password for a long-lived JWT (365-day expiry) and presents
it as a `Bearer` token on every API call. Other API clients do the same:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"password": "<your password>"}'
# → {"token": "eyJhbGciOi..."}
```

Then send `Authorization: Bearer <token>` on subsequent calls.

Generate a `PASSWORD_HASH` with:

```bash
python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('your-password'))"
```

## API

### Auth

```
POST   /api/v1/auth/token             # Exchange password for a JWT Bearer token
```

### Carriers

```
GET    /api/v1/carriers               # List carriers with auth_type and setup hints
```

### Accounts (connect carrier accounts)

```
POST   /api/v1/accounts/test/credentials  # Dry-run credentials without saving
POST   /api/v1/accounts/test/token        # Dry-run manual token without saving
POST   /api/v1/accounts/token             # Connect with manual token (PostNL, DPD)
POST   /api/v1/accounts/credentials       # Connect with email/password (Amazon, DHL)
GET    /api/v1/accounts                   # List connected accounts (tokens stripped)
GET    /api/v1/accounts/{id}              # Account details
DELETE /api/v1/accounts/{id}              # Disconnect account
POST   /api/v1/accounts/{id}/sync         # Force sync packages from account
POST   /api/v1/accounts/{id}/browser-push # Chrome extension: push scraped HTML keyed to one account
POST   /api/v1/browser-push               # Chrome extension: push scraped HTML, carrier auto-detected from URL
```

### Packages

```
GET    /api/v1/packages               # List all packages (from accounts + manual)
POST   /api/v1/packages               # Manually add {tracking_number, carrier, label?, postal_code?}
GET    /api/v1/packages/{id}          # Package details + event history
DELETE /api/v1/packages/{id}          # Stop tracking
POST   /api/v1/packages/{id}/refresh  # Force refresh single package
```

### Notifications

```
GET    /api/v1/notifications              # List notifications (query: limit, offset)
GET    /api/v1/notifications/unread-count # {"count": N}
POST   /api/v1/notifications/{id}/read    # Mark single notification read
POST   /api/v1/notifications/read-all     # Mark everything read
```

### Health

```
GET    /health                        # Health check + version
```

## Docker

```bash
docker build -t dwmp .
docker run -p 8000:8000 -v dwmp-data:/app/data dwmp
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_PATH` | `dwmp.db` | SQLite database file path |
| `POLL_INTERVAL_MINUTES` | `30` | Background polling interval |
| `PASSWORD_HASH` | *(unset)* | Argon2 hash of the login password. Unset → open access. See *Authentication* above for how to generate it. |
| `JWT_SECRET` | *(random)* | HS256 signing secret for session JWTs. Defaults to a per-process random value, so sessions invalidate on every restart unless you pin it. |
| `TZ` | `Europe/Amsterdam` | Display timezone for rendered dates/times in the web UI. |
| `PLAYWRIGHT_BROWSER_CHANNEL` | `chrome` | Playwright browser channel used by the DPD guest-track flow (public tracking fallback). Set empty to use bundled Chromium. |
| `DHL_API_KEY` | *(unset)* | Free API key from [developer.dhl.com](https://developer.dhl.com). Enables rich event timeline for DHL packages (6+ events). Without it, falls back to Playwright scraping. |
| `DWMP_PUBLIC_URL` | *(unset)* | Public-facing URL (e.g. `https://dwmp.madebysteven.nl`). Used by the Chrome extension's service worker when DWMP sits behind a reverse proxy or Cloudflare and its origin differs from the one the extension's popup was opened against. |

## Kubernetes

```bash
kubectl apply -f kubernetes/
```

The app honors an `X-Ingress-Path` header for reverse proxies that strip a URL
prefix (used by the Home Assistant addon's ingress). Without the header the app
behaves as a plain root-mounted service, so direct-port and Kubernetes
deployments need no extra configuration.

## Home Assistant

Two pieces, designed to be installed together:

1. **Run dwmp as an HA addon** — wraps this image, exposes the web UI through
   HA ingress (sidebar tile), persists the database to the addon's `/data`
   volume. Install from
   [`madebysteven-ha-addons`](https://github.com/stevendejongnl/madebysteven-ha-addons):
   add the repo under *Settings → Add-ons → Add-on Store → ⋮ → Repositories*,
   then install **Dude, Where's My Package?** Set a password in the addon
   config and start it.

2. **Add the HA integration** — exposes parcels as `sensor.dwmp_packages`
   plus a Lovelace card and `dwmp_package_status_changed` events for
   automations. Install via HACS:
   add [`stevens-home-assistant-integrations`](https://github.com/stevendejongnl/stevens-home-assistant-integrations)
   as a custom repository (Integration), download, restart HA, then configure
   it against the addon's URL (e.g. `http://local-dwmp:8000`) and the password
   set in step 1.

The addon talks to dwmp over HTTP on its hassio-DNS hostname; the integration
talks to dwmp via the same REST API used by the curl examples above.

## License

MIT
