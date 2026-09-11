import datetime as dt

import pytest

from fcst_manager.periods import Month, month_range


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026_08", (2026, 8)),
        ("2026-08", (2026, 8)),
        (" 2026_8 ", (2026, 8)),
        (dt.date(2026, 8, 14), (2026, 8)),
        (dt.datetime(2026, 8, 14, 9, 30), (2026, 8)),
    ],
)
def test_parse_accepts_expected_formats(raw, expected):
    m = Month.parse(raw)
    assert (m.year, m.month) == expected


@pytest.mark.parametrize("raw", ["2026_13", "2026_00", "Historie", "", None, 42, "abc_de"])
def test_parse_rejects_garbage_with_value_error(raw):
    with pytest.raises(ValueError):
        Month.parse(raw)


def test_arithmetic_crosses_year_boundary():
    assert (Month.parse("2026_11") + 4).label == "2027_03"
    assert Month.parse("2027_03") - Month.parse("2026_11") == 4
    assert (Month.parse("2027_03") - 4).label == "2026_11"


def test_ordering_and_hashing():
    a, b = Month.parse("2026_08"), Month.parse("2027_03")
    assert a < b and max(a, b) == b
    assert len({a, Month.parse("2026_08")}) == 1


def test_month_range_inclusive_and_empty():
    assert len(month_range(Month.parse("2026_08"), Month.parse("2028_01"))) == 18
    assert month_range(Month.parse("2027_01"), Month.parse("2026_01")) == []
