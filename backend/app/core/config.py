"""
core/config.py — Central configuration for DocuMind AI.

All environment variables are loaded here through pydantic-settings.
If ANY required variable is missing, the app raises a clear ValidationError
at import time and refuses to start — this is intentional "fail-fast" design.

WHY fail-fast beats scattered os.getenv():
  - A missing GROQ_API_KEY discovered at boot gives you: "ValidationError: 1
    validation error for Settings / groq_api_key / Field required". 
  - The same key missing and read lazily gives you: a 500 error mid-request,
    buried inside a LangChain call stack, with no indication of what to fix.
  - Fail-fast turns a runtime mystery into a startup checklist item.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    # ── CORS ──────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins, e.g. "http://localhost:3000,https://yourapp.vercel.app"
    # We deliberately do NOT default to "*" — that would expose your free-tier
    # Groq/Pinecone quotas to any browser that finds the Render URL.
    cors_origins: str = Field(..., description="Comma-separated allowed CORS origins, no wildcard in prod")

    # ── App meta ──────────────────────────────────────────────────────────────
    app_version: str = Field(default="0.1.0", description="Displayed in /api/health response")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    @property
    def cors_origins_list(self) -> list[str]:
        """Split the comma-separated string into a list FastAPI's CORSMiddleware expects."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
