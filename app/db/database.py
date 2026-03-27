import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, List, Optional


DB_PATH = Path("commerce.db")
_DB_LOCK = Lock()


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with _DB_LOCK:
        connection = get_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    customer_phone TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    total REAL NOT NULL,
                    status TEXT NOT NULL,
                    address TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_state (
                    phone TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    pending_type TEXT,
                    pending_data TEXT,
                    last_language TEXT,
                    last_message TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    name TEXT PRIMARY KEY,
                    price REAL NOT NULL,
                    unit TEXT NOT NULL,
                    active_offer_price REAL,
                    offer_until TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()


def execute(query: str, params: Iterable[Any] = ()) -> None:
    with _DB_LOCK:
        connection = get_connection()
        try:
            connection.execute(query, tuple(params))
            connection.commit()
        finally:
            connection.close()


def fetch_one(query: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    with _DB_LOCK:
        connection = get_connection()
        try:
            return connection.execute(query, tuple(params)).fetchone()
        finally:
            connection.close()


def fetch_all(query: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
    with _DB_LOCK:
        connection = get_connection()
        try:
            return connection.execute(query, tuple(params)).fetchall()
        finally:
            connection.close()

