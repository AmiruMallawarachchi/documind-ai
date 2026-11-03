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
    # ── Vector DB ─────────────────────────────────────────────────────────────
    # FREE-TIER NOTE: Pinecone Starter = 2 GB storage, 2M write units/month.
    # Re-embedding the same document repeatedly burns write units fast; the
    # ingestion service (Phase 1) will guard against re-indexing existing chunks.
    pinecone_api_key: str = Field(..., description="Pinecone API key — get from app.pinecone.io")
    pinecone_index_name: str = Field(..., description="Name of your Pinecone index, e.g. documind-chunks")

    # ── Document DB ───────────────────────────────────────────────────────────
    # FREE-TIER NOTE: MongoDB Atlas M0 = 512 MB hard cap. We store only metadata
    # + chunk pointers here — never full document text. The comment here is the
    # reminder; enforcement is in the mongo_service (Phase 1).
    mongodb_uri: str = Field(..., description="MongoDB Atlas connection string — get from Atlas dashboard")

    # ── CORS ──────────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins, e.g. "http://localhost:3000,https://yourapp.vercel.app"
    # We deliberately do NOT default to "*" — that would expose your free-tier
    # Groq/Pinecone quotas to any browser that finds the Render URL.
    cors_origins: str = Field(..., description="Comma-separated allowed CORS origins, no wildcard in prod")

    # ── App meta ──────────────────────────────────────────────────────────────
    app_version: str = Field(default="0.1.0", description="Displayed in /api/health response")

    # ── Ingestion tuning ─────────────────────────────────────────────────────
    # Non-secret values get defaults rather than being required: a wrong default is a bad
    # answer, but a missing secret is a crash. Only the crash should be fail-fast.
    environment: str = Field(default="development", description="'development' or 'production'")
    embedding_model_name: str = Field(
        default="all-MiniLM-L6-v2",
        description="sentence-transformers model. 384-dim, runs locally for zero API cost.",
    )
    embedding_dimension: int = Field(
        default=384,
        description="Must match the embedding model output; the Pinecone index is created with this.",
    )
    chunk_max_characters: int = Field(default=1000, description="Upper bound on a single chunk.")
    chunk_overlap_characters: int = Field(
        default=150,
        description="Characters carried into the next chunk so an answer is not split mid-sentence.",
    )
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, description="Hard cap per file (10 MB).")
    allowed_upload_extensions: str = Field(
        default=".pdf,.docx,.txt",
        description="Comma-separated extension allowlist, enforced before parsing.",
    )
    pinecone_cloud: str = Field(default="aws", description="Serverless index cloud.")
    pinecone_region: str = Field(default="us-east-1", description="Serverless index region.")
    mongo_database_name: str = Field(default="documind", description="Atlas database the collections live in.")

    # Single-user demo identity, until Phase 5 swaps in real JWT auth. owner_id is plumbed
    # through every read and write now on purpose: Pinecone metadata filtering is built into
    # the query path from day one rather than retrofitted after the fact.
    default_owner_id: str = Field(default="local-user", description="Owner key used in single-user mode.")

    # pydantic-settings reads from a .env file automatically if present;
    # in production (Render) the vars come from the dashboard instead.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    @property
    def cors_origins_list(self) -> list[str]:
        """Split the comma-separated string into a list FastAPI's CORSMiddleware expects."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_extensions_set(self) -> set[str]:
        """Normalised extension allowlist, e.g. {'.pdf', '.docx', '.txt'}."""
        return {ext.strip().lower() for ext in self.allowed_upload_extensions.split(",") if ext.strip()}

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() in {"production", "prod"}


# Module-level singleton — imported everywhere as `from app.core.config import settings`
# This is what triggers the fail-fast: pydantic validates all fields on first import.
settings = Settings()
