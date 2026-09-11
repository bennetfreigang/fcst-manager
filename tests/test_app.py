"""Tests der Streamlit-App: Aufbereitungsfunktionen und der komplette Upload-Pfad."""

import pytest

pytest.importorskip(
    "streamlit",
    reason="App-Tests benoetigen Streamlit: `uv sync --extra app` bzw. die Dev-Gruppe",
)

from fcst_manager.app import (  # noqa: E402
    DISPLAY_WINDOW_MONTHS,
    _fcst_matrix,
    _orders_in_window,
    _overview_frame,
    _timeline_frame,
)
from fcst_manager.engine import forecast
from fcst_manager.excel_io import read_items
from fcst_manager.model import Config, IntervalMethod

from .conftest import DATA, ROOT, make_item

SAMPLE = DATA / "Sammeldatei_Test.xlsx"
APP = ROOT / "src" / "fcst_manager" / "app.py"
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def computed():
    layout, items, manual = read_items(SAMPLE)
    stichtag = layout.stichtag()
    decisions = {i.item_number: forecast(i, stichtag, Config()) for i in items}
    return layout, items, manual, decisions, stichtag


# --- 18-Monats-Sicht ------------------------------------------------------


def test_orders_in_window_counts_only_the_display_window(stichtag):
    item = make_item(historie={"2024_01": 10, "2025_06": 10, "2026_03": 10})
    assert _orders_in_window(item, stichtag) == 2, "2024_01 liegt ausserhalb der 18 Monate"


def test_timeline_hides_older_history_but_keeps_order_and_fcst(stichtag):
    item = make_item(historie={"2024_01": 10, "2025_06": 10, "2026_03": 10},
                     order={"2026_09": 10}, lt=5)
    decision = forecast(item, stichtag, Config())

    short = _timeline_frame(item, decision, stichtag, full=False)
    full = _timeline_frame(item, decision, stichtag, full=True)

    assert "2024_01" not in short.index, "ausserhalb des Fensters -> gar keine Zeile"
    assert full.loc["2024_01", "Historie"] == 10
    assert short.loc["2025_06", "Historie"] == 10, "innerhalb des Fensters -> sichtbar"
    assert short.loc["2026_09", "Order"] == 10, "Order bleibt immer sichtbar"
    assert short["FCST"].sum() == full["FCST"].sum() > 0


def test_display_window_does_not_change_the_calculation(stichtag):
    """Die Umschaltung ist reine Anzeige - Q, T und Anchor bleiben identisch."""
    item = make_item(historie={"2024_01": 10, "2025_06": 10, "2026_03": 10}, lt=5)
    decision = forecast(item, stichtag, Config())
    before = (decision.qty, decision.interval, decision.anchor, decision.as_series())
    for full in (False, True):
        _timeline_frame(item, decision, stichtag, full)
    assert (decision.qty, decision.interval, decision.anchor, decision.as_series()) == before


def test_truncating_history_would_break_classification(stichtag):
    """Begruendung dafuer, dass gerechnet immer auf der vollen Historie wird."""
    from fcst_manager.periods import Month, month_range

    orders = {"2024_08": 10, "2025_01": 10, "2025_06": 10, "2026_01": 10}
    full = make_item(historie=orders)
    short_window = month_range(stichtag - DISPLAY_WINDOW_MONTHS, Month.parse("2026_06"))
    short = make_item(historie=orders, hist_window=short_window)

    assert str(forecast(full, stichtag, Config()).segment) == "High Runner"
    assert str(forecast(short, stichtag, Config()).segment) == "Mid Runner"


# --- Tabellen -------------------------------------------------------------


def test_overview_frame_has_one_row_per_item_and_both_order_counts(computed):
    _, items, _, decisions, stichtag = computed
    frame = _overview_frame(items, decisions, stichtag)
    assert len(frame) == len(items)
    assert f"Bestell. ({DISPLAY_WINDOW_MONTHS} Mon.)" in frame.columns
    assert "Bestell. (gesamt)" in frame.columns
    row = frame.set_index("ItemNumber").loc["D228025-100"]
    assert row["Bestell. (18 Mon.)"] == 4 and row["Bestell. (gesamt)"] == 6


