"""Klassifizierung High Runner / Mid Runner / Sleeper."""

from __future__ import annotations

from .model import Config, Item, Segment
from .periods import Month


def classify(item: Item, stichtag: Month, cfg: Config) -> tuple[Segment, str, list[str]]:
    """Liefert (Segment, Begruendung, Annahmen).

    Die Regeln stammen aus dem Kundendiagramm "OeBB Rahmenvertraege". Wo das
    Diagramm unscharf ist, steht die getroffene Auslegung in ``assumptions``.
    """
    assumptions: list[str] = []
    events = item.sale_events()
    n = len(events)
    window = item.history_window_months()

    # Weiches Diagramm-Kriterium: nur berichten, nicht pruefen. Beide validierten
    # Beispiel-Items erfuellen es nicht strikt, gelten dem Kunden aber als High Runner.
    per_quarter = (n / (window / 3)) if window else 0.0
    quarter_note = f"{per_quarter:.2f} Bestellungen/Quartal"

    if n == 0:
        return Segment.SLEEPER, f"keine Bestellung in {window} Monaten Historie", assumptions

    age = stichtag - events[-1]

    if n >= cfg.high_runner_min_orders and window >= cfg.high_runner_min_window_months:
        assumptions.append(
            "High-Runner-Kriterium 'durchschnittlich >=1 Bestellung/Quartal' wird nur "
            f"berichtet, nicht geprueft (hier {quarter_note}) - so vom Kunden fuer die "
            "beiden Referenz-Items bestaetigt."
        )
        assumptions.append(
            f"'mind. {cfg.high_runner_min_window_months // 12} Jahre Datenbasis' ist als Breite "
            "des Historie-Fensters ausgelegt (Datenverfuegbarkeit), nicht als Alter des Artikels. "
            "Achtung: dieses Kriterium ist damit fuer alle Zeilen einer Datei identisch und "
            "unterscheidet die Items nicht."
        )
        return (
            Segment.HIGH,
            f"{n} Bestellungen (>= {cfg.high_runner_min_orders}) bei {window} Monaten "
            f"Datenbasis (>= {cfg.high_runner_min_window_months}); {quarter_note}",
            assumptions,
        )

    # Sleeper laut Diagramm: wenige Bestellungen, alt/klein, kein Demand-Wert.
    if (
        n <= cfg.sleeper_max_orders
        and item.avg_demand is None
        and age > cfg.sleeper_min_age_months
    ):
        assumptions.append(
            f"Sleeper-Abgrenzung ausgelegt als: <= {cfg.sleeper_max_orders} Bestellung(en) UND "
            f"kein AVG Demand UND letzte Bestellung aelter als {cfg.sleeper_min_age_months} "
            "Monate. Das Kriterium 'kleine Bestellungen' aus dem Diagramm ist nicht "
            "quantifizierbar und bleibt unberuecksichtigt."
        )
        return (
            Segment.SLEEPER,
            f"{n} Bestellung(en), letzte vor {age} Monaten, kein AVG Demand",
            assumptions,
        )

    if n < cfg.high_runner_min_orders:
        reason = f"{n} Bestellung(en) - unter der High-Runner-Schwelle {cfg.high_runner_min_orders}"
    elif window < cfg.high_runner_min_window_months:
        reason = (
            f"{n} Bestellungen, aber nur {window} Monate Datenbasis "
            f"(< {cfg.high_runner_min_window_months})"
        )
    else:
        reason = f"{n} Bestellungen, letzte vor {age} Monaten"
    assumptions.append(
        "Mid Runner ist der Restfall (weder High Runner noch Sleeper). Die Diagramm-Formulierung "
        "'geringer Demand' ist nicht quantifiziert und daher nicht als eigenes Kriterium "
        "umgesetzt."
    )
    return Segment.MID, f"{reason}; {quarter_note}", assumptions
