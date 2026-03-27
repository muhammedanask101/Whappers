from app.db.mongo import get_mongo_database


def init_db() -> None:
    get_mongo_database()
