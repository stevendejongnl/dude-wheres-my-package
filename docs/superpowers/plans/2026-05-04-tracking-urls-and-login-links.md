# Tracking URLs Everywhere + Extension Login Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a public tracking-page link on every package card header, and add a "Login" link in the Chrome extension popup so users can manually log in before syncing.

**Architecture:** (1) A new pure-Python module `tracking_urls.py` derives a public tracking URL from carrier + tracking number + optional postal code. `_enrich_package()` in `views.py` calls it as a fallback when no DB-stored URL exists, exposing `effective_tracking_url` to the template. The card header gets a small ↗ link that stops click propagation so it doesn't toggle the card body. (2) The extension popup reads `CARRIER_SYNC_URLS` (already imported) and renders a "Login" anchor next to each Sync button — no new data, just surfacing what's there.

**Tech Stack:** Python 3.13, pytest, Jinja2 templates, vanilla JS (Chrome extension MV3)

---

### Task 1: `public_tracking_url()` — new module + tests

**Files:**
- Create: `src/dwmp/carriers/tracking_urls.py`
- Create: `src/dwmp/carriers/tracking_urls_test.py`

- [ ] **Step 1: Write the failing tests**

```python
# src/dwmp/carriers/tracking_urls_test.py
import pytest
from dwmp.carriers.tracking_urls import public_tracking_url


def test_dpd_returns_tracking_url():
    url = public_tracking_url("dpd", "01234567890123456789")
    assert url == "https://tracking.dpd.de/status/nl_NL/parcel/01234567890123456789"


def test_dhl_with_postal_code():
    url = public_tracking_url("dhl", "JD000123456", "1234AB")
    assert url == "https://my.dhlecommerce.nl/receiver/track-and-trace/JD000123456/1234AB"


def test_dhl_without_postal_code_returns_root():
    url = public_tracking_url("dhl", "JD000123456")
    assert url == "https://my.dhlecommerce.nl/"


def test_dhl_without_postal_code_explicit_none():
    url = public_tracking_url("dhl", "JD000123456", None)
    assert url == "https://my.dhlecommerce.nl/"


def test_gls_returns_tracking_url():
    url = public_tracking_url("gls", "123456789")
    assert url == "https://gls-group.com/app/service/open/rstt/NL/nl/123456789"


def test_trunkrs_returns_tracking_url():
    url = public_tracking_url("trunkrs", "TR-ABC123")
    assert url == "https://parcel.trunkrs.nl/TR-ABC123"


def test_amazon_returns_none():
    # Amazon scrapers set tracking_url directly; no fallback template needed
    assert public_tracking_url("amazon", "123-456-789") is None


def test_postnl_returns_none():
    # PostNL scrapers set tracking_url directly
    assert public_tracking_url("postnl", "3SDEVC123456789") is None


def test_browser_returns_none():
    assert public_tracking_url("browser", "anything") is None


def test_unknown_carrier_returns_none():
    assert public_tracking_url("fedex", "123") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/stevendejong/workspace/personal/home-automation/dude-wheres-my-package
uv run pytest src/dwmp/carriers/tracking_urls_test.py -v 2>&1 | tail -20
```

Expected: `ModuleNotFoundError: No module named 'dwmp.carriers.tracking_urls'`

- [ ] **Step 3: Implement the module**

```python
# src/dwmp/carriers/tracking_urls.py
_TEMPLATES: dict[str, str] = {
    "dpd": "https://tracking.dpd.de/status/nl_NL/parcel/{tn}",
    "gls": "https://gls-group.com/app/service/open/rstt/NL/nl/{tn}",
    "trunkrs": "https://parcel.trunkrs.nl/{tn}",
}

_DHL_DEEP = "https://my.dhlecommerce.nl/receiver/track-and-trace/{tn}/{postal_code}"
_DHL_ROOT = "https://my.dhlecommerce.nl/"


def public_tracking_url(
    carrier: str,
    tracking_number: str,
    postal_code: str | None = None,
) -> str | None:
    """Return a public tracking-page URL for the given carrier and tracking number.

    Returns None for carriers whose scraper already provides a richer URL
    (amazon, postnl) and for unknown carriers.
    """
    if carrier == "dhl":
        if postal_code:
            return _DHL_DEEP.format(tn=tracking_number, postal_code=postal_code)
        return _DHL_ROOT

    template = _TEMPLATES.get(carrier)
    if template is None:
        return None
    return template.format(tn=tracking_number)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest src/dwmp/carriers/tracking_urls_test.py -v 2>&1 | tail -20
```

