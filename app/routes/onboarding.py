from fastapi import APIRouter
from pydantic import BaseModel

from app.api.contracts import success_response
from app.services.dashboard import create_business_with_subscription, update_onboarding_state

router = APIRouter(prefix="/onboard", tags=["onboarding"])


class OnboardRequest(BaseModel):
    name: str
    phone: str


@router.post("/")
def onboard(req: OnboardRequest):
    business = create_business_with_subscription(
        {
            "name": req.name,
            "vendor_phone": req.phone,
            "contact_phone": req.phone,
            "whatsapp_number": req.phone,
            "plan": "basic",
            "subscription_status": "trialing",
        }
    )
    update_onboarding_state(business["business_id"], step="signup")
    return success_response({"message": "Business created", "business_id": business["business_id"], "business": business})
