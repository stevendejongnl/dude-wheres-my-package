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
