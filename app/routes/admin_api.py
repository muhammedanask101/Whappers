import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from app.services.dashboard import (
    create_business_with_subscription,
    delete_business_with_context,
    get_activity_logs,
    get_business_analytics,
    get_business_detail_with_context,
    get_business_list_with_context,
    get_global_analytics,
    get_support_tickets,
    get_usage_summary,
    query_orders,
    update_business_with_context,
)

router = APIRouter(prefix="/admin", tags=["admin-api"])


def _log_legacy_admin_usage(route: str) -> None:
    print(f"[route.admin_api] deprecated_route_used route={route!r}")


def _require_admin_token(token: Optional[str]) -> None:
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="Invalid admin token.")


@router.post("/business")
def create_business_endpoint(payload: Dict[str, Any], x_admin_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _log_legacy_admin_usage("/admin/business")
    _require_admin_token(x_admin_token)
    try:
        return create_business_with_subscription(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/businesses")
def list_businesses_endpoint(x_admin_token: Optional[str] = Header(default=None)) -> List[Dict[str, Any]]:
    _log_legacy_admin_usage("/admin/businesses")
    _require_admin_token(x_admin_token)
    return get_business_list_with_context()


@router.put("/business/{business_id}")
def update_business_endpoint(business_id: str, payload: Dict[str, Any], x_admin_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _log_legacy_admin_usage("/admin/business/{business_id}")
    _require_admin_token(x_admin_token)
    updated = update_business_with_context(business_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="Business not found.")
    return updated


@router.delete("/business/{business_id}")
def delete_business_endpoint(business_id: str, x_admin_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _log_legacy_admin_usage("/admin/business/{business_id}")
    _require_admin_token(x_admin_token)
    try:
        deleted = delete_business_with_context(business_id)
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
    _log_legacy_admin_usage("/admin/orders")
    _require_admin_token(x_admin_token)
    return query_orders(business_id=business_id)


@router.get("/usage")
def admin_usage(
    business_id: str = Query(default=""),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _log_legacy_admin_usage("/admin/usage")
    _require_admin_token(x_admin_token)
    return get_usage_summary(business_id)


@router.get("/support")
def admin_support(
    business_id: str = Query(default=""),
    x_admin_token: Optional[str] = Header(default=None),
) -> List[Dict[str, Any]]:
    _log_legacy_admin_usage("/admin/support")
    _require_admin_token(x_admin_token)
    return get_support_tickets(business_id)

@router.get("/dashboard/{business_id}")
def get_dashboard(business_id: str, x_admin_token: Optional[str] = Header(default=None)):
    _log_legacy_admin_usage("/admin/dashboard/{business_id}")
    _require_admin_token(x_admin_token)
    business = get_business_detail_with_context(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Business not found.")
    analytics = get_business_analytics(business_id)
    return {"business": business, **analytics}


@router.get("/agent-traces")
def agent_traces(
    business_id: str = Query(default=""),
    phone: str = Query(default=""),
    x_admin_token: Optional[str] = Header(default=None),
) -> List[Dict[str, Any]]:
    _log_legacy_admin_usage("/admin/agent-traces")
    _require_admin_token(x_admin_token)
    if not business_id:
        raise HTTPException(status_code=400, detail="business_id is required.")
    return get_activity_logs(business_id, phone=phone).get("agent_traces", [])
