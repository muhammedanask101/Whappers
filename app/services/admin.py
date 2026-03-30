from __future__ import annotations

from typing import Any, Dict, Optional

from app.db.mongo import DEFAULT_BUSINESS_ID
from app.services.dashboard import (
    create_business_with_subscription,
    delete_business_with_context,
    get_admin_overview,
    get_business_detail_with_context,
    get_business_list_with_context,
    update_business_with_context,
)


def _log_admin_service_usage(function_name: str) -> None:
    print(f"[service.admin] deprecated_manager_service_used function={function_name!r}")


def get_manager_overview() -> Dict[str, Any]:
    _log_admin_service_usage("get_manager_overview")
    return get_admin_overview()


def list_business_summaries() -> list[dict[str, Any]]:
    _log_admin_service_usage("list_business_summaries")
    return get_business_list_with_context()


def get_business_snapshot(business_id: str) -> Optional[dict[str, Any]]:
    _log_admin_service_usage("get_business_snapshot")
    return get_business_detail_with_context(business_id)


def create_business_from_form(form: Dict[str, str]) -> Dict[str, Any]:
    _log_admin_service_usage("create_business_from_form")
    return create_business_with_subscription(form)


def update_business_from_form(business_id: str, form: Dict[str, str]) -> Optional[dict[str, Any]]:
    _log_admin_service_usage("update_business_from_form")
    return update_business_with_context(business_id, form)


def delete_business(business_id: str) -> Optional[dict[str, Any]]:
    _log_admin_service_usage("delete_business")
    return delete_business_with_context(business_id)


def get_selected_business_id(requested: Optional[str]) -> str:
    _log_admin_service_usage("get_selected_business_id")
    if requested:
        business = get_business_detail_with_context(requested)
        if business:
            return requested
    businesses = get_business_list_with_context()
    return businesses[0]["business_id"] if businesses else DEFAULT_BUSINESS_ID
