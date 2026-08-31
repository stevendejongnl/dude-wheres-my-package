from dwmp.carriers.dpd import DPD_PARCELS_URL

_TEMPLATES: dict[str, str] = {
    "dpd": DPD_PARCELS_URL + "?parcelNumber={tn}",
    "ups": "https://www.ups.com/track?loc=en_NL&tracknum={tn}",
}

_DHL_DEEP = "https://my.dhlecommerce.nl/home/tracktrace/{tn}/{postal_code}"
_DHL_ROOT = "https://my.dhlecommerce.nl/"

_TRUNKRS_DEEP = "https://parcel.trunkrs.nl/{tn}/{postal_code}"
_TRUNKRS_ROOT = "https://parcel.trunkrs.nl/"

# GLS retired the old /app/service/open/rstt/{country}/{lang}/{tn} deep link
# (now errors with an "internal system error"). The parcel-tracking widget at
# /GROUP/en/parcel-tracking/ is the current replacement; it reads the tracking
# number from ?match= and, when given, skips its postal-code confirmation step.
_GLS_DEEP = "https://gls-group.com/GROUP/en/parcel-tracking/?match={tn}&postalCode={postal_code}"
_GLS_ROOT = "https://gls-group.com/GROUP/en/parcel-tracking/?match={tn}"


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
            pc = postal_code.replace(" ", "").upper()
            return _DHL_DEEP.format(tn=tracking_number, postal_code=pc)
        return _DHL_ROOT

    if carrier == "trunkrs":
        if postal_code:
            return _TRUNKRS_DEEP.format(tn=tracking_number, postal_code=postal_code.upper())
        return _TRUNKRS_ROOT

    if carrier == "gls":
        if postal_code:
            pc = postal_code.replace(" ", "").upper()
            return _GLS_DEEP.format(tn=tracking_number, postal_code=pc)
        return _GLS_ROOT.format(tn=tracking_number)

    template = _TEMPLATES.get(carrier)
    if template is None:
        return None
    return template.format(tn=tracking_number)
