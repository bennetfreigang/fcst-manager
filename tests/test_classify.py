from fcst_manager.classify import classify
from fcst_manager.model import Config, Segment
from fcst_manager.periods import Month, month_range

from .conftest import make_item

CFG = Config()


def test_no_order_at_all_is_sleeper(stichtag):
    segment, reason, _ = classify(make_item(), stichtag, CFG)
    assert segment is Segment.SLEEPER
    assert "keine Bestellung" in reason


def test_three_orders_with_three_years_of_data_is_high_runner(stichtag):
    item = make_item(historie={"2025_01": 10, "2025_06": 10, "2026_01": 10})
    segment, reason, _ = classify(item, stichtag, CFG)
    assert segment is Segment.HIGH
    assert "Bestellungen/Quartal" in reason, "weiches Kriterium muss berichtet werden"


def test_quarterly_criterion_is_reported_but_not_enforced(stichtag):
    """Beide Referenz-Items liegen unter 1 Bestellung/Quartal, sind aber High Runner."""
    item = make_item(historie={"2025_01": 10, "2025_06": 10, "2026_01": 10})
    segment, reason, assumptions = classify(item, stichtag, CFG)
    assert segment is Segment.HIGH
    assert "0.21 Bestellungen/Quartal" in reason
    assert any("nicht geprueft" in a for a in assumptions)


def test_many_orders_but_short_history_window_is_not_high_runner(stichtag):
    window = month_range(Month.parse("2025_09"), Month.parse("2026_06"))
    item = make_item(historie={m.label: 10 for m in window}, hist_window=window)
    segment, reason, _ = classify(item, stichtag, CFG)
    assert segment is Segment.MID
    assert "Datenbasis" in reason


def test_single_old_order_without_demand_is_sleeper(stichtag):
    item = make_item(historie={"2024_01": 10})
    segment, _, assumptions = classify(item, stichtag, CFG)
    assert segment is Segment.SLEEPER
    assert any("Sleeper-Abgrenzung" in a for a in assumptions)


def test_single_recent_order_is_mid_runner_not_sleeper(stichtag):
    """Jung genug -> kein Sleeper, obwohl es nur eine Bestellung ist."""
    segment, _, _ = classify(make_item(historie={"2026_03": 10}), stichtag, CFG)
    assert segment is Segment.MID


def test_single_old_order_with_known_demand_is_mid_runner(stichtag):
    """Ein gepflegter Demand-Wert schliesst den Sleeper laut Diagramm aus."""
    item = make_item(avg_demand=12, historie={"2024_01": 10})
    segment, _, _ = classify(item, stichtag, CFG)
    assert segment is Segment.MID


def test_two_orders_are_mid_runner(stichtag):
    segment, _, _ = classify(make_item(historie={"2025_10": 60, "2026_02": 60}), stichtag, CFG)
    assert segment is Segment.MID


def test_thresholds_are_configurable(stichtag):
    item = make_item(historie={"2025_01": 10, "2025_06": 10})
    assert classify(item, stichtag, CFG)[0] is Segment.MID
    relaxed = Config(high_runner_min_orders=2)
    assert classify(item, stichtag, relaxed)[0] is Segment.HIGH
