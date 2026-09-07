"""The IR — the contract every other module is written against.

No AI, no solver, no I/O. Everything downstream inherits this module's mistakes,
which is why it is built first and in isolation.
"""

from __future__ import annotations

from app.ir.enums import Facing, RoomKind, VastuStance
from app.ir.models import Brief, IntentResult, Locale, PlotSpec, ProgramHints, Provenance
from app.ir.units import LengthUnit, area_to_sq_m, to_metres

__all__ = [
    "Brief",
    "Facing",
    "IntentResult",
    "LengthUnit",
    "Locale",
    "PlotSpec",
    "ProgramHints",
    "Provenance",
    "RoomKind",
    "VastuStance",
    "area_to_sq_m",
    "to_metres",
]