Expected: all 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dwmp/carriers/tracking_urls.py src/dwmp/carriers/tracking_urls_test.py
git commit -m "feat(carriers): add public_tracking_url fallback template helper"
```

---

### Task 2: Expose `effective_tracking_url` in `_enrich_package()`

**Files:**
- Modify: `src/dwmp/api/views.py` (function `_enrich_package`, lines 93–153)
- Modify: `src/dwmp/api/views_test.py` (add test)

- [ ] **Step 1: Write the failing test**

Open `src/dwmp/api/views_test.py` and look for how `_enrich_package` is tested.
Add this test (add the import at the top if not already present):

```python
# in src/dwmp/api/views_test.py
# Add near existing _enrich_package tests, or at end of file.
# Ensure this import is at the top of the file:
#   from dwmp.api.views import _enrich_package

def test_enrich_package_sets_effective_tracking_url_from_db():
    pkg = {
        "carrier": "dpd",
        "tracking_number": "01234567890123456789",
        "tracking_url": "https://example.com/stored",
        "events": [],
    }
    _enrich_package(pkg)
    assert pkg["effective_tracking_url"] == "https://example.com/stored"


def test_enrich_package_falls_back_to_template_when_no_db_url():
    pkg = {
        "carrier": "dpd",
        "tracking_number": "01234567890123456789",
        "tracking_url": None,
        "events": [],
    }
    _enrich_package(pkg)
    assert pkg["effective_tracking_url"] == (
        "https://tracking.dpd.de/status/nl_NL/parcel/01234567890123456789"
    )


def test_enrich_package_effective_url_none_for_unknown_carrier():
    pkg = {
        "carrier": "fedex",
        "tracking_number": "123",
        "tracking_url": None,
        "events": [],
    }
    _enrich_package(pkg)
    assert pkg["effective_tracking_url"] is None


