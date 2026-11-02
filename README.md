# DocuMind AI

Multi-agent document Q&A: upload PDFs, ask questions, get answers that cite the passage they came from.

This is the service skeleton — FastAPI, configuration validated at import, and a health endpoint. The
ingestion pipeline, the agent graph and the UI are built on top of it; `system.md` in this directory is
the roadmap they follow.

    cp .env.example backend/.env
    cd backend && python -m venv .venv && . .venv/Scripts/activate
    pip install -r requirements.txt
    uvicorn app.main:app --reload --port 8000

    curl http://127.0.0.1:8000/api/health
