"""Explicit unit handling.

Canonical architectural geometry is stored in centimetres; the layout solver
works on integer millimetres. Conversions are exact (1 ft = 304.8 mm).
"""

from decimal import Decimal
from typing import Literal

LengthUnit = Literal["mm", "cm", "m", "in", "ft"]

_MM_PER_UNIT: dict[str, Decimal] = {
    "mm": Decimal(1),
    "cm": Decimal(10),
    "m": Decimal(1000),
    "in": Decimal("25.4"),
    "ft": Decimal("304.8"),
}


def to_mm(value: float | int | Decimal, unit: LengthUnit) -> Decimal:
    if unit not in _MM_PER_UNIT:
        raise ValueError(f"unsupported length unit: {unit!r}")
    return Decimal(str(value)) * _MM_PER_UNIT[unit]


def to_cm(value: float | int | Decimal, unit: LengthUnit) -> Decimal:
    return to_mm(value, unit) / Decimal(10)


def solver_mm_floor(value: float | int | Decimal, unit: LengthUnit) -> int:
    """Integer mm for a bounding dimension; rounds down so a plot never grows."""
    return int(to_mm(value, unit).to_integral_value(rounding="ROUND_FLOOR"))
