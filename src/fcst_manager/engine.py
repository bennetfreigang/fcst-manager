"""FCST-Engine: Entscheidungsbaum aus dem Kundendiagramm, voll nachvollziehbar.

Jeder Lauf liefert ein ``Decision``-Objekt, aus dem sich jeder einzelne
FCST-Wert herleiten laesst (Q, T, Anchor, Ast, Annahmen, Warnungen).
"""

from __future__ import annotations

from .classify import classify
from .estimators import cluster_events, compute_interval, compute_quantity, round_to_moq
from .model import Branch, Config, Decision, FcstPoint, Item, Segment
from .periods import Month


# ---------------------------------------------------------------------------
# Lieferluecke
# ---------------------------------------------------------------------------


def last_known_commitment(item: Item) -> tuple[Month | None, str]:
    """Letzte bekannte Order, sonst letzter Verkauf."""
    orders = item.order_events()
    if orders:
        return orders[-1], "bekannte Order"
    sales = item.sale_events()
    if sales:
        return sales[-1], "letzter Verkauf"
    return None, "keine"


def supply_gap(item: Item, stichtag: Month, cfg: Config):
    """(letzte Bindung, Art, Lueckendatum, Monate ab Stichtag, gross?, Warnungen)."""
    warnings: list[str] = []
    commit, kind = last_known_commitment(item)
    if commit is None:
        return None, kind, None, None, True, warnings

    gap_date = commit + item.lt
    months = gap_date - stichtag
    is_large = months > cfg.large_gap_months

    if gap_date < stichtag:
        warnings.append(
            f"Lieferluecke {gap_date} liegt vor dem Stichtag - der Artikel ist seit "
            f"{-months} Monaten ungedeckt. Die Regel 'gross = mehr als "
            f"{cfg.large_gap_months} Monate ab Stichtag' bewertet das als kleine Luecke; "
            "bitte fachlich pruefen."
        )
    if kind == "bekannte Order" and not item.open_order_events(stichtag):
        warnings.append(
            "alle Orderbook-Eintraege liegen vor dem Stichtag - als Bindung verwendet, "
            "obwohl es sich faktisch um Vergangenheit handelt"
        )
    return commit, kind, gap_date, months, is_large, warnings


# ---------------------------------------------------------------------------
# Anchor
# ---------------------------------------------------------------------------


def compute_anchor(
    item: Item, stichtag: Month, interval: int, cfg: Config | None = None
) -> tuple[Month, str]:
    """Erster FCST-Termin = spaetester aller Kandidaten.

    Der Order-Kandidat setzt auf dem *Beginn* der letzten Order auf. Aufeinander-
    folgende Order-Monate sind Split-Lieferungen derselben Bestellung und starten
    keinen neuen Bestellzyklus - erst damit reproduzieren beide Referenz-Items
    exakt. Ueber ``Config.anchor_on_order_cluster_start=False`` abschaltbar.
    """
    cfg = cfg or Config()
    orders = item.order_events()
    sales = item.sale_events()

    candidates: list[tuple[Month, str]] = []
    if orders:
        if cfg.anchor_on_order_cluster_start:
            group = cluster_events(orders, cfg.split_delivery_max_gap)[-1]
            base, note = group[0], f"letzte Order {group[0]} + T={interval}"
            if len(group) > 1:
                note += (
                    f" (Split-Lieferung {', '.join(m.label for m in group)} "
                    "als eine Order gewertet)"
                )
        else:
            base, note = orders[-1], f"letzter Order-Monat {orders[-1]} + T={interval}"
        candidates.append((base + interval, note))
    if sales:
        candidates.append((sales[-1] + item.lt, f"letzter Verkauf {sales[-1]} + LT={item.lt}"))
    candidates.append((stichtag + item.lt, f"Stichtag {stichtag} + LT={item.lt}"))

    month, source = max(candidates, key=lambda c: c[0].index)
    others = sorted({c[0].label for c in candidates if c[0] != month})
    if others:
        source += f"; spaeter als {', '.join(others)}"
    return month, source


# ---------------------------------------------------------------------------
# Terminreihe
# ---------------------------------------------------------------------------


