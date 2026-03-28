from typing import List, Literal

from pydantic import BaseModel, Field


class OrderItem(BaseModel):
    name: str = Field(..., min_length=1)
    qty: int = Field(..., ge=1)


class OrderRecord(BaseModel):
    orderId: str
    businessId: str
    customerPhone: str
    items: List[OrderItem]
    total: float
    status: Literal["pending", "accepted", "out_for_delivery", "delivered", "rejected"]
    address: str
    createdAt: str
