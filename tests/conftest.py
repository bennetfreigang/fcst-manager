from pathlib import Path

import pytest

from fcst_manager.model import Item
from fcst_manager.periods import Month, month_range

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "data"
STICHTAG = Month.parse("2026_08")

# Fenster wie in den Referenzdateien: 42 Monate Historie, 14 Monate Order.
HIST_WINDOW = month_range(Month.parse("2023_01"), Month.parse("2026_06"))
ORDER_WINDOW = month_range(Month.parse("2026_07"), Month.parse("2027_08"))


def make_item(
    *,
    item_number="TEST",
    avg_demand=None,
    moq=None,
    lt=5,
    historie=None,
    order=None,
    hist_window=None,
) -> Item:
    """Item mit vollem Monatsfenster; ``historie``/``order`` als {'2025_09': 40}."""
    window = hist_window if hist_window is not None else HIST_WINDOW
    hist = {m: 0.0 for m in window}
    hist.update({Month.parse(k): float(v) for k, v in (historie or {}).items()})
    orders = {m: 0.0 for m in ORDER_WINDOW}
    orders.update({Month.parse(k): float(v) for k, v in (order or {}).items()})
    return Item(
        item_number=item_number,
        avg_demand=avg_demand,
        moq=moq,
        lt=lt,
        historie=hist,
        order=orders,
    )


@pytest.fixture
def stichtag() -> Month:
    return STICHTAG


@pytest.fixture(params=["Example.xlsx", "Example 2.xlsx"])
def reference_file(request) -> Path:
    return DATA / request.param
