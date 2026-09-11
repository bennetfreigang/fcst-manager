"""Forecast-Automatisierung fuer Artikel aus OeBB-Rahmenvertraegen."""

from .classify import classify
from .engine import compute_anchor, forecast, supply_gap
from .estimators import compute_interval, compute_quantity, implied_demand
from .excel_io import read_items, write_output
from .model import Branch, Config, Decision, FcstPoint, IntervalMethod, Item, Segment
from .periods import Month

__all__ = [
    "Branch", "Config", "Decision", "FcstPoint", "IntervalMethod", "Item", "Month", "Segment",
    "classify", "compute_anchor", "compute_interval", "compute_quantity", "forecast",
    "implied_demand", "main", "read_items", "supply_gap", "write_output",
]


def main() -> int:
    from .cli import main as _main

    return _main()
