"""Schaetzer fuer Bestellmenge Q und Bestellintervall T."""

from __future__ import annotations

import math
import statistics
from collections import Counter

from .model import Config, IntervalMethod, Item
from .periods import Month

_MOQ_TOL = 1e-9


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _is_moq_multiple(qty: float, moq: float) -> bool:
    """MOQ-Vielfaches mit Toleranz - reines ``%`` ist bei Floats unzuverlaessig."""
    if moq <= 0:
        return False
    ratio = qty / moq
    return abs(ratio - round(ratio)) <= _MOQ_TOL * max(1.0, abs(ratio))


def cluster_events(events: list[Month], max_gap: int) -> list[list[Month]]:
    """Gruppiert dicht aufeinanderfolgende Bestellmonate zu je einer Bestellung.

    Hintergrund: in der Historie erscheinen Split-Lieferungen derselben Order als
    zwei Monate in Folge (z.B. 2024_11 + 2024_12). Fuer Rhythmus-Schaetzer sind
    das keine zwei Bestellzyklen.
    """
    if not events:
        return []
    groups = [[events[0]]]
    for m in events[1:]:
        if m - groups[-1][-1] <= max_gap:
            groups[-1].append(m)
        else:
            groups.append([m])
    return groups


def _gaps(events: list[Month]) -> list[int]:
    return [events[i + 1] - events[i] for i in range(len(events) - 1)]


# ---------------------------------------------------------------------------
# Bestellmenge Q
# ---------------------------------------------------------------------------


def compute_quantity(item: Item, cfg: Config) -> tuple[float | None, str, list[str]]:
    """Q = historisch haeufigste Menge, MOQ-Vielfache bevorzugt.

    Gleichstand wird zur *zuletzt beobachteten* Menge aufgeloest (ueber Historie
    UND Order, wie auch sonst im Engine "letzte Bindung" vor reiner Historie
    zaehlt). Frueher wurde bei Gleichstand die *groessere* Menge gewaehlt - das
    war nur an D228025-100 validiert, wo 20 und 40 je 3x vorkommen und 40 auch
    zufaellig die zuletzt beobachtete Menge ist (Order 2026_10). Bei Item
    1847010008 (jede Menge 15/10/5 kommt genau 1x vor - ein reiner Gleichstand
    ohne echtes Muster) waehlte "groesser" faelschlich 15 statt der vom Kunden
    bestaetigten 5 (= letzte Order 2026_09, = letzte Historie 2026_03). Recency
    trifft beide Faelle richtig; "groesser" bleibt nur als letzter Tiebreak fuer
    den (praktisch unmoeglichen) Fall, dass sogar der letzte Monat gleich ist.
    """
    warnings: list[str] = []
    negatives = {m.label: q for m, q in item.historie.items() if q is not None and q < 0}
    if negatives:
        warnings.append(f"negative Historie-Mengen ignoriert: {negatives}")

    quantities = [q for q in item.historie.values() if q and q > 0]
    if not quantities:
        if item.moq:
            return float(item.moq), "keine Historie -> MOQ als Ersatzmenge", warnings
        return None, "keine Historie und kein MOQ -> Q unbestimmbar", warnings

    counts = Counter(quantities)
    last_seen = _last_seen_months(item)

    def pick(candidates: dict[float, int]) -> float:
        return max(candidates, key=lambda q: (candidates[q], last_seen.get(q), q))

    def describe(best: float, candidates: dict[float, int], scope: str) -> str:
        tie = [q for q, c in candidates.items() if c == candidates[best]]
        src = f"haeufigste Menge{scope} ({candidates[best]}x in der Historie)"
        if len(tie) > 1:
            src += (
                f"; Gleichstand mit {sorted(q for q in tie if q != best)} -> "
                f"zuletzt beobachtete Menge gewaehlt ({last_seen[best]})"
            )
        return src

    if item.moq and item.moq > 0:
        multiples = {q: c for q, c in counts.items() if _is_moq_multiple(q, item.moq)}
        if multiples:
            best = pick(multiples)
            src = describe(best, multiples, " unter den MOQ-Vielfachen") + f", MOQ={item.moq:g}"
            return float(best), src, warnings
        warnings.append(
            f"keine Historie-Menge ist ein Vielfaches der MOQ {item.moq:g} - "
            "MOQ-Praeferenz uebersprungen"
        )

    best = pick(counts)
    return float(best), describe(best, counts, ""), warnings


def _last_seen_months(item: Item) -> dict[float, Month]:
    """Je Menge der spaeteste Monat, in dem sie in Historie ODER Order auftaucht."""
    last: dict[float, Month] = {}
    for source in (item.historie, item.order):
        for month, qty in source.items():
            if qty and qty > 0 and (qty not in last or month > last[qty]):
                last[qty] = month
    return last


def round_to_moq(qty: float, moq: float | None) -> float:
    """Auf das naechste MOQ-Vielfache aufrunden (nie unter die MOQ)."""
    if not moq or moq <= 0:
        return float(round(qty))
    return float(max(1, math.ceil(qty / moq - _MOQ_TOL)) * moq)