def _emit_series(
    item: Item,
    decision: Decision,
    anchor: Month,
    interval: int,
    qty: float,
    horizon_end: Month,
    qty_note: str,
    cfg: Config,
) -> None:
    """Erzeugt die Terminreihe Anchor, Anchor+T, Anchor+2T, ... bis Horizont-Ende.

    Bereits per Order belegte Monate werden uebersprungen (kein Duplikat). Ist
    ``cfg.guarantee_first_order`` gesetzt (Default), wird der erste noch nicht
    per Order belegte Termin immer aufgenommen - auch wenn er hinter dem
    Horizont-Ende liegt. Ohne das waere ein klassifizierter Runner mit sehr
    grossem T (z.B. winziger AVG Demand) komplett ohne FCST, obwohl es
    fachlich immer einen naechsten faelligen Termin gibt.
    """
    if interval < 1:
        raise ValueError(f"Intervall muss >= 1 sein, war {interval}")

    # Notbremse: verhindert eine Endlosschleife, falls das Orderbook aus
    # irgendeinem Grund jeden Monat auf unbegrenzte Zeit belegt haelt.
    safety_limit = anchor + max(cfg.max_interval_months * 10, 200)

    month, n = anchor, 0
    first_point_emitted = False
    while month <= horizon_end or (cfg.guarantee_first_order and not first_point_emitted):
        if month > safety_limit:
            decision.warnings.append(
                f"Suche nach dem ersten freien Termin ab Anchor {anchor} nach "
                f"{safety_limit - anchor} Monaten abgebrochen (jeder Monat war per "
                "Order belegt) - bitte Orderbook pruefen."
            )
            break

        existing = item.order.get(month) or 0
        if existing > 0:
            decision.skipped_months.append(month)
        else:
            beyond_horizon = month > horizon_end
            reason = (
                f"Anchor ({qty_note})"
                if n == 0
                else f"Anchor + {n}xT = {anchor} + {n}x{interval} ({qty_note})"
            )
            if beyond_horizon:
                reason += f"; liegt {month - horizon_end} Monate nach Horizont-Ende {horizon_end}"
            decision.fcst.append(FcstPoint(month=month, qty=qty, n=n, reason=reason))
            first_point_emitted = True
            if beyond_horizon:
                decision.warnings.append(
                    f"naechster faelliger Termin {month} liegt {month - horizon_end} Monate "
                    f"nach dem Horizont-Ende {horizon_end} - dennoch angezeigt, damit "
                    "mindestens ein Termin sichtbar ist; weitere Wiederholungen werden nicht "
                    "mehr ergaenzt."
                )
                break
        month, n = month + interval, n + 1


# ---------------------------------------------------------------------------
# Hauptfunktion
# ---------------------------------------------------------------------------


