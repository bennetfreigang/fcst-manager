"""Regressionscheck gegen die manuell erstellten Referenz-FCST des Kunden.

Aufruf:  uv run python -m fcst_manager.validate [datei.xlsx ...]
Ohne Argumente werden die Referenzdateien unter ``src/data`` geprueft.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .engine import forecast
from .excel_io import read_items
from .model import Config, IntervalMethod

DEFAULT_FILES = [
    Path(__file__).resolve().parents[1] / "data" / "Example.xlsx",
    Path(__file__).resolve().parents[1] / "data" / "Example 2.xlsx",
]


def check(path: Path, cfg: Config) -> tuple[int, int]:
    layout, items, manual = read_items(path)
    stichtag = layout.stichtag()
    hits = total = 0

    for item in items:
        decision = forecast(item, stichtag, cfg)
        computed = {p.month: p.qty for p in decision.fcst}
        reference = manual.get(item.item_number, {})
        months = sorted(set(reference) | set(computed))

        print("=" * 78)
        print(f"{path.name}  |  Item {item.item_number}")
        print(
            f"  AvgDemand={item.avg_demand}  MOQ={item.moq}  LT={item.lt}  "
            f"Stichtag={stichtag}  T-Methode={cfg.interval_method}"
        )
        print(f"  {decision.segment} / {decision.branch}")
        print(f"  Q={decision.qty}  T={decision.interval}  Anchor={decision.anchor}")
        print(f"  {'Monat':<10}{'Referenz':>10}{'berechnet':>12}{'':>4}")
        for month in months:
            ref = reference.get(month, 0)
            comp = computed.get(month, 0)
            ok = ref == comp
            total += 1
            hits += ok
            print(f"  {month.label:<10}{ref:>10g}{comp:>12g}{'  OK' if ok else '  ABWEICHUNG':>4}")
        if not months:
            print("  (kein Referenz-FCST und kein berechneter FCST)")
    return hits, total


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    files = [Path(a) for a in argv] or DEFAULT_FILES
    cfg = Config(interval_method=IntervalMethod.IMPLIED_DEMAND)

    hits = total = 0
    for path in files:
        if not path.exists():
            print(f"nicht gefunden: {path}", file=sys.stderr)
            return 2
        h, t = check(path, cfg)
        hits, total = hits + h, total + t

    print("=" * 78)
    print(f"Gesamt: {hits}/{total} Monate exakt reproduziert")
    return 0 if hits == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
