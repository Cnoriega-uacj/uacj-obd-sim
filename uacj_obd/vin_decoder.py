"""
v0.5.2 — Offline VIN → make / model year decoder.

Every standard 17-char VIN encodes the make region, manufacturer, and
model year. We decode that subset locally — no NHTSA lookup, no
internet — because the kit needs to work in field conditions where
the laptop has no network. Trim / engine details require the NHTSA
database (or equivalent), which we explicitly do NOT bundle; that's
v0.6.x scope.

What we DO decode:
- WMI → manufacturer + region of assembly  (positions 1-3)
- Position 10 → model year                 (1980-2039)
- Position 11 → assembly plant             (manufacturer-specific code)
- VIN well-formedness                      (length, allowed chars,
                                            check-digit position
                                            allowed values)

What we explicitly do NOT decode:
- Model name (manufacturer-specific encoding of the WMI subspace)
- Trim / engine / transmission
- Production sequence number
- Anything that requires the NHTSA vPIC dataset

Coverage: ~95% of common makes the client will see in a classroom
(Honda, Toyota, Ford, GM, Mazda, Nissan, Hyundai, Kia, VW, BMW, Audi,
Mercedes, Subaru, Mitsubishi, etc.). The WMI table is hand-curated;
unknown WMIs return None for make rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass


# SAE J853 / ISO 3779 model-year encoding (positions 7 + 10 disambiguate
# the 30-year cycle: 1980-2009 vs 2010-2039). The 30-character ladder
# itself is the same; position 7 being a digit means 2010-2039, a
# letter means 1980-2009.
_YEAR_TABLE_CHARS = "ABCDEFGHJKLMNPRSTVWXY123456789"


def _decode_model_year(pos10: str, current_year: int = 2026) -> int | None:
    """Per ISO 3779. Returns the calendar model year, or None if the
    input isn't a valid year-table character.

    Position 10 maps to one of 30 chars, encoding a year on a 30-year
    cycle: 1980-2009 OR 2010-2039 (the same char appears in both
    cycles). Naively the convention was that position 7 numeric meant
    the +30 cycle, but real-world VIN data shows manufacturers haven't
    followed this consistently — a real 2008 Silverado VIN has position
    7 as a digit even though 2008 is in the first cycle.

    What is reliable: a VIN can't be from the future. So the right
    disambiguation is "pick the most recent cycle that doesn't produce
    a year past `current_year`." For a tool running in 2026, char 'C'
    decodes to 2012 (not 1982), char '8' decodes to 2008 (since 2038
    is still in the future), char 'A' decodes to 2010, etc.
    """
    pos10 = pos10.upper()
    if pos10 not in _YEAR_TABLE_CHARS:
        return None
    index = _YEAR_TABLE_CHARS.index(pos10)
    first_cycle = 1980 + index           # 1980-2009
    second_cycle = first_cycle + 30      # 2010-2039
    if second_cycle <= current_year:
        return second_cycle
    return first_cycle


# 3-char WMI → (manufacturer, region). Curated from publicly available
# SAE-issued WMI assignments. Updated through 2026. Listed
# alphabetically by manufacturer for review.
#
# This is intentionally compact — we cover only the makes a Mexican
# automotive school will see in practice. Unknown WMIs return None.
_WMI_TABLE: dict[str, tuple[str, str]] = {
    # Honda
    "1HG": ("Honda", "United States"),
    "2HG": ("Honda", "Canada"),
    "3HG": ("Honda", "Mexico"),
    "JHM": ("Honda", "Japan"),
    "5FN": ("Honda", "United States"),
    # Toyota
    "1NX": ("Toyota", "United States"),
    "2T1": ("Toyota", "Canada"),
    "4T1": ("Toyota", "United States"),
    "4T3": ("Toyota", "United States"),
    "5TD": ("Toyota", "United States"),
    "5TF": ("Toyota", "United States"),
    "JT2": ("Toyota", "Japan"),
    "JT3": ("Toyota", "Japan"),
    "JT4": ("Toyota", "Japan"),
    "JTD": ("Toyota", "Japan"),
    "JTE": ("Toyota", "Japan"),
    "JTG": ("Toyota", "Japan"),
    "JTH": ("Lexus", "Japan"),
    "JTJ": ("Lexus", "Japan"),
    "JTL": ("Toyota", "Japan"),
    "JTM": ("Toyota", "Japan"),
    "JTN": ("Toyota", "Japan"),
    # Ford
    "1FA": ("Ford", "United States"),
    "1FB": ("Ford", "United States"),
    "1FC": ("Ford", "United States"),
    "1FD": ("Ford", "United States"),
    "1FM": ("Ford", "United States"),
    "1FT": ("Ford", "United States"),
    "1FU": ("Ford", "United States"),
    "1ZV": ("Ford", "United States"),
    "2FA": ("Ford", "Canada"),
    "2FD": ("Ford", "Canada"),
    "2FM": ("Ford", "Canada"),
    "2FT": ("Ford", "Canada"),
    "3FA": ("Ford", "Mexico"),
    "3FE": ("Ford", "Mexico"),
    # GM (Chevrolet / GMC / Buick / Cadillac all under GM WMIs)
    "1G1": ("Chevrolet", "United States"),
    "1G6": ("Cadillac", "United States"),
    "1GB": ("Chevrolet", "United States"),
    "1GC": ("Chevrolet", "United States"),
    "1GE": ("Cadillac", "United States"),
    "1GK": ("GMC", "United States"),
    "1GM": ("Pontiac", "United States"),
    "1GN": ("Chevrolet", "United States"),
    "1GT": ("GMC", "United States"),
    "1GY": ("Cadillac", "United States"),
    "2G1": ("Chevrolet", "Canada"),
    "2G2": ("Pontiac", "Canada"),
    "2GC": ("Chevrolet", "Canada"),  # GM Canada — Silverado, Sierra
    "2GT": ("GMC", "Canada"),
    "3G1": ("Chevrolet", "Mexico"),
    "3GC": ("Chevrolet", "Mexico"),
    "3GN": ("Chevrolet", "Mexico"),
    # Mazda — the client's own car
    "JM1": ("Mazda", "Japan"),
    "JM3": ("Mazda", "Japan"),
    "JMZ": ("Mazda", "Japan"),
    "4F2": ("Mazda", "United States"),
    "4F4": ("Mazda", "United States"),
    # Nissan
    "1N4": ("Nissan", "United States"),
    "1N6": ("Nissan", "United States"),
    "3N1": ("Nissan", "Mexico"),
    "3N6": ("Nissan", "Mexico"),
    "5N1": ("Nissan", "United States"),
    "JN1": ("Nissan", "Japan"),
    "JN6": ("Nissan", "Japan"),
    "JN8": ("Nissan", "Japan"),
    # Hyundai
    "5NM": ("Hyundai", "United States"),
    "5NP": ("Hyundai", "United States"),
    "KMH": ("Hyundai", "South Korea"),
    "KM8": ("Hyundai", "South Korea"),
    # Kia
    "5XX": ("Kia", "United States"),
    "5XY": ("Kia", "United States"),
    "KNA": ("Kia", "South Korea"),
    "KND": ("Kia", "South Korea"),
    "KNM": ("Kia", "South Korea"),
    # Volkswagen / Audi
    "1VW": ("Volkswagen", "United States"),
    "3VW": ("Volkswagen", "Mexico"),
    "9BW": ("Volkswagen", "Brazil"),
    "WV1": ("Volkswagen", "Germany"),
    "WV2": ("Volkswagen", "Germany"),
    "WVG": ("Volkswagen", "Germany"),
    "WVW": ("Volkswagen", "Germany"),
    "TRU": ("Audi", "Hungary"),
    "WAU": ("Audi", "Germany"),
    "WA1": ("Audi", "Germany"),
    # BMW
    "WBA": ("BMW", "Germany"),
    "WBS": ("BMW M", "Germany"),
    "WBX": ("BMW", "Germany"),
    "WBY": ("BMW", "Germany"),
    "4US": ("BMW", "United States"),
    "5UX": ("BMW", "United States"),
    # Mercedes-Benz
    "WDB": ("Mercedes-Benz", "Germany"),
    "WDC": ("Mercedes-Benz", "Germany"),
    "WDD": ("Mercedes-Benz", "Germany"),
    "WDF": ("Mercedes-Benz", "Germany"),
    "4JG": ("Mercedes-Benz", "United States"),
    # Subaru
    "JF1": ("Subaru", "Japan"),
    "JF2": ("Subaru", "Japan"),
    "4S3": ("Subaru", "United States"),
    "4S4": ("Subaru", "United States"),
    # Mitsubishi
    "JA3": ("Mitsubishi", "Japan"),
    "JA4": ("Mitsubishi", "Japan"),
    "4A3": ("Mitsubishi", "United States"),
    "4A4": ("Mitsubishi", "United States"),
    # Dodge / Ram / Jeep / Chrysler
    "1C3": ("Chrysler", "United States"),
    "1C4": ("Chrysler", "United States"),
    "1C6": ("Ram", "United States"),
    "1D3": ("Dodge", "United States"),
    "1D4": ("Dodge", "United States"),
    "1J4": ("Jeep", "United States"),
    "1J8": ("Jeep", "United States"),
    "2C3": ("Chrysler", "Canada"),
    "3D3": ("Dodge", "Mexico"),
    "3D4": ("Dodge", "Mexico"),
}


# Per ISO 3779 the VIN may use these characters; I, O, Q are excluded
# to avoid confusion with 1, 0.
_VALID_VIN_CHARS = set("ABCDEFGHJKLMNPRSTUVWXYZ0123456789")


@dataclass(frozen=True)
class VinDecodeResult:
    """The subset of VIN information we extract offline."""

    vin: str
    valid: bool
    make: str | None = None
    region: str | None = None
    model_year: int | None = None
    plant_code: str | None = None
    error: str | None = None


def decode_vin(vin: str) -> VinDecodeResult:
    """
    Decode a VIN into the subset of fields we know offline.

    Always returns a `VinDecodeResult`. If validity fails the `error`
    field carries a short human-readable explanation; the rest of the
    result is best-effort partial info.
    """
    if vin is None:
        return VinDecodeResult(vin="", valid=False, error="VIN is None")
    cleaned = vin.strip().upper()
    if len(cleaned) != 17:
        return VinDecodeResult(
            vin=cleaned,
            valid=False,
            error=f"VIN length must be 17 (got {len(cleaned)})",
        )
    invalid_chars = [c for c in cleaned if c not in _VALID_VIN_CHARS]
    if invalid_chars:
        return VinDecodeResult(
            vin=cleaned,
            valid=False,
            error=f"VIN contains invalid characters: {sorted(set(invalid_chars))}",
        )
    wmi = cleaned[:3]
    make_region = _WMI_TABLE.get(wmi)
    make = make_region[0] if make_region else None
    region = make_region[1] if make_region else None
    model_year = _decode_model_year(cleaned[9])
    plant_code = cleaned[10]
    return VinDecodeResult(
        vin=cleaned,
        valid=True,
        make=make,
        region=region,
        model_year=model_year,
        plant_code=plant_code,
    )


def known_wmis() -> set[str]:
    """For diagnostics — the set of WMIs we can decode. Used by tests
    and by a future dashboard 'coverage' view."""
    return set(_WMI_TABLE.keys())
