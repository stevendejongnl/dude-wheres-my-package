# Dude, Where's My Package?

Package tracking service for Dutch carriers. Runs as a container with a REST API and background poller.

## Carrier Setup

### Amazon

**Auth type:** `credentials` (email + password + optional TOTP)

Amazon.nl uses client-side JavaScript rendering — Playwright automates a headless Chromium to log in and capture the orders page. Cookies are cached between syncs; re-login happens automatically when they expire.

**Steps:**

1. Connect with your Amazon credentials:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/accounts/credentials \
  -H "Content-Type: application/json" \
  -d '{
    "carrier": "amazon",
    "username": "<your Amazon email>",
    "password": "<your Amazon password>"
  }'
```

2. **If you have TOTP MFA enabled** (authenticator app), add your TOTP secret:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/accounts/credentials \
  -H "Content-Type: application/json" \
  -d '{
    "carrier": "amazon",
    "username": "<your Amazon email>",
    "password": "<your Amazon password>",
    "totp_secret": "<your TOTP setup key>"
  }'
```

The TOTP secret is the base32 key you received when setting up your authenticator app (e.g. `JBSWY3DPEHPK3PXP`).

3. Sync your packages:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/accounts/<id>/sync
```

**How it works:** Playwright launches headless Chromium, logs in with your credentials, navigates to the orders page, waits for JavaScript to render, and captures the HTML. The HTML is parsed for order status, dates, and tracking info. Cookies are saved so subsequent syncs skip the login step.

**MFA:** TOTP (authenticator app) is supported. Push notification MFA is not — switch to TOTP in your Amazon security settings if you use push-based approval.

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

**API:** REST at `my.dhlecommerce.nl/receiver-parcel-api/parcels` — returns incoming and outgoing parcels.

### DPD

**Auth type:** `manual_token` (browser-captured HTML)

DPD uses Keycloak SSO at `login.dpdgroup.com` with Cloudflare bot protection on the parcels page. No JSON API — the service parses the server-rendered HTML. Because Cloudflare blocks headless HTTP clients, the parcels page HTML must be captured from a real browser session.

**Steps:**

1. Log in at https://www.dpdgroup.com/nl/mydpd/login
2. Navigate to **My parcels** > **Incoming**
3. Open DevTools (F12) > **Console** and run:
   ```js
   copy(document.documentElement.outerHTML)
   ```
4. Save the clipboard content to a file (e.g. `dpd.html`)
5. Strip the JSON string wrapper if present (the copied text may be wrapped in quotes)
6. Connect — send the HTML as the `access_token`:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/accounts/token \
  -H "Content-Type: application/json" \
  -d "{\"carrier\":\"dpd\",\"access_token\":\"$(cat dpd.html)\"}"
```

7. Sync your packages:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/accounts/<id>/sync
```

**Note:** DPD parcels are scraped from the HTML. The "token" is a snapshot of the parcels page — re-capture it when you need fresh data.

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

## API

### Carriers

```
GET    /api/v1/carriers               # List carriers with auth_type and setup hints
```

### Accounts (connect carrier accounts)

```
POST   /api/v1/accounts/token         # Connect with manual token (PostNL, DPD)
POST   /api/v1/accounts/credentials   # Connect with email/password (Amazon, DHL)
GET    /api/v1/accounts               # List connected accounts (tokens stripped)
GET    /api/v1/accounts/{id}          # Account details
DELETE /api/v1/accounts/{id}          # Disconnect account
POST   /api/v1/accounts/{id}/sync     # Force sync packages from account
```

### Packages

```
GET    /api/v1/packages               # List all packages (from accounts + manual)
POST   /api/v1/packages               # Manually add {tracking_number, carrier, label?, postal_code?}
GET    /api/v1/packages/{id}          # Package details + event history
DELETE /api/v1/packages/{id}          # Stop tracking
POST   /api/v1/packages/{id}/refresh  # Force refresh single package
```

### Health

```
GET    /health                        # Health check
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

## Kubernetes

```bash
kubectl apply -f kubernetes/
```

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
