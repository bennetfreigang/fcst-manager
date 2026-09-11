"""
Forecast-Engine für ÖBB-Rahmenverträge
========================================
Automatisiert die Klassifizierung (High/Mid/Sleeper Runner) und die
Erstellung eines 18-Monats-FCST aus Verkaufshistorie + Orderbook.

Basiert auf der Logik, die gemeinsam mit dem Kunden anhand von zwei
Beispiel-Items hergeleitet und validiert wurde (Stand: erste Iteration).

WICHTIG / offene Punkte (siehe Kommentare im Code):
- Klassifizierungs-Schwellwerte (High/Mid/Sleeper) sind aus dem Diagramm
  abgeleitet, aber teilweise unscharf formuliert ("mehrfach/einfach
  unterjährig") -> Schwellwerte sind ein erster Entwurf und sollten mit
  mehr Beispiel-Items nachgeschärft werden.
- Mid-Runner-FCST-Methode und High-Runner-"Demand beachten"-Pfad sind
  noch nicht validiert (keine Beispieldaten dafür vorhanden) -> Platzhalter.
- T-Fallback ohne AvgDemand ist eine Annäherung an das manuelle
  "grobe Schätzung der Abstände" -> wird nicht immer exakt mit einer
  rein manuell erstellten FCST übereinstimmen.
"""

import math
from collections import Counter
from dataclasses import dataclass, field
import pandas as pd


def to_period(label: str) -> pd.Period:
    """'2026_08' -> Period('2026-08', 'M')"""
    return pd.Period(label.replace("_", "-"), freq="M")


def to_label(period: pd.Period) -> str:
    return period.strftime("%Y_%m")


@dataclass
class Item:
    item_number: str
    avg_demand: float | None      # None, wenn "." / nicht bekannt
    moq: float | None             # None, wenn "." / nicht bekannt
    lt: int                       # Lead Time in Monaten
    historie: dict                # {period_label: qty}
    order: dict                   # {period_label: qty}  (bekanntes Orderbook)


# ---------------------------------------------------------------------------
# 1. Klassifizierung (High Runner / Mid Runner / Sleeper)
# ---------------------------------------------------------------------------

def classify(item: Item) -> str:
    events = sorted(p for p, q in item.historie.items() if q and q > 0)
    n = len(events)

    # "historisch verkauft seit X Jahren" bezieht sich auf die gesamte
    # verfügbare Historie (Datenverfügbarkeit), nicht nur auf die Spanne
    # zwischen erstem und letztem Bestell-Event.
    total_months_available = len(item.historie)
    span_years = total_months_available / 12

    if n == 0:
        return "Sleeper"

    # -- High Runner: >=3 Bestellungen UND historisch >=3 Jahre Datenbasis.
    # Das "durchschnittlich >=1x/Quartal"-Kriterium aus dem Diagramm wird
    # hier bewusst NICHT als hartes UND-Kriterium behandelt (beide
    # validierten Beispiel-Items erfüllen es nicht strikt, sind laut Kunde
    # aber trotzdem High Runner) -> vermutlich eher ein weiches/OR-Kriterium.
    # TODO mit Kunde schärfen, sobald mehr Beispiele (v.a. Mid Runner) vorliegen.
    if n >= 3 and span_years >= 3:
        return "High Runner"

    # -- Mid Runner: 1-2 Bestellungen ODER mehrere aber lange keine Order mehr, geringer Demand
    if 1 <= n <= 2:
        return "Mid Runner"

    # mehrere Bestellungen, aber schon lange keine mehr
    return "Mid Runner"


# ---------------------------------------------------------------------------
# 2. Lieferlücke
# ---------------------------------------------------------------------------

def last_known_commitment(item: Item):
    """Letzter Monat mit bekannter Order ODER (falls keine Order) letzter Verkauf."""
    order_events = [to_period(p) for p, q in item.order.items() if q and q > 0]
    if order_events:
        return max(order_events), "order"
    sale_events = [to_period(p) for p, q in item.historie.items() if q and q > 0]
    if sale_events:
        return max(sale_events), "sale"
    return None, None


