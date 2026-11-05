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
from pydantic import Field, model_validator
from typing import Literal


class Settings(BaseSettings):
    # ── LLM ───────────────────────────────────────────────────────────────────
    # FREE-TIER NOTE: Groq free tier has per-model rate limits (~30 req/min for
    # larger models). Retry/backoff logic lives in Phase 5; flag is here so you
    # know which key drives that constraint.
    groq_api_key: str = Field(..., description="Groq API key — get from console.groq.com")
    # The llama-3.x family this originally pinned is retired from Groq's catalogue, and reasoning
    # models (openai/gpt-oss-*) are not drop-in replacements here: they bill hidden thinking tokens
    # against max_tokens, so the router's 48-token budget returns an empty string and every question
    # loses its document scoping. This default answers directly, so small budgets still produce text.
    groq_model: str = Field(
        default="qwen/qwen3.8-27b",
        description=(
            "Chat model served by Groq today. Override with GROQ_MODEL if the default is retired or you "
            "want a different one."
        ),
    )

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

    # ── Auth ──────────────────────────────────────────────────────────────────
    jwt_secret: str = Field(
        ...,
        min_length=32,
        description="Random secret for JWT signing, e.g. openssl rand -hex 32. Shorter than 32 characters "
        "is refused at boot: HS256 keys below that are weak by RFC 7518 and PyJWT says so on every call.",
    )
    # The code a visitor types once to open a session. It is not a secret in the usual sense — the README
    # publishes it so reviewers can use the demo — so treat it as the price of entry, not as identity.
    # What it buys is that a stranger cannot point a script at the Render URL without knowing it, and the
    # JWT that follows carries the owner id, so the id cannot be forged into someone else's namespace.
    demo_access_code: str = Field(
        ..., min_length=6, description="Code required to mint a session JWT. Refused at boot if left blank."
    )
    jwt_ttl_minutes: int = Field(default=1440, description="Session lifetime; a day suits a demo.")

    # A verification link that expires in five minutes is a support ticket; one that never expires is a
    # phishing forward. A day fits a demo whose reviewer may register, close the tab, and come back.
    verification_ttl_minutes: int = Field(default=1440, description="How long a verification link stays valid.")

    # ── Rate limits ───────────────────────────────────────────────────────────
    # Per owner (login is per client address, which has no owner yet). These protect the free-tier Groq
    # and Pinecone budgets from one runaway client, which is the same quota every visitor shares.
    chat_requests_per_minute: int = Field(default=10, description="Max /api/chat calls per owner per minute.")
    upload_requests_per_minute: int = Field(default=6, description="Max /api/upload calls per owner per minute.")
    login_requests_per_minute: int = Field(default=5, description="Max /api/auth calls per address per minute.")
    # Signup gets an hour-long window instead: the cost it protects against is not a fast script but a slow
    # one filling the free Atlas cluster with unverified accounts, and one visitor legitimately needs a
    # second chance when the first email lands in spam.
    signup_requests_per_hour: int = Field(
        default=8, description="Max /api/auth/register and /resend calls per address per hour."
    )

    # ── Mail ──────────────────────────────────────────────────────────────────
    # Verification links go out through one of two transports. `console` writes the link to the log instead
    # of sending it, which is what lets the suite, a fresh clone, and a reviewer with no SMTP credentials
    # reach a verified account at all. `smtp` is the deployed setting and is configured for Gmail's App
    # Password flow: smtp.gmail.com:465 over implicit TLS.
    mail_transport: Literal["console", "smtp"] = Field(
        default="console",
        description="'console' logs the message; 'smtp' sends it through Gmail. Anything else is refused at boot.",
    )
    smtp_host: str = Field(default="smtp.gmail.com", description="SMTP relay host.")
    smtp_port: int = Field(default=465, description="SMTP relay port; 465 is implicit TLS.")
    # Both empty by default and required together: an SMTP transport with no credentials is a
    # misconfiguration that would only show up when a real visitor tried to confirm their address, so it
    # fails at boot instead. They are never logged, and the sender address is the same account.
    smtp_user: str = Field(default="", description="The Gmail address that sends verification mail (SMTP_USER).")
    smtp_password: str = Field(
        default="",
        description="A Gmail App Password for that address (SMTP_PASSWORD) — not the account password.",
    )

    # The verification link points at the frontend, which is a separate origin from this API. Read from
    # config rather than derived from the request because behind Render's proxy the Host header is the API's
    # own, and a link built from it would send the visitor to onrender.com.
    frontend_base_url: str = Field(
        default="http://localhost:3000", description="Public origin of the Next.js app, no trailing slash."
    )

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

    @model_validator(mode="after")
    def _smtp_transport_requires_credentials(self) -> "Settings":
        """Refuse to boot an SMTP configuration that cannot send.

        WHY: `MAIL_TRANSPORT=smtp` with a blank password is a deploy-time typo, and the only way it shows
        up is a visitor waiting for an email that never leaves the process. Fail at boot with the name of
        the missing variable instead.
        """
        if self.mail_transport == "smtp" and not (self.smtp_user.strip() and self.smtp_password.strip()):
            raise ValueError(
                "MAIL_TRANSPORT=smtp needs both SMTP_USER and SMTP_PASSWORD. For a local run, leave "
                "MAIL_TRANSPORT=console and the verification link is written to the log instead."
            )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        """Split the comma-separated string into a list FastAPI's CORSMiddleware expects."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def frontend_origin(self) -> str:
        """The frontend base URL with any trailing slash removed.

        Concatenated into `/verify?token=...`, so one stray slash in the dashboard setting would otherwise
        produce a link the browser resolves to the wrong path.
        """
        return self.frontend_base_url.strip().rstrip("/")

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
