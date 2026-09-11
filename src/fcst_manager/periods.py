"""Monats-Arithmetik ohne pandas.

Der FCST rechnet ausschliesslich in Kalendermonaten. Ein eigener, unveraender-
licher ``Month``-Typ ist hier robuster als ``pandas.Period``: die Arithmetik ist
exakt (Integer statt Offset-Objekte), versionsunabhaengig und erlaubt klare
Fehlermeldungen beim Einlesen krummer Excel-Werte.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

_LABEL = re.compile(r"^\s*(\d{4})\s*[_\-./]\s*(\d{1,2})\s*$")


@dataclass(frozen=True, order=True)
class Month:
    """Ein Kalendermonat, intern als laufender Monatsindex (Jahr * 12 + Monat-1)."""

    index: int

    # -- Konstruktion ------------------------------------------------------
    @classmethod
    def of(cls, year: int, month: int) -> "Month":
        if not 1 <= month <= 12:
            raise ValueError(f"Monat ausserhalb 1..12: {month}")
        return cls(year * 12 + (month - 1))

    @classmethod
    def parse(cls, value: object) -> "Month":
        """Akzeptiert '2026_08', '2026-08', date/datetime.

        Bewusst streng: alles andere fliegt mit einer Meldung raus, die den
        Originalwert nennt, damit Datenfehler in der Excel auffindbar sind.
        """
        if isinstance(value, Month):
            return value
        if isinstance(value, _dt.date):  # deckt datetime mit ab
            return cls.of(value.year, value.month)
        if isinstance(value, str):
            m = _LABEL.match(value)
            if m:
                return cls.of(int(m.group(1)), int(m.group(2)))
        raise ValueError(f"Kein Monats-Label (erwartet 'JJJJ_MM'): {value!r}")

    @classmethod
    def today(cls, today: _dt.date | None = None) -> "Month":
        d = today or _dt.date.today()
        return cls.of(d.year, d.month)

    # -- Zugriff -----------------------------------------------------------
    @property
    def year(self) -> int:
        return self.index // 12

    @property
    def month(self) -> int:
        return self.index % 12 + 1

    @property
    def label(self) -> str:
        return f"{self.year:04d}_{self.month:02d}"

    # -- Arithmetik --------------------------------------------------------
    def __add__(self, months: int) -> "Month":
        if not isinstance(months, int):
            return NotImplemented
        return Month(self.index + months)

    def __sub__(self, other: "Month | int") -> "Month | int":
        """Month - Month -> Abstand in Monaten; Month - int -> Month."""
        if isinstance(other, Month):
            return self.index - other.index
        if isinstance(other, int):
            return Month(self.index - other)
        return NotImplemented

    def __str__(self) -> str:
        return self.label


def month_range(start: Month, end: Month) -> list[Month]:
    """Alle Monate von ``start`` bis ``end`` (beide inklusive)."""
    if end < start:
        return []
    return [start + i for i in range(end - start + 1)]
