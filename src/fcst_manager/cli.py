"""Kommandozeile: ganze Excel-Dateien rechnen, erklaeren, T-Methoden vergleichen."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from .engine import forecast
from .estimators import compute_interval, compute_quantity
from .excel_io import compare_with_manual, read_items, write_output
from .model import Config, IntervalMethod
from .periods import Month


def _resolve_stichtag(args, layout) -> Month:
    if args.stichtag:
        return Month.parse(args.stichtag)
    if args.stichtag_heute:
        return Month.today()
    return layout.stichtag()


def _run_all(args):
    layout, items, manual = read_items(args.input, args.sheet)
    stichtag = _resolve_stichtag(args, layout)
    cfg = Config(
        # Default-Horizont = Breite des FCST-Abschnitts der Datei, sonst 18 Monate.
        horizon_months=args.horizon or len(layout.fcst) or 18,
        interval_method=IntervalMethod(args.t_method),
        large_gap_months=args.large_gap_months,
    )
    decisions = {i.item_number: forecast(i, stichtag, cfg) for i in items}
    return layout, items, manual, decisions, stichtag, cfg


def cmd_run(args) -> int:
    layout, items, manual, decisions, stichtag, cfg = _run_all(args)

    target = Path(args.output) if args.output else Path(args.input).with_name(
        Path(args.input).stem + "_FCST.xlsx"
    )
    write_output(args.input, target, layout, items, decisions, manual)

    print(f"Eingabe   : {args.input}  (Blatt '{layout.sheet_name}', {len(items)} Artikel)")
    print(f"Stichtag  : {stichtag}   Horizont: {cfg.horizon_months} Monate")
    print(f"T-Fallback: {cfg.interval_method}")
    print(f"Ausgabe   : {target}  (+ Blatt 'FCST-Report')")
    for warn in layout.warnings:
        print(f"  Layout-Warnung: {warn}")

    print("\nVerteilung auf die Aeste des Entscheidungsbaums:")
    for branch, count in Counter(str(d.branch) for d in decisions.values()).most_common():
        print(f"  {count:>5}x  {branch}")

    with_fcst = sum(1 for d in decisions.values() if d.fcst)
    warned = sum(1 for d in decisions.values() if d.warnings)
    print(f"\n  {with_fcst}/{len(items)} Artikel mit FCST, {warned} mit Warnungen")

    if any(manual.values()):
        print("\nVergleich mit vorhandenem manuellem FCST:")
        for item in items:
            verdict, diffs = compare_with_manual(decisions[item.item_number], manual.get(item.item_number, {}))
            if verdict == "kein manueller FCST":
                continue
            print(f"  {item.item_number:<16} {verdict}")
            if diffs and args.verbose:
                for part in diffs.split(" | "):
                    print(f"      {part}")

    if args.log:
        Path(args.log).write_text(
            "\n\n".join(decisions[i.item_number].explain() for i in items) + "\n",
            encoding="utf-8",
        )
        print(f"\nAudit-Log: {args.log}")
    return 0


def cmd_explain(args) -> int:
    _, items, _, decisions, _, _ = _run_all(args)
    wanted = set(args.item or [])
    shown = 0
    for item in items:
        if wanted and item.item_number not in wanted:
            continue
        print(decisions[item.item_number].explain())
        print()
        shown += 1
    if wanted and not shown:
        print(f"Kein Artikel gefunden fuer: {', '.join(sorted(wanted))}", file=sys.stderr)
        return 1
    return 0


def cmd_compare_t(args) -> int:
    """Zeigt T je Schaetzmethode - nur fuer Artikel ohne AVG Demand relevant."""
    layout, items, _ = read_items(args.input, args.sheet)
    stichtag = _resolve_stichtag(args, layout)
    methods = list(IntervalMethod)

    print(f"Stichtag {stichtag}. T-Schaetzer im Vergleich (Artikel ohne AVG Demand):\n")
    header = f"{'ItemNumber':<18}{'Q':>8}  " + "".join(f"{m.value[:22]:>24}" for m in methods)
    print(header)
    print("-" * len(header))
    relevant = 0
    for item in items:
        if item.avg_demand:
            continue
        relevant += 1
        qty, _, _ = compute_quantity(item, Config())
        cells = []
        for method in methods:
            t, _, _, _ = compute_interval(item, qty, stichtag, Config(interval_method=method))
            cells.append(f"{t if t is not None else '-':>24}")
        print(f"{item.item_number:<18}{qty if qty is not None else '-':>8}  " + "".join(cells))
    if not relevant:
        print("(keine - alle Artikel haben einen AVG Demand, der Fallback greift nicht)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fcst-manager",
        description="Forecast fuer Artikel aus OeBB-Rahmenvertraegen.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("input", help="Eingabe-Excel mit einer Zeile je Artikel")
        p.add_argument("--sheet", default=None, help="Arbeitsblatt (Default: erstes)")
        p.add_argument(
            "--stichtag",
            default=None,
            metavar="JJJJ_MM",
            help="Aktueller Zeitpunkt. Default: erste FCST-Spalte der Datei.",
        )
        p.add_argument(
            "--stichtag-heute",
            action="store_true",
            help="Statt der ersten FCST-Spalte den laufenden Kalendermonat verwenden.",
        )
        p.add_argument(
            "--horizon",
            type=int,
            default=None,
            help="FCST-Horizont in Monaten (Default: Anzahl der FCST-Spalten, sonst 18)",
        )
        p.add_argument(
            "--t-method",
            default=IntervalMethod.IMPLIED_DEMAND.value,
            choices=[m.value for m in IntervalMethod],
            help="T-Schaetzer, wenn AVG Demand fehlt",
        )
        p.add_argument(
            "--large-gap-months",
            type=int,
            default=Config().large_gap_months,
            help="Ab wie vielen Monaten ab Stichtag die Lieferluecke als gross gilt",
        )

    p_run = sub.add_parser("run", help="Alle Artikelzeilen rechnen und Excel exportieren")
    add_common(p_run)
    p_run.add_argument("-o", "--output", default=None, help="Ziel-Excel")
    p_run.add_argument("--log", default=None, help="Audit-Log als Textdatei schreiben")
    p_run.add_argument("-v", "--verbose", action="store_true", help="Abweichungen im Detail")
    p_run.set_defaults(func=cmd_run)

    p_explain = sub.add_parser("explain", help="Herleitung je Artikel im Klartext")
    add_common(p_explain)
    p_explain.add_argument("--item", action="append", help="Artikelnummer (mehrfach moeglich)")
    p_explain.set_defaults(func=cmd_explain)

    p_cmp = sub.add_parser("compare-t", help="T-Schaetzmethoden nebeneinander stellen")
    add_common(p_cmp)
    p_cmp.set_defaults(func=cmd_compare_t)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
