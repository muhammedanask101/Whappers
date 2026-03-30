from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path


def load_env_file() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class Settings:
    def __init__(self) -> None:
        load_env_file()
        self.environment = os.getenv("ENV", "dev").strip().lower() or "dev"
        self.debug_mode = os.getenv("DEBUG_MODE", os.getenv("DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}
        self.mongodb_uri = os.getenv("MONGODB_URI", "").strip()
        self.mongodb_db_name = os.getenv("MONGODB_DB_NAME", "").strip() or "whatsapp_agent"
        self.session_secret = os.getenv("MANAGER_SESSION_SECRET", "").strip() or secrets.token_urlsafe(48)
        self.session_https_only = self.environment in {"prod", "production"}
        self.session_same_site = os.getenv("SESSION_SAME_SITE", "lax").strip().lower() or "lax"
        self.session_cookie_domain = os.getenv("SESSION_COOKIE_DOMAIN", "").strip() or None
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.openai_api_url = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions").strip()
        self.openai_models = {
            "cheap": os.getenv("OPENAI_CHEAP_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")).strip(),
            "balanced": os.getenv("OPENAI_BALANCED_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")).strip(),
            "strong": os.getenv("OPENAI_STRONG_MODEL", os.getenv("OPENAI_SMART_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1"))).strip(),
        }
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.gemini_api_url = os.getenv("GEMINI_API_URL", "https://generativelanguage.googleapis.com/v1beta/models").strip()
        self.gemini_models = {
            "cheap": os.getenv("GEMINI_CHEAP_MODEL", "gemini-2.0-flash").strip(),
            "balanced": os.getenv("GEMINI_BALANCED_MODEL", "gemini-2.0-flash").strip(),
            "strong": os.getenv("GEMINI_STRONG_MODEL", "gemini-2.5-pro").strip(),
        }
        provider_priority = os.getenv("LLM_PROVIDER_PRIORITY", "openai,gemini").strip()
        self.llm_provider_priority = [item.strip().lower() for item in provider_priority.split(",") if item.strip()]
        self.twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        self.twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        self.twilio_whatsapp_number = os.getenv("TWILIO_WHATSAPP_NUMBER", "").strip()
        self.admin_token = os.getenv("ADMIN_TOKEN", "").strip()
        self.manager_email = os.getenv("MANAGER_EMAIL", "").strip().lower()
        self.manager_password = os.getenv("MANAGER_PASSWORD", "").strip()
        self.automation_interval_seconds = int(float(os.getenv("AUTOMATION_INTERVAL_SECONDS", "120")))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
