from fastapi import APIRouter
from pydantic import BaseModel

from app.services.onboarding import create_business

router = APIRouter(prefix="/onboard", tags=["onboarding"])


class OnboardRequest(BaseModel):
    name: str
    phone: str


@router.post("/")
def onboard(req: OnboardRequest):
    business = create_business(req.name, req.phone)
    return {
        "message": "Business created",
        "business_id": business["business_id"]
    }