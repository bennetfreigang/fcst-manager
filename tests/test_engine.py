import pytest

from fcst_manager.engine import compute_anchor, forecast, supply_gap
from fcst_manager.model import Branch, Config, Segment
from fcst_manager.periods import Month

from .conftest import make_item

CFG = Config()

REF_1 = dict(  # D228025-100
    historie={"2024_11": 20, "2024_12": 20, "2025_09": 40,
              "2025_10": 40, "2025_11": 20, "2026_03": 40},
    order={"2026_09": 40, "2026_10": 40},
    lt=5,
)
REF_2 = dict(  # 1/136648
    avg_demand=37.861111111111114, moq=50, lt=5,
    historie={"2023_03": 300, "2024_06": 200, "2024_12": 200, "2025_05": 150,
              "2025_07": 150, "2025_08": 150, "2025_12": 150, "2026_05": 150},
    order={"2026_09": 150, "2026_12": 150},
)


# --- Referenzfaelle -------------------------------------------------------


def test_reference_item_without_demand_is_reproduced_exactly(stichtag):
    d = forecast(make_item(**REF_1), stichtag, CFG)
    assert d.segment is Segment.HIGH and d.branch is Branch.HIGH_STANDARD
    assert (d.qty, d.interval, d.anchor.label) == (40, 4, "2027_01")
    assert d.as_series() == {"2027_01": 40, "2027_05": 40, "2027_09": 40, "2028_01": 40}


def test_reference_item_with_demand_is_reproduced_exactly(stichtag):
    d = forecast(make_item(**REF_2), stichtag, CFG)
    assert (d.qty, d.interval, d.anchor.label) == (150, 3, "2027_03")
    assert d.as_series() == {"2027_03": 150, "2027_06": 150, "2027_09": 150, "2027_12": 150}


# --- Anchor ---------------------------------------------------------------


def test_anchor_treats_consecutive_orders_as_one_split_delivery(stichtag):
    """Ohne Cluster-Logik landet der Anchor auf 2027_02 statt der Referenz 2027_01."""
    item = make_item(**REF_1)
    clustered, note = compute_anchor(item, stichtag, 4, CFG)
    plain, _ = compute_anchor(item, stichtag, 4, Config(anchor_on_order_cluster_start=False))
    assert clustered.label == "2027_01" and plain.label == "2027_02"
    assert "Split-Lieferung" in note


def test_anchor_never_precedes_stichtag_plus_lead_time(stichtag):
    item = make_item(historie={"2024_05": 10, "2024_09": 10, "2025_02": 10}, lt=7)
    anchor, source = compute_anchor(item, stichtag, 3, CFG)
    assert anchor == stichtag + 7
    assert "Stichtag" in source


def test_anchor_uses_latest_order_cluster_when_it_is_later(stichtag):
    anchor, source = compute_anchor(make_item(**REF_2), stichtag, 3, CFG)
    assert anchor.label == "2027_03" and "letzte Order" in source


# --- Lieferluecke ---------------------------------------------------------


def test_gap_is_large_beyond_configured_threshold(stichtag):
    item = make_item(historie={"2025_04": 90}, order={"2027_06": 90}, lt=6)
    *_, months, is_large, _ = supply_gap(item, stichtag, CFG)
    assert months == 16 and is_large


def test_gap_exactly_at_threshold_is_still_small(stichtag):
    """large_gap_months=12 ist als 'mehr als' umgesetzt."""
    item = make_item(historie={"2025_04": 90}, order={"2027_05": 90}, lt=3)
    *_, months, is_large, _ = supply_gap(item, stichtag, CFG)
    assert months == 12 and not is_large


def test_gap_in_the_past_produces_a_warning(stichtag):
    item = make_item(historie={"2025_10": 60, "2026_02": 60}, lt=3)
    *_, months, is_large, warnings = supply_gap(item, stichtag, CFG)
    assert months < 0 and not is_large
    assert any("vor dem Stichtag" in w for w in warnings)


def test_order_takes_precedence_over_sale_as_last_commitment(stichtag):
    item = make_item(historie={"2025_10": 60}, order={"2026_11": 60}, lt=2)
    commit, kind, gap, *_ = supply_gap(item, stichtag, CFG)
    assert commit.label == "2026_11" and kind == "bekannte Order" and gap.label == "2027_01"


# --- Aeste des Entscheidungsbaums ----------------------------------------


def test_sleeper_gets_no_forecast(stichtag):
    d = forecast(make_item(historie={"2024_01": 10}), stichtag, CFG)
    assert d.branch is Branch.SLEEPER_NO_FCST and d.fcst == []


def test_mid_runner_with_large_gap_needs_backlog_and_demand(stichtag):
    item = make_item(avg_demand=15, moq=50, lt=6,
                     historie={"2025_04": 90, "2026_01": 90}, order={"2027_06": 90})
    d = forecast(item, stichtag, CFG)
    assert d.branch is Branch.MID_BACKLOG_DEMAND and d.fcst
    assert any("NICHT an Kundendaten validiert" in a for a in d.assumptions)


