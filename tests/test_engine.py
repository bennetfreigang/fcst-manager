import pytest

from fcst_manager.engine import compute_anchor, forecast, supply_gap
from fcst_manager.model import Branch, Config, Segment
from fcst_manager.periods import Month, month_range

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
    """Q und T stimmen weiter mit der Referenzdatei ueberein; der Anchor nicht mehr -
    die Datei nennt 2027_03, die vom Kunden bestaetigte Formel (Stichtag+LT, siehe
    compute_anchor) ergibt 2027_01. Laut Kunde koennen die per Hand erstellten
    Referenzwerte selbst fehlerhaft sein; Stichtag+LT gilt als verbindlich."""
    d = forecast(make_item(**REF_2), stichtag, CFG)
    assert (d.qty, d.interval, d.anchor.label) == (150, 3, "2027_01")
    assert d.as_series() == {
        "2027_01": 150, "2027_04": 150, "2027_07": 150, "2027_10": 150, "2028_01": 150,
    }


# --- Anchor ---------------------------------------------------------------


def test_anchor_is_exactly_stichtag_plus_lead_time(stichtag):
    """Verbindliche Formel (vom Kunden explizit bestaetigt, siehe compute_anchor-
    Docstring): Anchor = Stichtag + LT, ohne jeden Bezug zu Order oder Historie."""
    anchor, source = compute_anchor(make_item(**REF_2), stichtag)
    assert anchor == stichtag + 5 == Month.parse("2027_01")
    assert source == f"Stichtag {stichtag} + LT=5"


def test_anchor_ignores_order_and_historie_entirely(stichtag):
    """Regression fuer die alte (verworfene) Order/Cluster-Logik: zwei Items mit
    identischem Stichtag+LT muessen denselben Anchor haben, egal wie Order und
    Historie aussehen."""
    lean = make_item(lt=6, historie={}, order={})
    busy = make_item(lt=6, historie={"2024_02": 200, "2025_11": 40}, order={"2026_09": 999})
    assert compute_anchor(lean, stichtag)[0] == compute_anchor(busy, stichtag)[0] == stichtag + 6


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
    """Anchor (Stichtag+LT=2026_09) faellt in ein dreimonatiges Orderbook-Fenster;
    bei T=1 muss die Terminreihe durch alle drei bereits bekannten Monate laufen,
    bevor der erste echte FCST-Punkt entsteht."""
    item = make_item(avg_demand=60, lt=1,
                     historie={"2025_01": 60, "2025_07": 60, "2026_01": 60},
                     order={"2026_09": 60, "2026_10": 60, "2026_11": 60})
    d = forecast(item, stichtag, CFG)
    assert d.interval == 1 and d.anchor.label == "2026_09"
    assert [m.label for m in d.skipped_months] == ["2026_09", "2026_10", "2026_11"]
    series = d.as_series()
    assert not ({"2026_09", "2026_10", "2026_11"} & series.keys())
    assert series["2026_12"] == 60


def test_series_stays_inside_the_horizon(stichtag):
    d = forecast(make_item(**REF_1), stichtag, Config(horizon_months=6))
    assert all(p.month <= stichtag + 5 for p in d.fcst)


def test_anchor_beyond_horizon_is_still_shown_as_the_next_due_order(stichtag):
    """Ein sehr grosses T (winziger Demand) darf den FCST nicht komplett leeren -
    real beobachtet an Item 1847010008 (T=33, kein sichtbarer Termin mehr)."""
    item = make_item(avg_demand=25, moq=100, lt=30,
                     historie={"2024_02": 200, "2024_09": 200, "2025_04": 200},
                     order={"2027_05": 200})
    d = forecast(item, stichtag, CFG)
    assert len(d.fcst) == 1, "genau der naechste faellige Termin, keine weiteren Wiederholungen"
    assert d.fcst[0].month == d.anchor
    assert "nach Horizont-Ende" in d.fcst[0].reason
    assert any("dennoch angezeigt" in w for w in d.warnings)


def test_guarantee_first_order_can_be_disabled(stichtag):
    """Ueber Config abschaltbar - Verhalten der ersten Iteration bleibt erreichbar."""
    item = make_item(avg_demand=25, moq=100, lt=30,
                     historie={"2024_02": 200, "2024_09": 200, "2025_04": 200},
                     order={"2027_05": 200})
    cfg = Config(guarantee_first_order=False)
    d = forecast(item, stichtag, cfg)
    assert d.fcst == []
    assert d.warnings == [], "ohne Garantie gibt es (wie zuvor) still keinen FCST-Eintrag"


def test_guarantee_first_order_skips_months_already_covered_by_orders(stichtag):
    """Faellt der Anchor (Stichtag+LT) in ein mehrmonatiges Orderbook-Fenster, muss
    die Terminreihe erst durch die bereits bekannten Monate laufen, bevor der
    erste echte FCST-Punkt entsteht."""
    item = make_item(avg_demand=50, lt=1, historie={"2024_02": 50},
                     order={"2026_09": 50, "2026_10": 50})
    d = forecast(item, stichtag, CFG)
    assert d.interval == 1 and d.anchor == Month.parse("2026_09")
    assert d.skipped_months == [Month.parse("2026_09"), Month.parse("2026_10")]
    assert d.fcst[0].month == Month.parse("2026_11")


def test_guarantee_first_order_has_a_safety_limit_against_endless_search(stichtag):
    """Wenn das Orderbook jeden Kandidatenmonat weit ueber den Horizont hinaus belegt,
    bricht die Suche irgendwann ab, statt endlos zu laufen."""
    order = {m.label: 1 for m in month_range(stichtag, stichtag + 500)}
    item = make_item(avg_demand=25, moq=100, lt=0, historie={"2024_02": 200}, order=order)
    d = forecast(item, stichtag, CFG)
    assert d.fcst == []
    assert any("abgebrochen" in w for w in d.warnings)


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
                     "Erster Termin", "2027_01"):
        assert fragment in text


def test_invalid_horizon_is_rejected(stichtag):
    with pytest.raises(ValueError):
        forecast(make_item(**REF_1), stichtag, Config(horizon_months=0))
