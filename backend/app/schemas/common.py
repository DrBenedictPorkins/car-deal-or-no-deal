"""Shared schema conventions.

Money crosses this boundary as integer cents, same as it is stored. The UI formats;
nothing in between converts, so there is no rounding step to get wrong.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Timestamped(ORMModel):
    created_at: datetime | None = None
    updated_at: datetime | None = None
