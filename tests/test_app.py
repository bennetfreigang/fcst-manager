"""Tests der Streamlit-App: Aufbereitungsfunktionen und der komplette Upload-Pfad."""

import io

import pandas as pd
import pytest

pytest.importorskip(
    "streamlit",
    reason="App-Tests benoetigen Streamlit: `uv sync --extra app` bzw. die Dev-Gruppe",
)

from fcst_manager.app import (  # noqa: E402
    DISPLAY_WINDOW_MONTHS,
    _fcst_matrix,
    _old_vs_new_frame,
    _orders_in_window,
    _overview_frame,
    _timeline_frame,
)
from fcst_manager.engine import forecast
from fcst_manager.excel_io import read_items
from fcst_manager.model import Config, IntervalMethod
from fcst_manager.periods import Month

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
    assert matrix.loc["1/136648", "2027_05"] == 150  # Anchor = letzte OrderBook-Zeile+LT, siehe engine.compute_anchor
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
    """Startet im Modus 'Fertige Sammeldatei hochladen' - die Tests hier pruefen
    genau diesen Pfad; der neue Split-Upload (SalesHistorie/OrderBook/Metadaten)
    hat eigene Tests weiter unten."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    at.radio[0].set_value("Fertige Sammeldatei hochladen").run()
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
    rows = {
        r["Kennzahl"]: f"{r['Wert']} {r['Begründung']}"
        for r in _derivation_rows(item, decisions["D228025-100"], stichtag)
    }

    assert "High Runner" in rows["Klassifizierung"]
    assert "4 / 6" in rows["Bestellungen"]
    assert "letzte 18 Mon." in rows["Bestellungen"]
    assert "gesamte Historie" in rows["Bestellungen"]
    assert "40" in rows["Menge Q"] and "Gleichstand" in rows["Menge Q"]
    assert "4 Monate" in rows["Intervall T"] and "impliziter Demand" in rows["Intervall T"]
    assert "2027_03" in rows["Erster Termin"] and "OrderBook" in rows["Erster Termin"]
    assert "—" in rows["Abw. zu Demand"], "D228025-100 hat kein AVG Demand -> Zeile bleibt sichtbar, aber leer"

    points = _fcst_points_frame(decisions["D228025-100"])
    assert list(points["Monat"]) == ["2027_03", "2027_07", "2027_11"]
    assert points["Begründung"].str.len().min() > 0


def test_derivation_rows_show_demand_deviation_percentage_when_avg_demand_is_known(computed):
    from fcst_manager.app import _derivation_rows

    _, items, _, decisions, stichtag = computed
    item = next(i for i in items if i.item_number == "1/136648")
    rows = {r["Kennzahl"]: r["Wert"] for r in _derivation_rows(item, decisions["1/136648"], stichtag)}
    assert rows["Abw. zu Demand"] == "+10.1%"


def test_old_vs_new_frame_compares_month_by_month(computed):
    """Zeile fuer Zeile: jeder Monat aus altem UND neuem FCST bekommt eine eigene
    Zeile, auch wenn er nur auf einer der beiden Seiten vorkommt."""
    _, items, _, decisions, stichtag = computed
    decision = decisions["D228025-100"]
    assert decision.as_series() == {"2027_03": 40, "2027_07": 40, "2027_11": 40}

    old = {
        Month.parse("2027_03"): 40,   # Treffer
        Month.parse("2027_07"): 30,   # Differenz
        Month.parse("2028_01"): 99,   # nur im alten FCST
    }
    frame = _old_vs_new_frame(decision, old).set_index("Monat")

    assert frame.loc["2027_03", ["Alt", "Neu", "Differenz", "Treffer"]].tolist() == [40, 40, 0, True]
    assert frame.loc["2027_07", ["Alt", "Neu", "Differenz", "Treffer"]].tolist() == [30, 40, 10, False]
    assert frame.loc["2028_01", "Neu"] is None or pd.isna(frame.loc["2028_01", "Neu"])
    assert frame.loc["2028_01", "Differenz"] == -99
    assert frame.loc["2027_11", "Alt"] is None or pd.isna(frame.loc["2027_11", "Alt"])
    assert frame.loc["2027_11", "Differenz"] == 40


# --- Split-Upload: SalesHistorie / OrderBook / Artikelstammdaten getrennt -


def _mini_xlsx(rows: list[list[object]]) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def split_app():
    """Default-Modus ist bereits der Split-Upload (erste Radio-Option)."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    return at


def test_split_upload_starts_with_three_uploaders_and_no_data_yet(split_app):
    assert not split_app.exception
    assert len(split_app.file_uploader) == 3
    assert "Bitte mindestens SalesHistorie und OrderBook" in split_app.caption[0].value


def test_split_upload_merges_intersection_by_default_and_computes_fcst(split_app):
    hist = _mini_xlsx([["ItemNumber", "2025_01", "2025_06"], ["A", 10, 10], ["B", 5, 0]])
    order = _mini_xlsx([["ItemNumber", "2026_09"], ["A", 10]])
    meta = _mini_xlsx([["ItemNumber", "AVG Demand", "MOQ", "LT"], ["A", 12.5, 5, 3]])

    at = split_app
    at.file_uploader[0].set_value(("hist.xlsx", hist, MIME))
    at.file_uploader[1].set_value(("order.xlsx", order, MIME))
    at.file_uploader[2].set_value(("meta.xlsx", meta, MIME))
    at.run()

    assert "Schnittmenge" in at.radio[1].value, "Default-Preset ist die Schnittmenge"
    at.text_input[0].set_value("2026_08").run()  # Stichtag des Merge-Schritts

    assert not at.exception
    assert {m.label: m.value for m in at.metric}["Artikel"] == "1", "nur A ist in beiden Quellen"
    assert [t.label for t in at.tabs] == ["Übersicht", "FCST-Matrix", "Artikel-Detail", "Export"]


def test_split_upload_union_preset_includes_items_from_either_source(split_app):
    hist = _mini_xlsx([["ItemNumber", "2025_01"], ["A", 10], ["B", 5]])
    order = _mini_xlsx([["ItemNumber", "2026_09"], ["A", 10]])

    at = split_app
    at.file_uploader[0].set_value(("hist.xlsx", hist, MIME))
    at.file_uploader[1].set_value(("order.xlsx", order, MIME))
    at.run()

    at.radio[1].set_value("Vereinigung — in mindestens einer Quelle").run()
    at.text_input[0].set_value("2026_08").run()

    assert not at.exception
    assert {m.label: m.value for m in at.metric}["Artikel"] == "2"


def test_split_upload_without_meta_file_treats_all_items_as_unknown(split_app):
    hist = _mini_xlsx([["ItemNumber", "2025_01"], ["A", 10]])
    order = _mini_xlsx([["ItemNumber", "2025_01"], ["A", 0]])

    at = split_app
    at.file_uploader[0].set_value(("hist.xlsx", hist, MIME))
    at.file_uploader[1].set_value(("order.xlsx", order, MIME))
    at.run()

    assert any("Keine Artikelstammdaten" in i.value for i in at.info)
    at.text_input[0].set_value("2026_08").run()
    assert not at.exception


def test_derivation_rows_stay_short_enough_to_read(computed):
    """Die Tabelle ersetzt den Monospace-Block, der rechts abgeschnitten wurde."""
    from fcst_manager.app import _derivation_rows

    _, items, _, decisions, stichtag = computed
    for item in items:
        for row in _derivation_rows(item, decisions[item.item_number], stichtag):
            assert row["Kennzahl"] and len(str(row["Kennzahl"])) <= 20