def test_mid_runner_with_large_gap_and_no_demand_gets_no_forecast(stichtag):
    item = make_item(moq=50, lt=6, historie={"2025_04": 90, "2026_01": 90}, order={"2027_06": 90})
    d = forecast(item, stichtag, CFG)
    assert d.branch is Branch.MID_BACKLOG_DEMAND and d.fcst == []
    assert "kein AVG Demand" in d.branch_reason


def test_mid_runner_with_large_gap_and_no_backlog_gets_no_forecast(stichtag):
    """Grosse Luecke aus einem alten Verkauf heraus, ohne offene Order."""
    item = make_item(avg_demand=15, lt=36, historie={"2025_04": 90, "2026_01": 90})
    d = forecast(item, stichtag, CFG)
    assert d.branch is Branch.MID_BACKLOG_DEMAND and d.fcst == []
    assert "kein offener Backlog" in d.branch_reason


def test_high_runner_with_large_gap_takes_quantity_from_demand(stichtag):
    item = make_item(avg_demand=25, moq=100, lt=9,
                     historie={"2024_02": 200, "2024_09": 200, "2025_04": 200,
                               "2025_11": 200, "2026_04": 200},
                     order={"2027_01": 200})
    d = forecast(item, stichtag, Config(horizon_months=36))
    assert d.branch is Branch.HIGH_DEMAND_GAP
    assert "AVG Demand x T" in d.qty_source
    assert d.qty == pytest.approx(200)          # ceil(25*8/100)*100
    assert d.anchor >= d.gap_date, "erster Termin darf nicht vor dem Lueckenende liegen"


def test_high_runner_with_large_gap_and_no_demand_gets_no_forecast(stichtag):
    item = make_item(moq=100, lt=9,
                     historie={"2024_02": 200, "2024_09": 200, "2025_04": 200,
                               "2025_11": 200, "2026_04": 200},
                     order={"2027_05": 200})
    d = forecast(item, stichtag, CFG)
    assert d.branch is Branch.HIGH_DEMAND_GAP and d.fcst == []
    assert "nicht anwendbar" in d.branch_reason


def test_mid_runner_small_gap_is_flagged_as_unvalidated_assumption(stichtag):
    d = forecast(make_item(avg_demand=20, moq=30,
                           historie={"2025_10": 60, "2026_02": 60}, lt=3), stichtag, CFG)
    assert d.branch is Branch.MID_STANDARD and d.fcst
    assert any("keine Referenzdaten" in a for a in d.assumptions)


# --- Terminreihe ----------------------------------------------------------


def test_months_with_known_order_are_not_duplicated(stichtag):
    """Bei T=1 laeuft die Terminreihe in eine dreimonatige Split-Lieferung hinein."""
    item = make_item(avg_demand=60, lt=0,
                     historie={"2025_01": 60, "2025_07": 60, "2026_01": 60},
                     order={"2026_09": 60, "2026_10": 60, "2026_11": 60})
    d = forecast(item, stichtag, CFG)
    assert d.interval == 1 and d.anchor.label == "2026_10"
    assert [m.label for m in d.skipped_months] == ["2026_10", "2026_11"]
    series = d.as_series()
    assert "2026_10" not in series and "2026_11" not in series
    assert series["2026_12"] == 60


def test_series_stays_inside_the_horizon(stichtag):
    d = forecast(make_item(**REF_1), stichtag, Config(horizon_months=6))
    assert all(p.month <= stichtag + 5 for p in d.fcst)


def test_anchor_beyond_horizon_is_reported_not_silently_empty(stichtag):
    item = make_item(avg_demand=25, moq=100, lt=30,
                     historie={"2024_02": 200, "2024_09": 200, "2025_04": 200},
                     order={"2027_05": 200})
    d = forecast(item, stichtag, CFG)
    assert d.fcst == []
    assert any("nach dem Horizont-Ende" in w for w in d.warnings)


def test_lead_time_zero_with_single_order_terminates(stichtag):
    """Frueher eine Endlosschleife: T fiel auf die LT von 0 zurueck."""
    d = forecast(make_item(lt=0, historie={"2026_01": 5}), stichtag, CFG)
    assert d.interval >= 1 and len(d.fcst) <= CFG.horizon_months


def test_item_without_any_data_yields_no_forecast(stichtag):
    d = forecast(make_item(historie={}, order={}), stichtag, CFG)
    assert d.fcst == [] and d.branch is Branch.SLEEPER_NO_FCST


def test_every_forecast_point_carries_a_reason(stichtag):
    d = forecast(make_item(**REF_2), stichtag, CFG)
    assert all(p.reason for p in d.fcst)
    assert "Anchor" in d.fcst[0].reason and "2xT" in d.fcst[2].reason


def test_explain_covers_the_full_derivation(stichtag):
    text = forecast(make_item(**REF_2), stichtag, CFG).explain()
    for fragment in ("Klassifizierung", "Lieferluecke", "Menge Q", "Intervall T",
                     "Erster Termin", "2027_03"):
        assert fragment in text


def test_invalid_horizon_is_rejected(stichtag):
    with pytest.raises(ValueError):
        forecast(make_item(**REF_1), stichtag, Config(horizon_months=0))
