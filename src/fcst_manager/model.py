"""Datenmodell: Eingabe-Item, Konfiguration und nachvollziehbares Ergebnis."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .periods import Month

# ---------------------------------------------------------------------------
# Eingabe
# ---------------------------------------------------------------------------


def clean_number(value: object, *, field_name: str) -> tuple[float | None, str | None]:
    """Excel-Zelle -> Zahl oder None. Gibt zusaetzlich eine Warnung zurueck.

    Der Kunde kodiert 'unbekannt' als '.'; leere Zellen kommen als None an.
    Alles andere Nicht-Numerische ist ein Datenfehler und wird gemeldet statt
    still verschluckt.
    """
    if value is None:
        return None, None
    if isinstance(value, str):
        stripped = value.strip().replace(",", ".")
        if stripped in ("", ".", "-", "n/a", "N/A"):
            return None, None
        try:
            return float(stripped), None
        except ValueError:
            return None, f"{field_name}: unlesbarer Wert {value!r} -> als unbekannt behandelt"
    if isinstance(value, bool):
        return None, f"{field_name}: Wahrheitswert {value!r} -> als unbekannt behandelt"
    if isinstance(value, (int, float)):
        return float(value), None
    return None, f"{field_name}: unerwarteter Typ {type(value).__name__} -> als unbekannt behandelt"


@dataclass
class Item:
    """Ein Artikel = eine Zeile der Eingabe-Excel."""

    item_number: str
    avg_demand: float | None
    moq: float | None
    lt: int
    historie: dict[Month, float]
    order: dict[Month, float]
    row: int | None = None
    warnings: list[str] = field(default_factory=list)

    # -- abgeleitete Sichten ----------------------------------------------
    def sale_events(self) -> list[Month]:
        """Historie-Monate mit Menge > 0, aufsteigend. Jeder Monat = eine Bestellung."""
        return sorted(m for m, q in self.historie.items() if q and q > 0)

    def order_events(self) -> list[Month]:
        return sorted(m for m, q in self.order.items() if q and q > 0)

    def open_order_events(self, stichtag: Month) -> list[Month]:
        """Orderbook-Eintraege ab Stichtag = offener Backlog."""
        return [m for m in self.order_events() if m >= stichtag]

    def history_window_months(self) -> int:
        """Breite des Historie-Fensters in Monaten (= Datenverfuegbarkeit)."""
        return len(self.historie)

    def total_history_qty(self) -> float:
        return sum(q for q in self.historie.values() if q and q > 0)


# ---------------------------------------------------------------------------
# Konfiguration: alle Schwellwerte an einer Stelle, damit sie mit dem Kunden
# nachgeschaerft werden koennen, ohne die Logik anzufassen.
# ---------------------------------------------------------------------------


class IntervalMethod(StrEnum):
    """T-Schaetzer fuer den Fall, dass AVG Demand fehlt."""

    IMPLIED_DEMAND = "implied_demand"              # Default (vom Kunden gewaehlt)
    IMPLIED_DEMAND_ORDER_SPAN = "implied_demand_order_span"
    MEAN_GAPS = "mean_gaps"                        # Status quo der ersten Iteration
    MEAN_GAPS_FILTERED = "mean_gaps_filtered"
    MEDIAN_CLUSTER_GAPS = "median_cluster_gaps"


@dataclass(frozen=True)
class Config:
    horizon_months: int = 18

    # Klassifizierung
    high_runner_min_orders: int = 3
    high_runner_min_window_months: int = 36
    sleeper_max_orders: int = 1
    sleeper_min_age_months: int = 12

    # Lieferluecke
    large_gap_months: int = 12          # "groesser als" -> strikt >

    # FCST-Berechnung
    interval_method: IntervalMethod = IntervalMethod.IMPLIED_DEMAND
    split_delivery_max_gap: int = 1     # Folgemonate innerhalb dieser Distanz = eine Order
    max_interval_months: int = 36       # Notbremse gegen absurd lange Intervalle
    guarantee_first_order: bool = True
    """Den naechsten faelligen Termin immer anzeigen, auch wenn er hinter dem
    Horizont-Ende liegt (weitere Wiederholungen bleiben am Horizont gekappt).
    Ohne dieses Flag verschwindet der FCST komplett, sobald T sehr gross wird
    (z.B. winziger AVG Demand relativ zur Bestellmenge) - fachlich unerwuenscht,
    denn ein klassifizierter Runner sollte immer mindestens einen naechsten
    Bestelltermin zeigen. Real beobachtet an Item 1847010008 (T=33 Monate)."""
    anchor_on_order_cluster_start: bool = True
    """Split-Lieferungen im Orderbook als eine Order werten und den Anchor auf deren
    ersten Monat setzen. An beiden Referenz-Items validiert; auf False faellt die
    Engine auf 'letzter Order-Monat + T' zurueck (Verhalten der ersten Iteration)."""


# ---------------------------------------------------------------------------
# Ergebnis
# ---------------------------------------------------------------------------


class Segment(StrEnum):
    HIGH = "High Runner"
    MID = "Mid Runner"
    SLEEPER = "Sleeper"


class Branch(StrEnum):
    """Ast im Entscheidungsbaum des Kundendiagramms."""

    SLEEPER_NO_FCST = "Sleeper -> kein FCST"
    HIGH_STANDARD = "High Runner, kleine Luecke -> Standard-FCST"
    HIGH_DEMAND_GAP = "High Runner, grosse Luecke -> FCST mit Demand-Menge"
    MID_STANDARD = "Mid Runner, kleine Luecke -> Standard-FCST"
    MID_BACKLOG_DEMAND = "Mid Runner, grosse Luecke -> FCST nur bei Backlog + Demand"
    NO_FCST_DATA = "kein FCST (Datenlage unzureichend)"


UNVALIDATED_BRANCHES = frozenset(
    {Branch.HIGH_DEMAND_GAP, Branch.MID_STANDARD, Branch.MID_BACKLOG_DEMAND}
)
"""Aeste, fuer die es keine Kundenreferenz gibt - ihre Zahlen beruhen auf Annahmen.

