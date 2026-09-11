import pytest

from fcst_manager.estimators import (
    cluster_events,
    compute_interval,
    compute_quantity,
    implied_demand,
    round_to_moq,
)
from fcst_manager.model import Config, IntervalMethod
from fcst_manager.periods import Month

from .conftest import make_item

CFG = Config()


# --- Menge Q --------------------------------------------------------------


def test_quantity_prefers_moq_multiple_over_more_frequent_non_multiple():
    item = make_item(moq=50, historie={"2025_01": 30, "2025_04": 30, "2025_07": 30, "2025_10": 100})
    qty, source, _ = compute_quantity(item, CFG)
    assert qty == 100
    assert "MOQ-Vielfachen" in source


def test_quantity_tie_break_picks_larger_quantity():
    """So an D228025-100 validiert: 20 und 40 je 3x, der Kunde rechnet mit 40."""
    item = make_item(
        historie={"2024_11": 20, "2024_12": 20, "2025_11": 20,
                  "2025_09": 40, "2025_10": 40, "2026_03": 40}
    )
    qty, source, _ = compute_quantity(item, CFG)
    assert qty == 40
    assert "Gleichstand" in source


def test_quantity_warns_when_no_history_quantity_matches_moq():
    item = make_item(moq=50, historie={"2025_01": 30, "2025_06": 30})
    qty, _, warnings = compute_quantity(item, CFG)
    assert qty == 30
    assert any("Vielfaches der MOQ" in w for w in warnings)


def test_quantity_reports_negative_history_instead_of_swallowing_it():
    item = make_item(historie={"2025_03": -30, "2025_06": 30, "2025_11": 30})
    qty, _, warnings = compute_quantity(item, CFG)
    assert qty == 30
    assert any("negative Historie-Mengen" in w for w in warnings)


def test_quantity_falls_back_to_moq_without_history():
    qty, source, _ = compute_quantity(make_item(moq=50), CFG)
    assert qty == 50 and "MOQ als Ersatzmenge" in source


def test_quantity_is_none_without_history_and_without_moq():
    qty, source, _ = compute_quantity(make_item(), CFG)
    assert qty is None and "unbestimmbar" in source


def test_moq_multiple_check_tolerates_float_noise():
    item = make_item(moq=0.1, historie={"2025_01": 0.3, "2025_06": 0.3})
    qty, source, _ = compute_quantity(item, CFG)
    assert qty == pytest.approx(0.3)
    assert "MOQ-Vielfachen" in source


@pytest.mark.parametrize(
    "qty,moq,expected", [(107.5, 50, 150), (100, 50, 100), (0.1, 50, 50), (37.4, None, 37)]
)
def test_round_to_moq_never_falls_below_one_lot(qty, moq, expected):
    assert round_to_moq(qty, moq) == expected


# --- Split-Lieferungen ----------------------------------------------------


def test_cluster_events_groups_consecutive_months():
    months = [Month.parse(m) for m in ("2024_11", "2024_12", "2025_09", "2025_10", "2025_11", "2026_03")]
    groups = cluster_events(months, max_gap=1)
    assert [len(g) for g in groups] == [2, 3, 1]
    assert [g[0].label for g in groups] == ["2024_11", "2025_09", "2026_03"]


def test_cluster_events_handles_empty_input():
    assert cluster_events([], max_gap=1) == []


# --- Intervall T ----------------------------------------------------------


def test_interval_uses_floor_of_quantity_over_demand(stichtag):
    item = make_item(avg_demand=37.861111111111114, moq=50)
    t, source, demand, _ = compute_interval(item, 150, stichtag, CFG)
    assert t == 3, "floor(3.96) muss konservativ auf 3 abrunden"
    assert "floor(Q/AvgDemand)" in source and demand == item.avg_demand


def test_implied_demand_fallback_reproduces_customer_interval(stichtag):
    """D228025-100: der Kunde rechnet mit T=4, der Default-Fallback trifft das."""
    item = make_item(
        historie={"2024_11": 20, "2024_12": 20, "2025_09": 40,
                  "2025_10": 40, "2025_11": 20, "2026_03": 40}
    )
    t, source, demand, _ = compute_interval(item, 40, stichtag, CFG)
    assert t == 4
    assert demand == pytest.approx(180 / 21)
    assert "impliziter Demand" in source


@pytest.mark.parametrize(
    "method,expected",
    [
        (IntervalMethod.IMPLIED_DEMAND, 4),
        (IntervalMethod.IMPLIED_DEMAND_ORDER_SPAN, 3),
        (IntervalMethod.MEAN_GAPS, 3),
        (IntervalMethod.MEAN_GAPS_FILTERED, 6),
        (IntervalMethod.MEDIAN_CLUSTER_GAPS, 8),
    ],
)
def test_all_interval_methods_stay_selectable(method, expected, stichtag):
    item = make_item(
        historie={"2024_11": 20, "2024_12": 20, "2025_09": 40,
                  "2025_10": 40, "2025_11": 20, "2026_03": 40}
    )
    t, _, _, _ = compute_interval(item, 40, stichtag, Config(interval_method=method))
    assert t == expected


def test_interval_is_never_below_one(stichtag):
    """Ein T von 0 wuerde die FCST-Schleife nicht vorankommen lassen."""
    item = make_item(avg_demand=1000, historie={"2025_01": 10, "2025_06": 10})
    t, _, _, _ = compute_interval(item, 10, stichtag, CFG)
    assert t == 1


def test_interval_is_capped_at_configured_maximum(stichtag):
    item = make_item(avg_demand=0.01, historie={"2025_01": 10})
    t, _, _, warnings = compute_interval(item, 10_000, stichtag, CFG)
    assert t == CFG.max_interval_months
    assert any("gekappt" in w for w in warnings)


def test_interval_is_none_when_nothing_can_be_derived(stichtag):
    item = make_item(historie={})
    t, source, _, _ = compute_interval(item, None, stichtag, CFG)
    assert t is None and "unbestimmbar" in source


def test_implied_demand_guards_against_zero_length_window():
    item = make_item(historie={"2026_08": 40}, hist_window=[Month.parse("2026_08")])
    assert implied_demand(item, Month.parse("2026_08")) == 40.0