# ---------------------------------------------------------------------------
# Impliziter Demand (Basis des vom Kunden gewaehlten T-Fallbacks)
# ---------------------------------------------------------------------------


def implied_demand(item: Item, stichtag: Month, *, span: str = "to_stichtag") -> float | None:
    """Verbrauchsrate aus der Historie, wenn AVG Demand fehlt.

    ``to_stichtag``  : Summe / Monate seit der ersten Bestellung bis Stichtag.
                       Beruecksichtigt eine Flaute seit der letzten Bestellung
                       und ist damit der vom Kunden gewaehlte Default.
    ``order_span``   : Summe / Monate zwischen erster und letzter Bestellung
                       (klassische Verbrauchsrate der aktiven Phase).
    """
    events = item.sale_events()
    if not events:
        return None
    total = item.total_history_qty()
    if total <= 0:
        return None
    if span == "order_span":
        months = events[-1] - events[0]
    else:
        months = stichtag - events[0]
    if months <= 0:
        # Einzige Bestellung liegt im Stichtagsmonat (oder danach): kein
        # belastbarer Zeitraum -> ein Monat als Untergrenze.
        months = 1
    return total / months


# ---------------------------------------------------------------------------
# Bestellintervall T
# ---------------------------------------------------------------------------


def compute_interval(
    item: Item, qty: float | None, stichtag: Month, cfg: Config
) -> tuple[int | None, str, float | None, list[str]]:
    """Liefert (T, Quelle, verwendeter Demand, Warnungen).

    T ist immer >= 1, sonst wuerde die FCST-Schleife nicht vorankommen.
    """
    warnings: list[str] = []

    def clamp(value: float, source: str) -> tuple[int, str]:
        t = max(1, int(value))
        if t > cfg.max_interval_months:
            warnings.append(
                f"T={t} ueber Obergrenze {cfg.max_interval_months} -> gekappt"
            )
            t = cfg.max_interval_months
        return t, source

    # 1) Validierter Hauptfall: AVG Demand bekannt.
    if item.avg_demand and item.avg_demand > 0:
        if qty is None:
            return None, "T unbestimmbar (kein Q)", item.avg_demand, warnings
        t, src = clamp(
            math.floor(qty / item.avg_demand),
            f"floor(Q/AvgDemand) = floor({qty:g}/{item.avg_demand:.4g})",
        )
        return t, src, item.avg_demand, warnings

    # 2) Fallback-Familie ohne AVG Demand.
    events = item.sale_events()
    method = cfg.interval_method

    if method in (IntervalMethod.IMPLIED_DEMAND, IntervalMethod.IMPLIED_DEMAND_ORDER_SPAN):
        span = "to_stichtag" if method is IntervalMethod.IMPLIED_DEMAND else "order_span"
        demand = implied_demand(item, stichtag, span=span)
        if demand and demand > 0 and qty is not None:
            label = (
                "Monate seit erster Bestellung"
                if span == "to_stichtag"
                else "Spanne erste..letzte Bestellung"
            )
            t, src = clamp(
                math.floor(qty / demand),
                f"floor(Q/impliziter Demand) = floor({qty:g}/{demand:.2f}); "
                f"Demand = {item.total_history_qty():g} Stk / {label}",
            )
            return t, src, demand, warnings
        warnings.append(
            "impliziter Demand nicht berechenbar (zu wenig Historie) -> Rhythmus-Schaetzer"
        )
        method = IntervalMethod.MEAN_GAPS

    if len(events) < 2:
        return (
            None,
            "T unbestimmbar: weniger als 2 Bestellungen und kein Demand",
            None,
            warnings,
        )

    if method is IntervalMethod.MEAN_GAPS:
        gaps = _gaps(events)
        t, src = clamp(
            round(statistics.fmean(gaps)),
            f"Mittelwert der Bestellabstaende {gaps}",
        )
        return t, src, None, warnings

    if method is IntervalMethod.MEAN_GAPS_FILTERED:
        gaps = [g for g in _gaps(events) if g > cfg.split_delivery_max_gap]
        if not gaps:
            warnings.append(
                "nach Filterung der Split-Lieferungen bleibt kein Abstand -> Mittelwert roh"
            )
            gaps = _gaps(events)
        t, src = clamp(
            round(statistics.fmean(gaps)),
            f"Mittelwert der Bestellabstaende > {cfg.split_delivery_max_gap} Monat {gaps}",
        )
        return t, src, None, warnings

    if method is IntervalMethod.MEDIAN_CLUSTER_GAPS:
        starts = [g[0] for g in cluster_events(events, cfg.split_delivery_max_gap)]
        gaps = _gaps(starts)
        if not gaps:
            return (
                None,
                "T unbestimmbar: nur eine Bestellung nach Cluster-Bildung",
                None,
                warnings,
            )
        t, src = clamp(
            round(statistics.median(gaps)),
            f"Median der Abstaende zwischen Bestell-Clustern {gaps}",
        )
        return t, src, None, warnings

    raise ValueError(f"unbekannte Intervall-Methode: {method}")
