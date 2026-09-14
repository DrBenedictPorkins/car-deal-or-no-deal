from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Dealer

DbSession = Annotated[Session, Depends(get_db)]


def get_dealer_or_404(db: Session, dealer_id: int) -> Dealer:
    dealer = db.get(Dealer, dealer_id)
    if dealer is None:
        raise HTTPException(status_code=404, detail=f"dealer {dealer_id} not found")
    return dealer
