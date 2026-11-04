# ─────────────────────────────────────────────────────────────────────────────
# DocuMind AI — backend image for Render (free tier: 0.1 CPU, 512 MB RAM).
#
# Build context is this directory, so nothing outside backend/ can land in the
# image. Render: Environment = Docker, Dockerfile = backend/Dockerfile.
#
# Everything below exists because of one constraint: this image has to fit and
# boot inside half a gigabyte.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/install/lib/python3.11/site-packages \
    HF_HOME=/build/hf-cache

# requirements.txt first: pip's resolution is the slowest layer, and it only needs
# re-running when a dependency changes, not on every code edit.
COPY requirements.txt .

# torch comes from PyTorch's CPU index, pinned with the +cpu local version so pip
# cannot silently fall back to the CUDA build. The default wheel drags ~2 GB of
# NVIDIA runtime libraries into an image that will never see a GPU, and the free
# tier's disk and RAM budgets are the binding constraints here, not speed.
# The pin matches the version the test suite runs against (backend/.venv), so the
# image and CI agree on what "torch" means.
RUN pip install --upgrade pip && \
    pip install --prefix=/install --extra-index-url https://download.pytorch.org/whl/cpu \
        "torch==2.15.0+cpu" && \
    pip install --prefix=/install -r requirements.txt

# Bake the embedding model into the image instead of downloading it on first
# request. Without this, the first chat after every cold start spends several
# seconds and ~100 MB of egress pulling MiniLM from Hugging Face before it can
# answer anything — the slowest possible first impression for a demo.
# Building here, not at runtime, also turns "no network to HF" into a failed
# build rather than a container that starts and then never answers a question.
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('all-MiniLM-L6-v2'); print('embedding model cached')"

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runner

# Unbuffered stdout: this app's whole observability story is one JSON line per
# record on stdout, and block-buffered output makes Render's log stream lag.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/home/documind/.cache/huggingface

# Non-root, because the container should not be able to write to its own image
# layers if a request ever reaches a filesystem bug. Render's $PORT is 10000, so
# nothing here needs privileges. The home directory is created by the COPY below
# (with the right ownership), which avoids a system user owning a skeleton /etc/skel
# copy it will never read.
RUN groupadd --system documind && \
    useradd --system --gid documind --home-dir /home/documind --shell /usr/sbin/nologin documind

WORKDIR /app

COPY --from=builder --chown=documind:documind /install /usr/local
COPY --from=builder --chown=documind:documind /build/hf-cache /home/documind/.cache/huggingface

# Only the application package: tests, scripts, .venv and .env are excluded by
# .dockerignore, and the COPY is explicit anyway so a stray file cannot leak in.
COPY --chown=documind:documind app/ ./app/

USER documind

EXPOSE 8000

# No API keys here, ever — Render injects them as environment variables at the
# dashboard level. ENV lines in a Dockerfile are baked into the image and are
# readable by anyone who can pull it.
# $PORT comes from Render (10000); 8000 is the local `docker run` fallback.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
