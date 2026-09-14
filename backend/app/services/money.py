"""Money conversion at the API boundary.

Money is integer cents everywhere inside the system. Decimal appears only here, and
only to talk to the outside world. There are no floats in the money path.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENTS = Decimal("0.01")


def to_cents(value: Decimal | int | float | str | None) -> int | None:
    """Convert a dollar amount to integer cents, rounding half-up at the cent."""
    if value is None:
        return None
    if isinstance(value, int):
        return value * 100
    try:
        dec = Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"not a monetary amount: {value!r}") from exc
    return int(dec.quantize(CENTS, rounding=ROUND_HALF_UP) * 100)


def to_dollars(cents: int | None) -> Decimal | None:
    """Convert integer cents back to an exact decimal dollar amount."""
    if cents is None:
        return None
    return (Decimal(cents) / 100).quantize(CENTS)


def fmt(cents: int | None, *, dash: str = "—") -> str:
    """Human display, used in notification text and rule reasons."""
    if cents is None:
        return dash
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"


def bp_to_percent_str(bp: int | None) -> str:
    if bp is None:
        return "—"
    return f"{Decimal(bp) / 100:.2f}%"