Nicht ueber ``Decision.assumptions`` bestimmbar: auch validierte Items tragen
Klassifizierungs-Hinweise, die nichts ueber die Verlaesslichkeit des FCST sagen.
"""


@dataclass
class FcstPoint:
    """Ein einzelner FCST-Wert samt Begruendung."""

    month: Month
    qty: float
    n: int                  # n-ter Termin ab Anchor (0 = Anchor)
    reason: str

    @property
    def label(self) -> str:
        return self.month.label


@dataclass
class Decision:
    """Vollstaendige Herleitung eines Item-FCST - Basis fuer das Audit-Log."""

    item_number: str
    stichtag: Month
    segment: Segment
    segment_reason: str
    branch: Branch
    branch_reason: str

    n_orders: int = 0
    history_window_months: int = 0
    last_commitment: Month | None = None
    last_commitment_kind: str = "keine"
    gap_date: Month | None = None
    gap_months_from_stichtag: int | None = None
    large_gap: bool = False

    qty: float | None = None
    qty_source: str = ""
    interval: int | None = None
    interval_source: str = ""
    implied_demand: float | None = None
    anchor: Month | None = None
    anchor_source: str = ""

    fcst: list[FcstPoint] = field(default_factory=list)
    skipped_months: list[Month] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    # -- Ausgabeformen -----------------------------------------------------
    def as_series(self) -> dict[str, float]:
        """FCST als {'2027_01': 40.0, ...} fuer den Excel-Export."""
        return {p.label: p.qty for p in self.fcst}

    def explain(self) -> str:
        """Klartext-Herleitung, wie sie dem Kunden vorgelegt werden kann."""
        L: list[str] = []
        L.append(f"Item {self.item_number}   (Stichtag {self.stichtag})")
        L.append(f"  Klassifizierung : {self.segment}  -  {self.segment_reason}")
        L.append(
            f"  Bestellungen    : {self.n_orders} in {self.history_window_months} Monaten Historie"
        )
        if self.last_commitment is not None:
            L.append(
                f"  Letzte Bindung  : {self.last_commitment} ({self.last_commitment_kind})"
            )
        if self.gap_date is not None:
            L.append(
                f"  Lieferluecke    : {self.gap_date} "
                f"(= letzte Bindung + LT), {self.gap_months_from_stichtag:+d} Monate ab Stichtag"
                f" -> {'GROSS' if self.large_gap else 'klein'}"
            )
        L.append(f"  Ast             : {self.branch}")
        if self.branch_reason:
            L.append(f"                    {self.branch_reason}")
        if self.qty is not None:
            L.append(f"  Menge Q         : {self.qty:g}  ({self.qty_source})")
        if self.implied_demand is not None:
            L.append(f"  impliz. Demand  : {self.implied_demand:.2f} Stk/Monat")
        if self.interval is not None:
            L.append(f"  Intervall T     : {self.interval} Monate  ({self.interval_source})")
        if self.anchor is not None:
            L.append(f"  Erster Termin   : {self.anchor}  ({self.anchor_source})")
        if self.fcst:
            L.append("  FCST:")
            for p in self.fcst:
                L.append(f"    {p.label}  {p.qty:>10g}   {p.reason}")
        else:
            L.append("  FCST: (leer)")
        for m in self.skipped_months:
            L.append(f"  uebersprungen   : {m} - Order bereits bekannt, kein Duplikat")
        for a in self.assumptions:
            L.append(f"  ANNAHME         : {a}")
        for w in self.warnings:
            L.append(f"  WARNUNG         : {w}")
        return "\n".join(L)
