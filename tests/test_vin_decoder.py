"""
v0.5.2 — Tests for the offline VIN decoder.

Verifies that:
- Real-world VINs across the makes the client will see decode to the
  expected make / model year / region.
- The Mazda3 VIN the client captured during on-site testing decodes
  correctly. (`JM1BL1L72C1627697` → 2012 Mazda from Japan.)
- Invalid VINs (wrong length, invalid characters, None) return a
  clean error rather than crashing or guessing.
- Model year decoding handles the 30-year cycle (position 7 disambiguates
  1980-2009 vs 2010-2039).
- Unknown WMIs return None for make/region rather than incorrect guesses.
"""

from __future__ import annotations

from uacj_obd.vin_decoder import decode_vin, known_wmis


# ---------------------------------------------------------------------------
# Real-world VINs
# ---------------------------------------------------------------------------

def test_client_mazda3_vin_decodes() -> None:
    """The actual VIN from the client's 2012 Mazda3 captured on-site.
    This locks in that the decoder works for the car the client owns."""
    result = decode_vin("JM1BL1L72C1627697")
    assert result.valid is True
    assert result.make == "Mazda"
    assert result.region == "Japan"
    assert result.model_year == 2012  # position 10 = 'C', position 7 = '7' (digit → +30 cycle)


def test_honda_civic_2015_vin_decodes() -> None:
    """The mock-adapter Civic VIN used elsewhere in tests."""
    result = decode_vin("2HGFC2F59FH123456")
    assert result.valid is True
    assert result.make == "Honda"
    assert result.region == "Canada"
    # Position 10 = 'F', position 7 = '5' (digit → 2010-2039 cycle)
    assert result.model_year == 2015


def test_chevrolet_silverado_2008_vin_decodes() -> None:
    """The Silverado VIN used in the on-site smoke test scenarios."""
    result = decode_vin("2GCEC13C081234567")
    assert result.valid is True
    assert result.make == "Chevrolet"
    # Position 7 = '3' (digit → +30) so position 10 '8' → 2008
    assert result.model_year == 2008


def test_ford_f150_vin_decodes_make() -> None:
    result = decode_vin("1FTFW1EF5BFC12345")
    assert result.valid is True
    assert result.make == "Ford"
    assert result.region == "United States"


def test_toyota_camry_japan_vin_decodes_make() -> None:
    result = decode_vin("JTDBE32K530123456")
    assert result.valid is True
    assert result.make == "Toyota"
    assert result.region == "Japan"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_short_vin_is_invalid() -> None:
    result = decode_vin("ABC123")
    assert result.valid is False
    assert "length" in result.error.lower()


def test_long_vin_is_invalid() -> None:
    result = decode_vin("ABCDEFGHJKLMNPRSTUVWXYZ")
    assert result.valid is False
    assert "length" in result.error.lower()


def test_vin_with_letter_i_is_invalid() -> None:
    """I, O, Q are not allowed in VINs (avoid confusion with 1, 0)."""
    result = decode_vin("JM1BL1L72C162769I")
    assert result.valid is False
    assert "invalid characters" in result.error.lower()


def test_vin_with_letter_o_is_invalid() -> None:
    result = decode_vin("JM1BL1L72C162769O")
    assert result.valid is False


def test_vin_with_letter_q_is_invalid() -> None:
    result = decode_vin("JM1BL1L72C162769Q")
    assert result.valid is False


def test_none_vin_handled_cleanly() -> None:
    result = decode_vin(None)  # type: ignore[arg-type]
    assert result.valid is False
    assert result.make is None


def test_empty_vin_handled_cleanly() -> None:
    result = decode_vin("")
    assert result.valid is False


def test_whitespace_only_vin_handled_cleanly() -> None:
    result = decode_vin("   ")
    assert result.valid is False


# ---------------------------------------------------------------------------
# Model year decoding
# ---------------------------------------------------------------------------

def test_position_10_a_decodes_to_2010_not_1980_in_current_era() -> None:
    """v0.5.2: position 10 char 'A' decodes to one of {1980, 2010}.
    The decoder picks the most recent cycle that isn't in the future —
    a tool running in 2026 must NOT report a real car as 1980 just
    because the char also matched that year on the 30-year cycle."""
    result = decode_vin("WBA1A7CA8A0123456")  # synthetic BMW; position 10 = 'A'
    assert result.valid is True
    assert result.model_year == 2010