def test_enrich_package_dhl_uses_postal_code():
    pkg = {
        "carrier": "dhl",
        "tracking_number": "JD000123456",
        "tracking_url": None,
        "postal_code": "1234AB",
        "events": [],
    }
    _enrich_package(pkg)
    assert pkg["effective_tracking_url"] == (
        "https://my.dhlecommerce.nl/receiver/track-and-trace/JD000123456/1234AB"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest src/dwmp/api/views_test.py -k "effective_tracking" -v 2>&1 | tail -20
```

Expected: `KeyError: 'effective_tracking_url'` or `AssertionError`

- [ ] **Step 3: Add the import and the field to `_enrich_package`**

In `src/dwmp/api/views.py`, add the import near the top (after existing carrier imports):

```python
from dwmp.carriers.tracking_urls import public_tracking_url
```

Then in `_enrich_package()`, add this line just before `return pkg` (after the
`pkg["last_synced"] = last_synced` assignment at line 152):

```python
    pkg["effective_tracking_url"] = pkg.get("tracking_url") or public_tracking_url(
        pkg.get("carrier", ""),
        pkg.get("tracking_number", ""),
        pkg.get("postal_code"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest src/dwmp/api/views_test.py -k "effective_tracking" -v 2>&1 | tail -20
```

Expected: all 4 new tests PASS. Also run the full views test suite to check for regressions:

```bash
uv run pytest src/dwmp/api/views_test.py -v 2>&1 | tail -30
```

- [ ] **Step 5: Commit**

```bash
git add src/dwmp/api/views.py src/dwmp/api/views_test.py
git commit -m "feat(views): expose effective_tracking_url in _enrich_package"
```

---

### Task 3: Show ↗ link in the package card header

**Files:**
- Modify: `src/dwmp/templates/_package_card.html`
- Modify: `src/dwmp/templates/base.html` (add `.pkg-tracking-link` style)

- [ ] **Step 1: Add the link to the card header**

In `src/dwmp/templates/_package_card.html`, replace lines 15–15 (`pkg-tracking` div):

Old:
```html
        <div class="pkg-tracking">{{ pkg.tracking_number }}</div>
```

New:
```html
        <div class="pkg-tracking">
          {{ pkg.tracking_number }}
          {% if pkg.effective_tracking_url %}
          <a href="{{ pkg.effective_tracking_url }}" target="_blank" rel="noopener"
             class="pkg-tracking-link" title="Open carrier tracking page"
             onclick="event.stopPropagation()">↗</a>
          {% endif %}
        </div>
```

- [ ] **Step 2: Remove the now-redundant tracking URL entry from the `<details>` block**

In `_package_card.html`, remove lines 85–88 entirely:

Old (lines 85–88):
```html
        {% if pkg.tracking_url %}
        <span class="meta-label">Tracking URL</span>
        <span class="meta-value"><a href="{{ pkg.tracking_url }}" target="_blank" rel="noopener">{{ pkg.tracking_url[:60] }}{% if pkg.tracking_url|length > 60 %}…{% endif %}</a></span>
        {% endif %}
```

New: *(delete those 4 lines)*

- [ ] **Step 3: Add `.pkg-tracking-link` style to `base.html`**

In `src/dwmp/templates/base.html`, find line 103:
```css
    .pkg-tracking { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.85rem; }
```

Append on the next line:
```css
    .pkg-tracking-link { margin-left: 0.3em; color: var(--text-dim); text-decoration: none; font-size: 0.8em; vertical-align: middle; }
    .pkg-tracking-link:hover { color: var(--text); }
```

- [ ] **Step 4: Smoke-test manually**

Start the dev server:
```bash
uv run python -m dwmp
```
Open `http://localhost:8000` (or whatever the configured port is). Verify:
- Every package card header shows a small ↗ next to the tracking number (for DPD, DHL, GLS, Trunkrs packages)
- Amazon/PostNL packages show ↗ only if their `tracking_url` DB column is populated (existing behaviour)
- Clicking ↗ opens the carrier page in a new tab without expanding/collapsing the card

- [ ] **Step 5: Commit**

```bash
git add src/dwmp/templates/_package_card.html src/dwmp/templates/base.html
git commit -m "feat(ui): show tracking-page link (↗) on every package card header"
```

---

### Task 4: Chrome extension popup — add "Login" link per account

**Files:**
- Modify: `chrome-extension/popup/popup.js` (function `renderAccountRow`, ~line 148)
- Modify: `chrome-extension/popup/popup.css` (add `.login-link` rule)

- [ ] **Step 1: Add the login link to `renderAccountRow`**

In `chrome-extension/popup/popup.js`, locate the `actions.appendChild(syncBtn)` line
(~line 251, just before `row.appendChild(badge)`). Insert the login link block directly
before that line:

```js
  // Login link: opens the carrier's login (or parcels) page in a new tab.
  // Lets the user manually sign in before triggering sync, without having
  // to remember the carrier URL.
  const syncUrls = CARRIER_SYNC_URLS[carrier];
  if (syncUrls) {
    const loginUrl = syncUrls.login || syncUrls.parcels;
    const loginLink = document.createElement("a");
    loginLink.href = loginUrl;
    loginLink.target = "_blank";
    loginLink.rel = "noopener";
    loginLink.className = "btn btn-ghost login-link";
    loginLink.textContent = "Login";
    loginLink.title = `Open ${label} login page`;
    actions.appendChild(loginLink);
  }
```

Insert **before** `actions.appendChild(syncBtn)` so the order reads:
auto-sync toggle | Login link | Sync button.

- [ ] **Step 2: Add `.login-link` style to `popup.css`**

In `chrome-extension/popup/popup.css`, after the `.btn-ghost:hover` rule (~line 237),
add:

```css
.login-link {
  text-decoration: none;
  display: inline-flex;
  align-items: center;
}
```

- [ ] **Step 3: Verify `CARRIER_SYNC_URLS` is imported in popup.js**

Check the top of `chrome-extension/popup/popup.js` for:
```js
import { ..., CARRIER_SYNC_URLS, ... } from "../lib/carriers.js";
```

If `CARRIER_SYNC_URLS` is missing from the import, add it to the destructured list.

- [ ] **Step 4: Manual extension smoke-test**

1. In Chrome, go to `chrome://extensions/` → "Load unpacked" → select the `chrome-extension/` folder.
2. Open the extension popup.
3. Confirm each account row shows a "Login" button between the toggle and the Sync button.
4. Amazon → clicking "Login" opens Amazon NL sign-in page.
5. DPD → clicking "Login" opens DPD Group login page.
6. PostNL → clicking "Login" opens jouw.postnl.nl/account.
7. DHL → clicking "Login" opens my.dhlecommerce.nl (root, no login URL defined for DHL).

- [ ] **Step 5: Commit**

```bash
git add chrome-extension/popup/popup.js chrome-extension/popup/popup.css
git commit -m "feat(extension): add Login link per account in popup for manual auth before sync"
```

---

### Task 5: Full test suite + GLS/Trunkrs URL verification

**Files:** no new files

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest src/ -v 2>&1 | tail -40
```

Expected: all tests pass, no regressions.

- [ ] **Step 2: Verify GLS and Trunkrs tracking URL templates**

If you have real GLS or Trunkrs packages in the dashboard, click their ↗ links
and confirm the carrier page resolves. If a template is wrong, fix **only** the
URL string in `src/dwmp/carriers/tracking_urls.py` and update the corresponding
test in `src/dwmp/carriers/tracking_urls_test.py`.

Known URLs to verify:
- GLS: `https://gls-group.com/app/service/open/rstt/NL/nl/{tn}` — open a real GLS tracking number and confirm it loads
- Trunkrs: `https://parcel.trunkrs.nl/{tn}` — open a real Trunkrs tracking number and confirm it loads

If either URL is wrong, fix the template string and the test assertion, re-run the tests, and commit the fix with `fix(carriers): correct <carrier> public tracking URL template`.

- [ ] **Step 3: Final commit (if GLS/Trunkrs templates needed correction)**

```bash
git add src/dwmp/carriers/tracking_urls.py src/dwmp/carriers/tracking_urls_test.py
git commit -m "fix(carriers): correct GLS/Trunkrs public tracking URL templates"
```