def test_fcst_matrix_matches_the_decisions(computed):
    layout, items, _, decisions, stichtag = computed
    cfg = Config(horizon_months=len(layout.fcst))
    matrix = _fcst_matrix(items, decisions, stichtag, cfg)
    # Alle Datei-eigenen FCST-Spalten muessen enthalten sein; zusaetzliche Spalten
    # sind moeglich, wenn die 'mindestens ein Termin'-Garantie darueber hinausgreift
    # (siehe test_fcst_matrix_shows_termine_beyond_the_horizon_from_the_order_guarantee).
    assert set(m.label for m in layout.fcst_months) <= set(matrix.columns)
    assert list(matrix.columns) == sorted(matrix.columns)
    assert matrix.loc["1/136648", "2027_03"] == 150
    assert matrix.loc["SLEEP-001"].isna().all()


def test_fcst_matrix_extends_columns_beyond_a_larger_configured_horizon(computed):
    """Regression: die Matrix zeigte bisher immer nur die 18 festen FCST-Spalten
    der Eingabedatei, unabhaengig vom Horizont-Regler - ein groesserer Horizont
    aenderte sichtbar nichts, obwohl die Berechnung dahinter korrekt lief."""
    layout, items, _, _, stichtag = computed
    cfg = Config(horizon_months=36)
    decisions = {i.item_number: forecast(i, stichtag, cfg) for i in items}
    matrix = _fcst_matrix(items, decisions, stichtag, cfg)
    assert matrix.columns[-1] == (stichtag + 35).label
    assert len(matrix.columns) > len(layout.fcst)


def test_fcst_matrix_shows_termine_beyond_the_horizon_from_the_order_guarantee(computed):
    """Ein per 'mindestens ein Termin'-Garantie ueber den Horizont hinaus gezeigter
    FCST-Punkt (siehe Config.guarantee_first_order) darf in der Matrix nicht
    verschwinden, nur weil er ausserhalb der Horizont-Spalten liegt."""
    layout, items, _, _, stichtag = computed
    cfg = Config(horizon_months=18)
    decisions = {i.item_number: forecast(i, stichtag, cfg) for i in items}
    beyond = [d for d in decisions.values() if d.fcst and d.fcst[-1].month > stichtag + 17]
    assert beyond, "Testvoraussetzung: mindestens ein Item mit Termin ueber den Horizont hinaus"
    matrix = _fcst_matrix(items, decisions, stichtag, cfg)
    for d in beyond:
        assert d.fcst[-1].month.label in matrix.columns


# --- Kompletter Upload-Pfad ----------------------------------------------


@pytest.fixture
def app():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    return at


def test_app_starts_without_a_file(app):
    assert not app.exception
    assert len(app.file_uploader) == 1
    assert "Noch keine Datei geladen" in app.info[0].value


def test_app_processes_an_uploaded_workbook(app):
    app.file_uploader[0].set_value(("Sammeldatei_Test.xlsx", SAMPLE.read_bytes(), MIME))
    app.run()

    assert not app.exception
    assert {m.label: m.value for m in app.metric}["Artikel"] == "12"
    assert [t.label for t in app.tabs] == ["Übersicht", "FCST-Matrix", "Artikel-Detail", "Export"]
    assert len(app.get("download_button")) == 2
    assert "Item D228025-100" in app.code[0].value


def test_app_reports_a_broken_upload_as_error_not_traceback(app):
    app.file_uploader[0].set_value(("kaputt.xlsx", b"das ist keine Excel-Datei", MIME))
    app.run()
    assert not app.exception, "Fehler muss als st.error erscheinen, nicht als Traceback"
    assert app.error and "konnte nicht gelesen werden" in app.error[0].value


def test_app_rejects_an_unreadable_stichtag(app):
    app.file_uploader[0].set_value(("Sammeldatei_Test.xlsx", SAMPLE.read_bytes(), MIME))
    app.run()
    app.sidebar.text_input[0].set_value("Quartal 3").run()
    assert app.error and "Stichtag unlesbar" in app.error[0].value