def test_position_10_digit_8_decodes_to_2008_not_2038() -> None:
    """A 2008 car is valid; a 2038 car is in the future. Decoder must
    pick the past year. This is the Silverado VIN shape — locks in
    the v0.5.2 disambiguation rule."""
    result = decode_vin("2GCEC13C081234567")  # Silverado, position 10 = '8'
    assert result.valid is True
    assert result.model_year == 2008


def test_position_10_invalid_char_returns_none_year() -> None:
    # Position 10 = 'I' is invalid → no year decoded, but VIN itself is
    # already rejected for the bad char.
    result = decode_vin("JM1BL1L72I1627697")
    assert result.valid is False  # invalid char rejection beats year decode


# ---------------------------------------------------------------------------
# Unknown WMI behaviour
# ---------------------------------------------------------------------------

def test_unknown_wmi_returns_none_make_but_vin_still_valid() -> None:
    """We don't guess for unknown manufacturers. VIN structural
    validity stands; make/region are None."""
    result = decode_vin("ZZZ12345678901234")
    assert result.valid is True
    assert result.make is None
    assert result.region is None
    # Year decoding still works regardless of make
    assert result.model_year is not None


def test_known_wmis_coverage_includes_client_cars() -> None:
    """Sanity: the WMIs of cars the client has tested are in the table."""
    wmis = known_wmis()
    assert "JM1" in wmis  # Mazda (client's car)
    assert "2HG" in wmis  # Honda Canada (mock Civic)
    assert "2GC" in wmis  # Chevrolet (smoke-test Silverado)


# ---------------------------------------------------------------------------
# Whitespace tolerance
# ---------------------------------------------------------------------------

def test_lowercase_vin_uppercased() -> None:
    result = decode_vin("jm1bl1l72c1627697")
    assert result.valid is True
    assert result.vin == "JM1BL1L72C1627697"
    assert result.make == "Mazda"


def test_vin_with_surrounding_whitespace_handled() -> None:
    result = decode_vin("  JM1BL1L72C1627697  ")
    assert result.valid is True
    assert result.make == "Mazda"


# ---------------------------------------------------------------------------
# Dashboard endpoint integration
# ---------------------------------------------------------------------------

def test_decode_endpoint_returns_clean_dict_for_real_vin(tmp_path) -> None:
    """v0.5.2: /api/vin/decode answers with the decoded fields shaped
    exactly as the dashboard expects."""
    from fastapi.testclient import TestClient
    from uacj_obd.api import create_app
    client = TestClient(create_app(data_root=tmp_path))
    r = client.get("/api/vin/decode", params={"vin": "JM1BL1L72C1627697"})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["make"] == "Mazda"
    assert body["region"] == "Japan"
    assert body["model_year"] == 2012
    assert body["error"] is None


def test_decode_endpoint_returns_error_on_invalid_vin(tmp_path) -> None:
    from fastapi.testclient import TestClient
    from uacj_obd.api import create_app
    client = TestClient(create_app(data_root=tmp_path))
    r = client.get("/api/vin/decode", params={"vin": "TOO_SHORT"})
    assert r.status_code == 200  # endpoint never errors; returns valid=False
    body = r.json()
    assert body["valid"] is False
    assert "length" in body["error"].lower()


def test_list_vehicles_enriches_with_decoded_fields(tmp_path) -> None:
    """v0.5.2: the existing /api/vehicles endpoint now carries decoded
    make/year/region even when the captured session's metadata never
    had them populated."""
    from fastapi.testclient import TestClient
    from uacj_obd.api import create_app
    app = create_app(data_root=tmp_path)
    client = TestClient(app)
    # Capture with the mock adapter to populate a vehicle row
    r = client.post("/api/sessions/start",
                     json={"adapter": "mock", "duration_s": 0.2,
                            "pids": ["010C"]})
    assert r.status_code == 200
    import time
    deadline = time.time() + 5
    while time.time() < deadline:
        cur = client.get("/api/sessions/current").json()
        if cur is None:
            break
        time.sleep(0.05)
    vehicles = client.get("/api/vehicles").json()
    assert isinstance(vehicles, list)
    if vehicles:  # mock adapter populates one
        v = vehicles[0]
        assert "decoded_make" in v
        assert "decoded_year" in v
        assert "decoded_region" in v
        assert "vin_valid" in v