def lieferluecke(item: Item, current: pd.Period):
    last_commit, _ = last_known_commitment(item)
    if last_commit is None:
        return None, True  # keine Historie/Order -> Lücke "unendlich" groß
    gap_date = last_commit + item.lt
    months_from_now = (gap_date - current).n
    is_large = months_from_now > 12   # Kunde: "ich würde schätzen 1 Jahr ist groß"
    return gap_date, is_large


# ---------------------------------------------------------------------------
# 3. Bestellmenge Q
# ---------------------------------------------------------------------------

def compute_quantity(item: Item):
    nonzero = [q for q in item.historie.values() if q and q > 0]
    if not nonzero:
        return item.moq
    counts = Counter(nonzero)
    if item.moq:
        moq_multiples = {v: c for v, c in counts.items() if v % item.moq == 0}
        if moq_multiples:
            return max(moq_multiples, key=lambda v: (moq_multiples[v], v))
    return max(counts, key=lambda v: (counts[v], v))


# ---------------------------------------------------------------------------
# 4. Intervall T
# ---------------------------------------------------------------------------

def compute_interval(item: Item, qty):
    if item.avg_demand and item.avg_demand > 0:
        return max(1, math.floor(qty / item.avg_demand)), "formel"

    # Fallback: grobe Schätzung über Abstände der historischen Bestellmonate
    # (jeder Monat zählt einzeln, wie vom Kunden bestätigt)
    events = sorted(to_period(p) for p, q in item.historie.items() if q and q > 0)
    if len(events) < 2:
        return item.lt, "fallback_lt"  # keine Basis für einen Abstand -> LT als Notlösung
    gaps = [(events[i + 1] - events[i]).n for i in range(len(events) - 1)]
    avg_gap = sum(gaps) / len(gaps)
    return max(1, round(avg_gap)), "fallback_historie"


# ---------------------------------------------------------------------------
# 5. Erster FCST-Termin (Anchor) = Maximum aus Order-Kandidat & Sales/LT-Kandidat
# ---------------------------------------------------------------------------

def compute_anchor(item: Item, current: pd.Period, interval: int):
    order_events = [to_period(p) for p, q in item.order.items() if q and q > 0]
    sale_events = [to_period(p) for p, q in item.historie.items() if q and q > 0]

    candidate_order = (max(order_events) + interval) if order_events else None
    candidate_sales = max(
        [e + item.lt for e in sale_events] + [current + item.lt]
    )

    candidates = [c for c in [candidate_order, candidate_sales] if c is not None]
    return max(candidates)


# ---------------------------------------------------------------------------
# 6. FCST-Generierung (18 Monate)
# ---------------------------------------------------------------------------

def generate_fcst(item: Item, current: pd.Period, horizon_months: int = 18):
    horizon_start = current
    horizon_end = current + (horizon_months - 1)

    segment = classify(item)
    gap_date, is_large_gap = lieferluecke(item, current)

    result = {}
    notes = {"segment": segment, "lieferluecke": str(gap_date), "grosse_luecke": is_large_gap}

    if segment == "Sleeper":
        notes["methode"] = "kein FCST (Sleeper)"
        return result, notes

    if segment == "Mid Runner" and is_large_gap:
        notes["methode"] = "TODO: 'Nur FCST bei Backlog und ÖBB Demand' – noch nicht implementiert/validiert"
        return result, notes

    if segment == "High Runner" and is_large_gap:
        notes["methode"] = "TODO: 'FCST Demand beachten' – noch nicht implementiert/validiert"
        return result, notes

    # Standardfall (in beiden Beispielen einschlägig): High/Mid Runner ohne große Lieferlücke
    qty = compute_quantity(item)
    interval, interval_source = compute_interval(item, qty)
    anchor = compute_anchor(item, current, interval)
    notes.update({"Q": qty, "T": interval, "T_quelle": interval_source, "anchor": to_label(anchor)})

    p = anchor
    while p <= horizon_end:
        label = to_label(p)
        if not (item.order.get(label) or 0):
            result[label] = qty
        p = p + interval

    return result, notes
