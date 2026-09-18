"""Streamlit-Oberflaeche: Excel hochladen, FCST ansehen, Ergebnis herunterladen.

    uv run streamlit run src/fcst_manager/app.py

Die Importe sind absolut (``fcst_manager.…``), weil Streamlit diese Datei als
Skript ausfuehrt und nicht als Paketmodul - relative Importe scheitern dabei.

Wichtig zur Historie-Ansicht: die Umschaltung "letzte 18 Monate / gesamte
Historie" betrifft ausschliesslich die *Darstellung*. Gerechnet wird immer auf
der vollen Historie - eine Kuerzung wuerde das High-Runner-Kriterium
(mindestens 36 Monate Datenbasis) aushebeln und jeden Artikel zum Mid Runner
machen.
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from fcst_manager.engine import forecast
from fcst_manager.excel_io import (
    build_combined_workbook,
    compare_with_manual,
    read_item_master_table,
    read_items,
    read_orderbook_table,
    read_sales_history_table,
    write_output,
)
from fcst_manager.model import (
    UNVALIDATED_BRANCHES,
    Config,
    Decision,
    IntervalMethod,
    Item,
)
from fcst_manager.periods import Month, month_range

DISPLAY_WINDOW_MONTHS = 18

SERIES_COLORS = ["#4C78A8", "#F58518", "#54A24B"]  # Historie / Order / FCST

T_METHOD_LABELS = {
    IntervalMethod.IMPLIED_DEMAND: "Impliziter Demand (Standard, an Referenz validiert)",
    IntervalMethod.IMPLIED_DEMAND_ORDER_SPAN: "Impliziter Demand über Bestellspanne",
    IntervalMethod.MEAN_GAPS: "Mittelwert der Bestellabstände (1. Iteration)",
    IntervalMethod.MEAN_GAPS_FILTERED: "Mittelwert der Abstände > 1 Monat",
    IntervalMethod.MEDIAN_CLUSTER_GAPS: "Median der Cluster-Abstände",
}

# ---------------------------------------------------------------------------
# Daten laden und rechnen
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner="Excel wird gelesen …")
def _load(raw: bytes, sheet: str | None):
    layout, items, manual = read_items(io.BytesIO(raw), sheet)
    return layout, items, manual


def _compute(raw: bytes, sheet: str | None, stichtag_label: str, cfg: Config):
    """Bewusst UNGECACHT: forecast() ist reine Arithmetik ohne I/O (<1ms je Artikel,
    siehe Benchmark im Commit) - der Rechenaufwand ist kein Grund zu cachen. Wichtiger:
    st.cache_data hasht nur den Bytecode dieser Funktion selbst, nicht den von engine.py/
    estimators.py - eine Aenderung an der Q/T/Anchor-Formel wuerde in einer laufenden
    App-Session sonst STILL einen veralteten Cache-Treffer liefern, obwohl der Code
    laengst korrigiert ist (so beobachtet waehrend der Entwicklung: Q blieb nach einem
    Formel-Fix in einer laufenden Session auf dem alten Wert stehen)."""
    _, items, _ = _load(raw, sheet)
    stichtag = Month.parse(stichtag_label)
    return {i.item_number: forecast(i, stichtag, cfg) for i in items}


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------


def _orders_in_window(item: Item, stichtag: Month) -> int:
    start = stichtag - DISPLAY_WINDOW_MONTHS
    return sum(1 for m in item.sale_events() if m >= start)


def _overview_frame(items, decisions, stichtag) -> pd.DataFrame:
    rows = []
    for item in items:
        d = decisions[item.item_number]
        rows.append(
            {
                "ItemNumber": item.item_number,
                "Segment": str(d.segment),
                "Ast": str(d.branch),
                f"Bestell. ({DISPLAY_WINDOW_MONTHS} Mon.)": _orders_in_window(item, stichtag),
                "Bestell. (gesamt)": d.n_orders,
                "AVG Demand": item.avg_demand,
                "MOQ": item.moq,
                "LT": item.lt,
                "Lieferlücke": d.gap_date.label if d.gap_date else None,
                "Große Lücke": d.large_gap,
                "Q": d.qty,
                "T": d.interval,
                "Anchor": d.anchor.label if d.anchor else None,
                "FCST-Termine": len(d.fcst),
                "FCST-Summe": sum(p.qty for p in d.fcst) or None,
                "Abw. zu Demand": d.demand_deviation,
                "Warnungen": len(d.warnings),
            }
        )
    return pd.DataFrame(rows)


def _fcst_matrix(items, decisions, stichtag: Month, cfg: Config) -> pd.DataFrame:
    """Spalten = konfigurierter Horizont, erweitert um alle tatsaechlich berechneten
    FCST-Monate. Ohne die Erweiterung wuerden Termine verschwinden, die die
    'mindestens ein Termin'-Garantie ueber den Horizont hinaus zeigt (siehe
    Config.guarantee_first_order) - die Datei-eigenen FCST-Spalten allein reichen
    nicht, sobald der Horizont-Regler ueber deren feste Breite hinausgestellt wird.
    """
    horizon_end = stichtag + (cfg.horizon_months - 1)
    months = set(month_range(stichtag, horizon_end))
    for decision in decisions.values():
        months.update(p.month for p in decision.fcst)
    ordered = [m.label for m in sorted(months)]
    data = {
        item.item_number: [decisions[item.item_number].as_series().get(m) for m in ordered]
        for item in items
    }
    return pd.DataFrame(data, index=ordered).T


def _derivation_rows(item: Item, decision: Decision, stichtag: Month) -> list[dict[str, object]]:
    """Herleitung als Kennzahl/Wert/Begruendung - eine Stats-Tabelle, aus der sich das
    Ergebnis Zeile fuer Zeile nachvollziehen laesst, statt als Flusstext."""
    rows: list[tuple[str, object, str]] = [
        ("Klassifizierung", str(decision.segment), decision.segment_reason),
        (
            "Bestellungen",
            f"{_orders_in_window(item, stichtag)} / {decision.n_orders}",
            f"letzte {DISPLAY_WINDOW_MONTHS} Mon. / gesamte Historie ({decision.history_window_months} Mon.)",
        ),
        ("LT", f"{item.lt} Monate", "Lieferzeit, geht in die Lieferlücke ein"),
        (
            "AVG Demand",
            f"{item.avg_demand:.4g} Stk/Monat" if item.avg_demand else "—",
            "Basis für Intervall T und Menge Q, falls hinterlegt",
        ),
        ("MOQ", f"{item.moq:g}" if item.moq else "—", "Mindestbestellmenge, rundet Menge Q auf"),
    ]
    if decision.last_commitment:
        rows.append((
            "Letzte Bestellung",
            str(decision.last_commitment),
            decision.last_commitment_kind,
        ))
    if decision.gap_date:
        rows.append((
            "Lieferlücke",
            str(decision.gap_date),
            f"letzte Bindung + LT, {decision.gap_months_from_stichtag:+d} Monate ab Stichtag → "
            + ("GROSS" if decision.large_gap else "klein"),
        ))
    rows.append(("Ast", str(decision.branch), decision.branch_reason))
    if decision.qty is not None:
        rows.append(("Menge Q", f"{decision.qty:g}", decision.qty_source))
    if decision.implied_demand is not None:
        rows.append(("Impliziter Demand", f"{decision.implied_demand:.2f} Stk/Monat", ""))
    if decision.interval is not None:
        rows.append(("Intervall T", f"{decision.interval} Monate", decision.interval_source))
    if decision.anchor is not None:
        rows.append(("Erster Termin", str(decision.anchor), decision.anchor_source))
    if decision.skipped_months:
        rows.append((
            "Übersprungen",
            ", ".join(m.label for m in decision.skipped_months),
            "Order bereits bekannt",
        ))
    if decision.demand_deviation is not None:
        rows.append((
            "Abw. zu Demand",
            f"{decision.demand_deviation:+.1%}",
            "Prüfkriterium: ((Σ Orderbook + Σ FCST im Horizont) / Horizont) / AVG Demand − 1; "
            "0 % = Ø Monatsmenge trifft den Demand genau",
        ))
    return [{"Kennzahl": k, "Wert": v, "Begründung": b} for k, v, b in rows]


def _fcst_points_frame(decision: Decision) -> pd.DataFrame:
    return pd.DataFrame(
        [{"Monat": p.label, "Menge": p.qty, "Begründung": p.reason} for p in decision.fcst]
    )


def _timeline_frame(item: Item, decision: Decision, stichtag: Month, full: bool) -> pd.DataFrame:
    hist_months = sorted(item.historie)
    if not full:
        start = stichtag - DISPLAY_WINDOW_MONTHS
        hist_months = [m for m in hist_months if m >= start]

    fcst = {p.month: p.qty for p in decision.fcst}
    months = sorted(set(hist_months) | set(item.order) | set(fcst))
    return pd.DataFrame(
        {
            "Historie": [item.historie.get(m, 0) if m in hist_months else 0 for m in months],
            "Order": [item.order.get(m, 0) or 0 for m in months],
            "FCST": [fcst.get(m, 0) for m in months],
        },
        index=[m.label for m in months],
    )


# ---------------------------------------------------------------------------
# Upload: fertige Sammeldatei ODER drei getrennte Dateien
# ---------------------------------------------------------------------------


def _upload_combined() -> tuple[bytes | None, str | None]:
    uploaded = st.file_uploader(
        "Excel-Datei hochladen",
        type=["xlsx", "xlsm"],
        help="Aufbau wie die Beispieldateien: Zeile 1 Abschnitt (Historie/Order/FCST), "
        "Zeile 2 Spaltenkopf, ab Zeile 3 je Artikel eine Zeile.",
    )

    if uploaded is None:
        st.info(
            "Noch keine Datei geladen. Erwartetes Format:\n\n"
            "* **Zeile 1** — Abschnittsname je Spalte: `Historie`, `Order`, `FCST` "
            "(leer bei den Metaspalten)\n"
            "* **Zeile 2** — Spaltenkopf: `ItemNumber`, `AVG Demand ÖBB [mon]`, `MOQ Vertrag`, "
            "`LT [mon]` bzw. Monat `JJJJ_MM`\n"
            "* **Zeile 3 ff** — je Zeile ein Artikel; `.` oder leer bedeutet „unbekannt“\n\n"
            "Der Stichtag wird aus der ersten FCST-Spalte abgeleitet."
        )
        return None, None

    return uploaded.getvalue(), uploaded.name.rsplit(".", 1)[0]


def _upload_split() -> tuple[bytes | None, str | None]:
    st.info(
        "Drei einfache Dateien statt einer manuell zusammengebauten Sammeldatei: "
        "je eine Kopfzeile mit `ItemNumber` + Monatsspalten (`JJJJ_MM`) für SalesHistorie "
        "und OrderBook, dazu `ItemNumber`, `AVG Demand`, `MOQ`, `LT` für die Artikelstammdaten."
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        hist_file = st.file_uploader("SalesHistorie", type=["xlsx", "xlsm"], key="hist_upload")
    with col2:
        order_file = st.file_uploader("OrderBook", type=["xlsx", "xlsm"], key="order_upload")
    with col3:
        meta_file = st.file_uploader(
            "Artikelstammdaten (AVG Demand / MOQ / LT)",
            type=["xlsx", "xlsm"],
            key="meta_upload",
            help="Optional. Fehlt die Datei, gelten AVG Demand/MOQ/LT für alle Artikel als unbekannt.",
        )

    if hist_file is None or order_file is None:
        st.caption("Bitte mindestens SalesHistorie und OrderBook hochladen.")
        return None, None

    try:
        historie, w1 = read_sales_history_table(io.BytesIO(hist_file.getvalue()))
        order, w2 = read_orderbook_table(io.BytesIO(order_file.getvalue()))
        meta, w3 = read_item_master_table(io.BytesIO(meta_file.getvalue())) if meta_file else ({}, [])
    except Exception as exc:  # Datei-/Layoutfehler dem Nutzer zeigen, nicht als Traceback
        st.error(f"Datei konnte nicht gelesen werden: {exc}")
        return None, None

    for warn in w1 + w2 + w3:
        st.warning(warn, icon="⚠️")
    if meta_file is None:
        st.info(
            "Keine Artikelstammdaten hochgeladen — AVG Demand/MOQ/LT gelten für alle "
            "Artikel als unbekannt.",
            icon="ℹ️",
        )

    hist_items = set(historie)
    order_items = set(order)
    presets = {
        "Schnittmenge — in beiden Quellen": hist_items & order_items,
        "Vereinigung — in mindestens einer Quelle": hist_items | order_items,
        "Nur SalesHistorie": hist_items - order_items,
        "Nur OrderBook": order_items - hist_items,
    }
    choice = st.radio(
        "Für welche Artikel soll ein FCST erstellt werden?",
        list(presets),
        format_func=lambda k: f"{k}  ({len(presets[k])} Artikel)",
    )
    item_numbers = sorted(presets[choice])
    if not item_numbers:
        st.warning("Diese Auswahl enthält keine Artikel.", icon="⚠️")
        return None, None

    c1, c2 = st.columns(2)
    stichtag_label = c1.text_input(
        "Stichtag (JJJJ_MM)", value=Month.today().label, key="split_stichtag",
        help="Legt fest, ab welchem Monat die FCST-Spalten der zusammengeführten Datei beginnen.",
    )
    horizon = c2.number_input(
        "Horizont (Monate)", min_value=1, max_value=60, value=18, key="split_horizon",
        help="Anzahl FCST-Spalten in der zusammengeführten Datei. Kann unten im Reiter "
        "'Einstellungen' für die Anzeige noch separat angepasst werden.",
    )
    try:
        stichtag = Month.parse(stichtag_label)
    except ValueError as exc:
        st.error(f"Stichtag unlesbar: {exc}")
        return None, None

    wb = build_combined_workbook(historie, order, meta, item_numbers, stichtag, int(horizon))
    buffer = io.BytesIO()
    wb.save(buffer)
    st.success(f"{len(item_numbers)} Artikel zusammengeführt, Stichtag {stichtag}.")
    return buffer.getvalue(), "FCST_Zusammengefuehrt"


# ---------------------------------------------------------------------------
# Oberflaeche
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="FCST Rechner", page_icon="📦", layout="wide"
    )

    st.title("📦 Forecast Rechner")

    mode = st.radio(
        "Eingabe",
        ["SalesHistorie / OrderBook einzeln hochladen", "Fertige Sammeldatei hochladen"],
        horizontal=True,
    )
    if mode == "Fertige Sammeldatei hochladen":
        raw, source_name = _upload_combined()
    else:
        raw, source_name = _upload_split()

    if raw is None:
        st.stop()

    try:
        layout, items, manual = _load(raw, None)
    except Exception as exc:  # Datei-/Layoutfehler dem Nutzer zeigen, nicht als Traceback
        st.error(f"Datei konnte nicht gelesen werden: {exc}")
        st.stop()

    if not items:
        st.error("Keine Artikelzeilen gefunden. Steht die ItemNumber-Spalte in Zeile 2?")
        st.stop()

    # -- Sidebar ---------------------------------------------------------------
    with st.sidebar:
        st.header("Einstellungen")

        view = st.radio(
            "Historie-Ansicht",
            [f"Letzte {DISPLAY_WINDOW_MONTHS} Monate", "Gesamte Historie"],
            help="Betrifft nur die Anzeige. Gerechnet wird immer auf der gesamten Historie.",
        )
        full_history = view == "Gesamte Historie"

        try:
            default_stichtag = layout.stichtag().label
        except ValueError:
            default_stichtag = Month.today().label
            st.warning("Kein FCST-Abschnitt in der Datei — Stichtag bitte prüfen.")

        stichtag_label = st.text_input("Stichtag (JJJJ_MM)", value=default_stichtag)
        horizon = st.number_input(
            "Horizont (Monate)", min_value=1, max_value=60, value=len(layout.fcst) or 18
        )
        t_method = st.selectbox(
            "T-Schätzer ohne AVG Demand",
            list(IntervalMethod),
            format_func=lambda m: T_METHOD_LABELS[m],
        )
        large_gap = st.number_input(
            "Große Lieferlücke ab (Monate)", min_value=1, max_value=60, value=Config().large_gap_months
        )

        st.caption(
            f"Datei: {layout.sheet_name} · {len(items)} Artikel · "
            f"{len(layout.historie)} Monate Historie · {len(layout.order)} Monate Order"
        )
        for warn in layout.warnings:
            st.warning(warn, icon="⚠️")

    try:
        stichtag = Month.parse(stichtag_label)
    except ValueError as exc:
        st.error(f"Stichtag unlesbar: {exc}")
        st.stop()

    cfg = Config(
        horizon_months=int(horizon),
        interval_method=t_method,
        large_gap_months=int(large_gap),
    )
    decisions = _compute(raw, None, stichtag.label, cfg)

    # -- Tabs ------------------------------------------------------------------
    tab_overview, tab_matrix, tab_detail, tab_export = st.tabs(
        ["Übersicht", "FCST-Matrix", "Artikel-Detail", "Export"]
    )

    with tab_overview:
        with_fcst = sum(1 for d in decisions.values() if d.fcst)
        warned = sum(1 for d in decisions.values() if d.warnings)
        assumed = sum(
        1 for d in decisions.values() if d.branch in UNVALIDATED_BRANCHES and d.fcst
    )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Artikel", len(items))
        c2.metric("mit FCST", with_fcst)
        c3.metric("mit Warnungen", warned)
        c4.metric("Stichtag", stichtag.label)

        st.subheader("Verteilung auf die Äste des Entscheidungsbaums")
        counts = (
            pd.Series([str(d.branch) for d in decisions.values()])
            .value_counts()
            .rename_axis("Ast")
            .to_frame("Artikel")
        )
        st.dataframe(counts, width="stretch")
        if assumed:
            st.info(
                f"Bei {assumed} von {with_fcst} Artikeln mit FCST stammen die Zahlen aus einem "
                "Ast, der **nicht an Kundendaten validiert** "
                "ist. Die jeweilige Annahme steht im Artikel-Detail und in der Export-Datei.",
                icon="ℹ️",
            )

        st.subheader("Alle Artikel")
        st.dataframe(
            _overview_frame(items, decisions, stichtag),
            width="stretch",
            height=420,
            column_config={"Abw. zu Demand": st.column_config.NumberColumn(format="percent")},
        )

        if any(manual.values()):
            st.subheader("Vergleich mit dem manuellen FCST in der Datei")
            rows = []
            for item in items:
                verdict, diffs = compare_with_manual(decisions[item.item_number], manual.get(item.item_number, {}))
                if verdict != "kein manueller FCST":
                    rows.append({"ItemNumber": item.item_number, "Ergebnis": verdict, "Abweichungen": diffs})
            st.dataframe(pd.DataFrame(rows), width="stretch")

    with tab_matrix:
        st.subheader(f"FCST je Artikel und Monat ({cfg.horizon_months} Monate ab {stichtag})")
        st.dataframe(_fcst_matrix(items, decisions, stichtag, cfg), width="stretch", height=460)
        st.caption(
            "Leere Zellen = kein FCST-Termin. Monate mit bereits bekannter Order werden nicht "
            "doppelt belegt. Spalten reichen ueber den Horizont hinaus, wenn die "
            "'mindestens ein Termin'-Garantie einen spaeteren Termin zeigt (siehe Warnungen "
            "im Artikel-Detail)."
        )

    with tab_detail:
        numbers = [i.item_number for i in items]
        selected = st.selectbox("Artikel", numbers)
        item = next(i for i in items if i.item_number == selected)
        decision = decisions[selected]

        in_window = _orders_in_window(item, stichtag)
        if not full_history and in_window < decision.n_orders:
            st.warning(
                f"Nur {in_window} von {decision.n_orders} Bestellungen liegen in den letzten "
                f"{DISPLAY_WINDOW_MONTHS} Monaten. Für diesen Artikel lohnt die gesamte Historie "
                "(links umschaltbar).",
                icon="🔎",
            )

        st.subheader("Verlauf")
        st.bar_chart(
            _timeline_frame(item, decision, stichtag, full_history),
            color=SERIES_COLORS,
            height=280,
        )
        st.caption(
            ("Gesamte Historie" if full_history else f"Historie der letzten {DISPLAY_WINDOW_MONTHS} Monate")
            + " · Order und FCST immer vollständig."
        )

        st.subheader("Herleitung")
        st.table(pd.DataFrame(_derivation_rows(item, decision, stichtag)).set_index("Kennzahl"))

        if decision.fcst:
            st.subheader("FCST-Termine")
            st.dataframe(_fcst_points_frame(decision), width="stretch", hide_index=True)
        else:
            st.caption("Für diesen Artikel wird kein FCST erzeugt — siehe Ast und Warnungen.")

        with st.expander("Rohtext der Herleitung (für Copy & Paste)"):
            st.code(decision.explain(), language=None)

        for assumption in decision.assumptions:
            st.info(assumption, icon="ℹ️")
        for warning in decision.warnings:
            st.warning(warning, icon="⚠️")

    with tab_export:
        st.subheader("Ergebnis herunterladen")
        st.write(
            "Die Datei entspricht der Eingabe mit befüllten FCST-Spalten, ergänzt um das "
            "Blatt **FCST-Report** mit Segment, Ast, Q, T, Anchor samt Herleitung, Annahmen "
            "und Warnungen je Artikel."
        )
        buffer = io.BytesIO()
        write_output(io.BytesIO(raw), buffer, layout, items, decisions, manual)
        name = source_name or "FCST"
        st.download_button(
            "Excel mit FCST herunterladen",
            data=buffer.getvalue(),
            file_name=f"{name}_FCST.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
        st.download_button(
            "Audit-Log herunterladen (.txt)",
            data="\n\n".join(decisions[i.item_number].explain() for i in items) + "\n",
            file_name=f"{name}_audit.txt",
            mime="text/plain",
        )

if __name__ == "__main__":
    main()
