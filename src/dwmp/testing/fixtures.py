"""Canonical sample data for tests, fixtures and manual QA seeding.

Centralised so tests and the demo-seed CLI share one source of truth — when
the schema evolves, this file is the only place that needs to change.

Every dict below is a ``**kwargs`` payload for the matching
``PackageRepository`` method (``add_account``, ``add_package``,
``add_notification``). Use the seeder helpers in
:mod:`dwmp.testing.seeders` to write them to a repo.

The set intentionally covers:
    * every supported carrier (amazon / dpd / dhl / postnl) so carrier-chip
      colour variants all render,
    * every notification status variant the UI can display
      (in_transit, out_for_delivery, delivered, failed_attempt, auth_failed),
    * both the **update** and **alert** notification-card variants.

Keep this file free of imports from the rest of the project so it is safe to
reference from top-level ``__init__`` without creating circular-import risk.
"""

from __future__ import annotations

# --- Accounts ---------------------------------------------------------------

SAMPLE_ACCOUNTS: list[dict] = [
    {
        "carrier": "amazon",
        "auth_type": "credentials",
        "tokens": {"access_token": "demo-amazon-token"},
        "username": "dude@example.com",
        "lookback_days": 30,
        "postal_code": "1234AB",
    },
    {
        "carrier": "dpd",
        "auth_type": "credentials",
        "tokens": {"access_token": "demo-dpd-token"},
        "username": "steven@example.com",
        "lookback_days": 30,
        "postal_code": "1234AB",
    },
    {
        "carrier": "postnl",
        "auth_type": "manual_token",
        "tokens": {"access_token": "demo-postnl-token"},
        "username": None,
        "lookback_days": 30,
        "postal_code": "1234AB",
    },
]


# --- Packages ---------------------------------------------------------------

SAMPLE_PACKAGES: list[dict] = [
    {
        "tracking_number": "403-2026068-5498762",
        "carrier": "amazon",
        "label": "Amazon package",
        "postal_code": "1234AB",
    },
    {
        "tracking_number": "3SKABA000000001",
        "carrier": "postnl",
        "label": "Book",
        "postal_code": "1234AB",
    },
    {
        "tracking_number": "00340123450000000012",
        "carrier": "dhl",
        "label": "Keyboard",
        "postal_code": "1234AB",
    },
    {
        "tracking_number": "05096789012345",
        "carrier": "dpd",
        "label": "Stroopwafels",
        "postal_code": "1234AB",
    },
]


# --- Notifications ----------------------------------------------------------

# Covers every notification variant the redesigned card renders.  The final
# entry is the alert variant — ``tracking_number="Account"`` is the sentinel
# the service layer emits when a carrier's sync fails, and the seeder keeps
# ``package_id`` NULL for it regardless of what packages exist.
SAMPLE_NOTIFICATIONS: list[dict] = [
    {
        "tracking_number": "403-2026068-5498762",
        "carrier": "amazon",
        "label": "Amazon package",
        "old_status": "in_transit",
        "new_status": "out_for_delivery",
        "description": "Wordt naar schatting op 16 april bezorgd",
    },
    {
        "tracking_number": "3SKABA000000001",
        "carrier": "postnl",
        "label": "Book",
        "old_status": "out_for_delivery",
        "new_status": "delivered",
        "description": "Delivered at the front door.",
    },
    {
        "tracking_number": "00340123450000000012",
        "carrier": "dhl",
        "label": "Keyboard",
        "old_status": "pre_transit",
        "new_status": "in_transit",
        "description": "Accepted by carrier — in transit to sorting facility.",
    },
    {
        "tracking_number": "05096789012345",
        "carrier": "dpd",
        "label": "Stroopwafels",
        "old_status": "in_transit",
        "new_status": "failed_attempt",
        "description": "Delivery attempted but nobody was home. Re-attempt tomorrow.",
    },
    {
        "tracking_number": "Account",
        "carrier": "dpd",
        "label": None,
        "old_status": "connected",
        "new_status": "auth_failed",
        "description": (
            "Sync failed for dpd. The carrier's API or login flow may have "
            "changed. Try re-authenticating your account."
        ),
    },
]