def test_app_stichtag_change_shifts_the_forecast(app):
    app.file_uploader[0].set_value(("Sammeldatei_Test.xlsx", SAMPLE.read_bytes(), MIME))
    app.run()
    before = _matrix_of(app)
    app.sidebar.text_input[0].set_value("2027_02").run()
    assert not app.exception
    assert _matrix_of(app) != before


def test_app_switching_t_method_changes_interval_only_where_demand_is_missing(app):
    app.file_uploader[0].set_value(("Sammeldatei_Test.xlsx", SAMPLE.read_bytes(), MIME))
    app.run()
    overview = app.dataframe[1].value.set_index("ItemNumber")
    assert overview.loc["D228025-100", "T"] == 4 and overview.loc["1/136648", "T"] == 3

    app.sidebar.selectbox[0].set_value(IntervalMethod.MEAN_GAPS).run()
    overview = app.dataframe[1].value.set_index("ItemNumber")
    assert overview.loc["D228025-100", "T"] == 3, "ohne Demand wirkt die Methode"
    assert overview.loc["1/136648", "T"] == 3, "mit Demand bleibt T unberuehrt"


def _matrix_of(app):
    """Die FCST-Matrix herausfischen: ihre Spalten sind Monatslabels."""
    for frame in (d.value for d in app.dataframe):
        if all(str(c)[:4].isdigit() for c in frame.columns):
            return frame.to_dict()
    raise AssertionError("FCST-Matrix nicht gefunden")


def test_validated_reference_items_are_not_flagged_as_unvalidated():
    """Regression: die Zaehlung darf nicht an `assumptions` haengen - auch validierte
    High Runner tragen Klassifizierungs-Hinweise."""
    from fcst_manager.model import UNVALIDATED_BRANCHES

    for name in ("Example.xlsx", "Example 2.xlsx"):
        layout, items, _ = read_items(DATA / name)
        decision = forecast(items[0], layout.stichtag(), Config())
        assert decision.assumptions, "Hinweise zur Klassifizierung sind vorhanden …"
        assert decision.branch not in UNVALIDATED_BRANCHES, "… der Ast ist trotzdem validiert"


def test_unvalidated_count_covers_the_three_branches_without_reference_data():
    from fcst_manager.model import Branch, UNVALIDATED_BRANCHES

    assert UNVALIDATED_BRANCHES == {
        Branch.HIGH_DEMAND_GAP,
        Branch.MID_STANDARD,
        Branch.MID_BACKLOG_DEMAND,
    }
    assert Branch.HIGH_STANDARD not in UNVALIDATED_BRANCHES
    assert Branch.SLEEPER_NO_FCST not in UNVALIDATED_BRANCHES


def test_derivation_rows_cover_every_computed_quantity(computed):
    from fcst_manager.app import _derivation_rows, _fcst_points_frame

    _, items, _, decisions, stichtag = computed
    item = next(i for i in items if i.item_number == "D228025-100")
    rows = {r["Schritt"]: str(r["Herleitung"]) for r in _derivation_rows(item, decisions["D228025-100"], stichtag)}

    assert "High Runner" in rows["Klassifizierung"]
    assert "4 in den letzten 18 Monaten" in rows["Bestellungen"]
    assert "6 in der gesamten Historie" in rows["Bestellungen"]
    assert "40" in rows["Menge Q"] and "Gleichstand" in rows["Menge Q"]
    assert "4 Monate" in rows["Intervall T"] and "impliziter Demand" in rows["Intervall T"]
    assert "2027_01" in rows["Erster Termin"] and "Split-Lieferung" in rows["Erster Termin"]

    points = _fcst_points_frame(decisions["D228025-100"])
    assert list(points["Monat"]) == ["2027_01", "2027_05", "2027_09", "2028_01"]
    assert points["Begründung"].str.len().min() > 0


def test_derivation_rows_stay_short_enough_to_read(computed):
    """Die Tabelle ersetzt den Monospace-Block, der rechts abgeschnitten wurde."""
    from fcst_manager.app import _derivation_rows

    _, items, _, decisions, stichtag = computed
    for item in items:
        for row in _derivation_rows(item, decisions[item.item_number], stichtag):
            assert row["Schritt"] and len(str(row["Schritt"])) <= 20