def forecast(item: Item, stichtag: Month, cfg: Config | None = None) -> Decision:
    """Klassifiziert das Item und erzeugt den FCST fuer den Horizont ab Stichtag."""
    cfg = cfg or Config()
    if cfg.horizon_months < 1:
        raise ValueError("horizon_months muss >= 1 sein")
    horizon_end = stichtag + (cfg.horizon_months - 1)

    segment, segment_reason, assumptions = classify(item, stichtag, cfg)
    commit, kind, gap_date, gap_months, large_gap, gap_warnings = supply_gap(item, stichtag, cfg)

    decision = Decision(
        item_number=item.item_number,
        stichtag=stichtag,
        segment=segment,
        segment_reason=segment_reason,
        branch=Branch.NO_FCST_DATA,
        branch_reason="",
        n_orders=len(item.sale_events()),
        history_window_months=item.history_window_months(),
        last_commitment=commit,
        last_commitment_kind=kind,
        gap_date=gap_date,
        gap_months_from_stichtag=gap_months,
        large_gap=large_gap,
        warnings=[*item.warnings, *gap_warnings],
        assumptions=assumptions,
    )

    # -- Ast 1: Sleeper -> kein FCST (validiert durch das Diagramm) ---------
    if segment is Segment.SLEEPER:
        decision.branch = Branch.SLEEPER_NO_FCST
        decision.branch_reason = "Sleeper erzeugen laut Diagramm keinen FCST"
        return decision

    # -- Q und T vorbereiten (fuer alle FCST-Aeste gleich) -----------------
    qty, qty_source, qty_warnings = compute_quantity(item, cfg)
    decision.warnings.extend(qty_warnings)
    interval, interval_source, demand, interval_warnings = compute_interval(
        item, qty, stichtag, cfg
    )
    decision.warnings.extend(interval_warnings)
    decision.qty, decision.qty_source = qty, qty_source
    decision.interval, decision.interval_source = interval, interval_source
    if item.avg_demand is None:
        decision.implied_demand = demand

    # -- Ast 2: Mid Runner + grosse Luecke ---------------------------------
    # Diagramm: "nur FCST bei Backlog und OeBB Demand".
    # Auslegung nach Absprache: Backlog = offener Eintrag im Orderbook.
    if segment is Segment.MID and large_gap:
        decision.branch = Branch.MID_BACKLOG_DEMAND
        decision.assumptions.append(
            "Mid Runner + grosse Luecke: 'Backlog' ist als offener Orderbook-Eintrag ab "
            "Stichtag ausgelegt (es gibt keine eigene Backlog-Spalte). 'OeBB Demand' ist als "
            "vorhandener AVG-Demand-Wert ausgelegt. Beides muss zutreffen. "
            "NICHT an Kundendaten validiert."
        )
        backlog = item.open_order_events(stichtag)
        if not backlog:
            decision.branch_reason = "kein offener Backlog im Orderbook -> kein FCST"
            return decision
        if item.avg_demand is None or item.avg_demand <= 0:
            decision.branch_reason = (
                f"Backlog vorhanden ({', '.join(m.label for m in backlog)}), "
                "aber kein AVG Demand -> kein FCST"
            )
            return decision
        if qty is None or interval is None:
            decision.branch_reason = "Q oder T unbestimmbar -> kein FCST"
            return decision
        anchor, anchor_source = compute_anchor(item, stichtag, interval, cfg)
        decision.anchor, decision.anchor_source = anchor, anchor_source
        decision.branch_reason = (
            f"Backlog {', '.join(m.label for m in backlog)} und AVG Demand "
            f"{item.avg_demand:.4g} vorhanden -> Standard-Terminreihe"
        )
        _emit_series(item, decision, anchor, interval, qty, horizon_end, "Menge aus Historie", cfg)
        return decision

    # -- Ast 3: High Runner + grosse Luecke --------------------------------
    # Diagramm: "FCST unter Beachtung des Demand".
    # Auslegung nach Absprache: Menge aus Demand statt aus der Historie,
    # erster Termin nicht vor dem Ende der Lieferluecke.
    if segment is Segment.HIGH and large_gap:
        decision.branch = Branch.HIGH_DEMAND_GAP
        decision.assumptions.append(
            "High Runner + grosse Luecke: 'Demand beachten' ist als Q = AVG Demand x T "
            "(auf MOQ-Vielfaches aufgerundet) ausgelegt, erster Termin nicht vor dem "
            "Lueckenende. T bleibt floor(Q_historisch/Demand), weil Q und T sonst zirkulaer "
            "voneinander abhaengen. NICHT an Kundendaten validiert."
        )
        if item.avg_demand is None or item.avg_demand <= 0:
            decision.branch_reason = (
                "kein AVG Demand vorhanden - 'Demand beachten' nicht anwendbar -> kein FCST"
            )
            return decision
        if interval is None:
            decision.branch_reason = "T unbestimmbar -> kein FCST"
            return decision

        demand_qty = round_to_moq(item.avg_demand * interval, item.moq)
        decision.qty = demand_qty
        decision.qty_source = (
            f"AVG Demand x T = {item.avg_demand:.4g} x {interval} = "
            f"{item.avg_demand * interval:.2f}, aufgerundet auf MOQ-Vielfaches "
            f"({item.moq:g})" if item.moq else
            f"AVG Demand x T = {item.avg_demand:.4g} x {interval} = "
            f"{item.avg_demand * interval:.2f}, gerundet (keine MOQ bekannt)"
        )
        anchor, anchor_source = compute_anchor(item, stichtag, interval, cfg)
        if gap_date is not None and anchor < gap_date:
            anchor, anchor_source = gap_date, f"Ende der Lieferluecke {gap_date}"
        decision.anchor, decision.anchor_source = anchor, anchor_source
        decision.branch_reason = (
            f"grosse Lieferluecke ({gap_months} Monate ab Stichtag) -> Menge aus Demand"
        )
        _emit_series(item, decision, anchor, interval, demand_qty, horizon_end, "Menge aus Demand", cfg)
        return decision

    # -- Ast 4/5: kleine Luecke -> Standard-FCST ---------------------------
    decision.branch = Branch.HIGH_STANDARD if segment is Segment.HIGH else Branch.MID_STANDARD
    if segment is Segment.MID:
        decision.assumptions.append(
            "Mid Runner + kleine Luecke nutzt dieselbe Standard-Berechnung wie der High "
            "Runner. Das ist eine Annahme - im Diagramm ist der Ast nicht ausgefuehrt und "
            "es gibt keine Referenzdaten."
        )
    if qty is None:
        decision.branch = Branch.NO_FCST_DATA
        decision.branch_reason = f"kein FCST: {qty_source}"
        return decision
    if interval is None:
        decision.branch = Branch.NO_FCST_DATA
        decision.branch_reason = f"kein FCST: {interval_source}"
        return decision

    anchor, anchor_source = compute_anchor(item, stichtag, interval, cfg)
    decision.anchor, decision.anchor_source = anchor, anchor_source
    decision.branch_reason = (
        f"kleine Lieferluecke ({gap_months} Monate ab Stichtag) -> Standard-Terminreihe "
        f"Q={qty:g} alle {interval} Monate"
    )
    _emit_series(item, decision, anchor, interval, qty, horizon_end, "Menge aus Historie", cfg)
    return decision
