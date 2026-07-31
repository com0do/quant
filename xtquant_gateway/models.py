from __future__ import annotations

from pydantic import BaseModel, Field


class AccountReq(BaseModel):
    account_id: str | None = None


class PositionsReq(BaseModel):
    account_id: str | None = None


class QuoteReq(BaseModel):
    code: str
    account_id: str | None = None


class OrderReq(BaseModel):
    code: str
    side: str = Field(pattern="^(buy|sell)$")
    quantity: int = Field(gt=0)
    price: float | None = None
    price_type: str = "limit"
    strategy_name: str = ""
    order_remark: str = ""
    client_order_id: str | None = None
    account_id: str | None = None


class CancelReq(BaseModel):
    order_id: str
    account_id: str | None = None


class OrderStatusReq(BaseModel):
    order_id: str
    account_id: str | None = None


class FillsReq(BaseModel):
    since_ts: str = ""
    account_id: str | None = None
