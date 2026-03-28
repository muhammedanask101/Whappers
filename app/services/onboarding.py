from app.db.mongo import get_collection, utc_now
import uuid


def create_business(name: str, vendor_phone: str) -> dict:
    business_id = f"biz_{uuid.uuid4().hex[:8]}"

    business = {
        "business_id": business_id,
        "name": name,
        "vendor_phone": vendor_phone,
        "whatsapp_number": "",  # to be set
        "created_at": utc_now()
    }

    get_collection("businesses").insert_one(business)

    return business