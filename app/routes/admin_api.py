import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from app.db.mongo import get_collection, parse_datetime
from app.services.admin import delete_business
from app.services.vendor import create_business, get_business_by_id, list_businesses, update_business, get_product_catalog

router = APIRouter(prefix="/admin", tags=["admin-api"])


def _require_admin_token(token: Optional[str]) -> None:
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="Invalid admin token.")


@router.post("/business")
def create_business_endpoint(payload: Dict[str, Any], x_admin_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_admin_token(x_admin_token)
    try:
        return create_business(
            name=str(payload.get("name") or "").strip(),
            slug=str(payload.get("slug") or "").strip() or None,
            whatsapp_number=str(payload.get("whatsapp_number") or "").strip(),
            vendor_phone=str(payload.get("vendor_phone") or "").strip(),
            business_type=str(payload.get("business_type") or "retail").strip(),
            description=str(payload.get("description") or "").strip(),
            address=str(payload.get("address") or "").strip(),
            hours=str(payload.get("hours") or "").strip(),
            contact_phone=str(payload.get("contact_phone") or "").strip(),
            support_details=str(payload.get("support_details") or "").strip(),
            policies=str(payload.get("policies") or "").strip(),
            delivery_info=str(payload.get("delivery_info") or "").strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/businesses")
def list_businesses_endpoint(x_admin_token: Optional[str] = Header(default=None)) -> List[Dict[str, Any]]:
    _require_admin_token(x_admin_token)
    return list_businesses()


@router.put("/business/{business_id}")
def update_business_endpoint(business_id: str, payload: Dict[str, Any], x_admin_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_admin_token(x_admin_token)
    updated = update_business(
        business_id,
        name=payload.get("name"),
        slug=payload.get("slug"),
        whatsapp_number=payload.get("whatsapp_number"),
        vendor_phone=payload.get("vendor_phone"),
        business_type=payload.get("business_type"),
        description=payload.get("description"),
        address=payload.get("address"),
        hours=payload.get("hours"),
        contact_phone=payload.get("contact_phone"),
        support_details=payload.get("support_details"),
        policies=payload.get("policies"),
        delivery_info=payload.get("delivery_info"),
        active=payload.get("active"),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Business not found.")
    return updated


@router.delete("/business/{business_id}")
def delete_business_endpoint(business_id: str, x_admin_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_admin_token(x_admin_token)
    try:
        deleted = delete_business(business_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Business not found.")
    return {"ok": True, "business_id": business_id}


@router.get("/orders")
def admin_orders(
    business_id: str = Query(default=""),
    x_admin_token: Optional[str] = Header(default=None),
) -> List[Dict[str, Any]]:
    _require_admin_token(x_admin_token)
    query: Dict[str, Any] = {}
    if business_id:
        query["business_id"] = business_id
    rows = get_collection("orders").find(query).sort("created_at", -1).limit(200)
    return [
        {
            "business_id": row.get("business_id"),
            "order_id": row.get("order_id"),
            "customer_phone": row.get("customer_phone"),
            "status": row.get("status"),
            "total": float(row.get("total", 0)),
            "created_at": parse_datetime(row.get("created_at")).isoformat() if parse_datetime(row.get("created_at")) else None,
        }
        for row in rows
    ]


@router.get("/usage")
def admin_usage(
    business_id: str = Query(default=""),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin_token(x_admin_token)
    business_query = {"business_id": business_id} if business_id else {}
    history_query = {"business_id": business_id} if business_id else {}
    return {
        "messages": get_collection("chat_history").count_documents(history_query),
        "orders": get_collection("orders").count_documents(business_query),
        "support_tickets": get_collection("support_tickets").count_documents(business_query),
        "customers": len(get_collection("orders").distinct("customer_phone", business_query)),
    }


@router.get("/support")
def admin_support(
    business_id: str = Query(default=""),
    x_admin_token: Optional[str] = Header(default=None),
) -> List[Dict[str, Any]]:
    _require_admin_token(x_admin_token)
    query: Dict[str, Any] = {}
    if business_id:
        query["business_id"] = business_id
    rows = get_collection("support_tickets").find(query).sort("created_at", -1).limit(200)
    return [
        {
            "business_id": row.get("business_id"),
            "ticket_id": row.get("ticket_id"),
            "customer_phone": row.get("customer_phone"),
            "issue": row.get("issue"),
            "status": row.get("status"),
            "order_id": row.get("order_id"),
            "created_at": parse_datetime(row.get("created_at")).isoformat() if parse_datetime(row.get("created_at")) else None,
        }
        for row in rows
    ]
