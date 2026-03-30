from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.contracts import success_response
from app.services.auth import (
    ROLE_BUSINESS_ADMIN,
    ROLE_SUPERADMIN,
    ensure_business_scope,
    get_current_user,
    require_feature_access,
    require_roles,
)
from app.services.dashboard import (
    add_catalog_product,
    create_business_with_subscription,
    get_activity_logs,
    get_admin_overview,
    get_business_analytics,
    get_business_detail_with_context,
    get_business_list_with_context,
    get_global_analytics,
    get_onboarding_state,
    list_vendor_activity,
    query_orders,
    remove_catalog_product,
    update_business_with_context,
    update_catalog_product,
    update_onboarding_state,
)
from app.services.vendor import get_business_profile, get_product_catalog, update_business_profile


router = APIRouter(prefix="/api", tags=["dashboard-api"])


class BusinessPayload(BaseModel):
    name: str = ""
    slug: str = ""
    whatsapp_number: str = ""
    vendor_phone: str = ""
    business_type: str = "retail"
    description: str = ""
    address: str = ""
    hours: str = ""
    contact_phone: str = ""
    support_details: str = ""
    policies: str = ""
    delivery_info: str = ""
    active: Optional[bool] = None
    plan: str = "basic"
    subscription_status: str = "trialing"


class ProductPayload(BaseModel):
    name: str = ""
    price: Optional[float] = None
    unit: str = "unit"
    category: str = ""
    description: str = ""
    tags: list[str] = []
    attributes: dict = {}
    active: Optional[bool] = None


class SettingsPayload(BaseModel):
    name: Optional[str] = None
    business_type: Optional[str] = None
    description: Optional[str] = None
    address: Optional[str] = None
    hours: Optional[str] = None
    contact_phone: Optional[str] = None
    support_details: Optional[str] = None
    policies: Optional[str] = None
    delivery_info: Optional[str] = None
    broadcast_groups: Optional[list[str]] = None


class OnboardingStepPayload(BaseModel):
    step: str
    mark_complete: bool = False


@router.get("/admin/overview")
def admin_overview(user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_SUPERADMIN)
    return success_response(get_admin_overview())


@router.get("/admin/businesses")
def admin_businesses(user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_SUPERADMIN)
    return success_response(get_business_list_with_context())


@router.get("/admin/businesses/{business_id}")
def admin_business_detail(business_id: str, user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_SUPERADMIN)
    business = get_business_detail_with_context(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Business not found.")
    return success_response(business)


@router.post("/admin/businesses")
def admin_create_business(payload: BusinessPayload, user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_SUPERADMIN)
    return success_response(create_business_with_subscription(payload.model_dump()))


@router.put("/admin/businesses/{business_id}")
def admin_update_business(business_id: str, payload: BusinessPayload, user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_SUPERADMIN)
    business = update_business_with_context(business_id, payload.model_dump(exclude_unset=True))
    if not business:
        raise HTTPException(status_code=404, detail="Business not found.")
    return success_response(business)


@router.get("/admin/orders")
def admin_orders(
    business_id: str = Query(default=""),
    status: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    user: dict = Depends(get_current_user),
) -> dict:
    require_roles(user, ROLE_SUPERADMIN)
    return success_response(query_orders(business_id=business_id, status=status, date_from=date_from, date_to=date_to))


@router.get("/admin/analytics")
def admin_global_analytics(user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_SUPERADMIN)
    return success_response(get_global_analytics())


@router.get("/admin/analytics/{business_id}")
def admin_business_analytics(business_id: str, user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_SUPERADMIN)
    return success_response(get_business_analytics(business_id))


@router.get("/admin/vendors")
def admin_vendors(business_id: str = Query(default=""), user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_SUPERADMIN)
    return success_response(list_vendor_activity(business_id))


@router.get("/admin/activity")
def admin_activity(
    business_id: str = Query(default=""),
    phone: str = Query(default=""),
    user: dict = Depends(get_current_user),
) -> dict:
    require_roles(user, ROLE_SUPERADMIN)
    if not business_id:
        raise HTTPException(status_code=400, detail="business_id is required.")
    return success_response(get_activity_logs(business_id, phone=phone))


@router.get("/business/me")
def business_me(user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    business = get_business_detail_with_context(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Business not found.")
    return success_response(business)


@router.get("/business/orders")
def business_orders(
    status: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    user: dict = Depends(get_current_user),
) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    return success_response(query_orders(business_id=business_id, status=status, date_from=date_from, date_to=date_to))


@router.get("/business/catalog")
def business_catalog(user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    return success_response(get_product_catalog(business_id=business_id, limit=100))


@router.post("/business/catalog")
def business_add_catalog(payload: ProductPayload, user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    return success_response(add_catalog_product(business_id, payload.model_dump()))


@router.put("/business/catalog/{product_name}")
def business_update_catalog(product_name: str, payload: ProductPayload, user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    product = update_catalog_product(business_id, product_name, payload.model_dump(exclude_unset=True))
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    return success_response(product)


@router.delete("/business/catalog/{product_name}")
def business_delete_catalog(product_name: str, user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    deleted = remove_catalog_product(business_id, product_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Product not found.")
    return success_response({"deleted": True, "product_name": product_name})


@router.get("/business/performance")
def business_performance(user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    require_feature_access(user, "performance")
    return success_response(get_business_analytics(business_id))


@router.get("/business/settings")
def business_settings(user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    return success_response(get_business_profile(business_id))


@router.put("/business/settings")
def business_update_settings(payload: SettingsPayload, user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    return success_response(update_business_profile(business_id=business_id, **payload.model_dump(exclude_unset=True)))


@router.get("/business/vendors")
def business_vendors(user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    require_feature_access(user, "vendors")
    business_id = ensure_business_scope(user, user.get("business_id"))
    return success_response(list_vendor_activity(business_id))


@router.get("/onboarding/status")
def onboarding_status(user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    return success_response(get_onboarding_state(business_id))


@router.post("/onboarding/step")
def onboarding_step(payload: OnboardingStepPayload, user: dict = Depends(get_current_user)) -> dict:
    require_roles(user, ROLE_BUSINESS_ADMIN, ROLE_SUPERADMIN)
    business_id = ensure_business_scope(user, user.get("business_id"))
    return success_response(update_onboarding_state(business_id, step=payload.step, mark_complete=payload.mark_complete))
