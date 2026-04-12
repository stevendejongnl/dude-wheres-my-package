# Dude, Where's My Package?

Package tracking service for Dutch carriers. Runs as a container with a REST API and background poller.

## Carrier Setup

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

**Auth type:** `credentials` (email + password)

DPD uses Keycloak SSO at `login.dpdgroup.com`. Server-side rendered — no JSON API, parcels are scraped from the HTML.

**Steps:**

1. Create a myDPD account at https://www.dpdgroup.com/nl/mydpd/login (click "New user? Register!")
2. Connect:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/accounts/credentials \
  -H "Content-Type: application/json" \
  -d '{
    "carrier": "dpd",
    "username": "<your email>",
    "password": "<your password>"
  }'
```

3. Sync your packages:

```bash
curl -X POST https://dwmp.madebysteven.nl/api/v1/accounts/<id>/sync
```

**Note:** DPD has no JSON API — the service logs into myDPD via Keycloak and scrapes the parcels page HTML.

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
POST   /api/v1/accounts/token         # Connect with manual token {carrier, access_token, refresh_token?}
POST   /api/v1/accounts/credentials   # Connect with login {carrier, username, password}
POST   /api/v1/accounts/oauth/start   # Start OAuth flow {carrier, callback_url}
POST   /api/v1/accounts/oauth/callback # Complete OAuth {carrier, code, callback_url}
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

## License

MIT
