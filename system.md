# DocuMind AI — Build & Deploy Plan

Oct 27, 2026 · @Amiru Umavin Mallawa Arachchi

A LangGraph multi-agent document-intelligence platform (FastAPI + Pinecone + MongoDB + Next.js 15 + SSE streaming), built in stages on free-tier infrastructure only, ending with a live, publicly deployed app plus a clear record of what each stage taught you.

## Scope — what v1 actually does

DocuMind AI v1 is a document Q&A and synthesis app: a user uploads PDF/DOCX/TXT files, then asks questions in a chat interface and gets a **streamed, cited answer** drawn from one or more of their documents. The multi-agent part is the point of the project — it's what separates this from a plain RAG tutorial — so the pipeline is a LangGraph graph with distinct agents (router, retrieval, synthesis, critique) rather than one prompt-and-retrieve function. That's what you'll be able to explain in an interview or a viva: not just "I built RAG," but "I built an orchestrated multi-agent system with verification."

**In scope for v1:**

- Upload and parse PDF, DOCX, and TXT (no OCR — scanned/image-only PDFs are out of scope)
- Chunk, embed, and index documents per user
- Multi-turn chat with streaming (SSE) responses and inline citations back to source chunks
- Cross-document reasoning ("compare these two contracts" style queries spanning several uploaded files)
- A simple auth layer (single-user API key or lightweight JWT) so the demo isn't a public, unmetered door to your free-tier quotas
- A clean Next.js UI: upload, document list, chat

**Deliberately cut from v1** (name these as "future work" in your README — it reads as judgment, not as gaps):

- OCR for scanned documents
- Multi-tenant teams/workspaces, billing, roles
- Audio/video ingestion
- Fine-tuning or custom models
- Horizontal scaling / multi-region — a single free-tier instance is the explicit target

## Architecture

&#91;embedded content: architecture · 6 components, 1 backend hub\]

Every chat turn flows from the browser through one FastAPI backend, which hands it to the LangGraph orchestrator; the orchestrator is the only thing that talks to Groq, Pinecone, and MongoDB — none of the three external services ever see the frontend directly.

## Multi-agent design

&#91;embedded content: LangGraph flow · 4 agents, 1 critique loop\]

The graph is a `StateGraph` with shared state (the user's question, retrieved chunks, draft answer, citation list, and a retry counter capped at 2). The critique agent is what makes this a multi-agent system rather than a RAG chain with extra steps: it checks the draft against the retrieved chunks before anything reaches the user, and can send it back for one more pass.

## Tech stack and free-tier limits

Every piece below has a real, no-card-required free tier as of late October 2026. The two most likely to pinch first are marked.

| Layer | Service | Free tier | Watch out for |
| --- | --- | --- | --- |
| LLM inference | **Groq** (Llama 3.1/3.3 or gpt-oss, OpenAI-compatible API) | Free, rate-limited per model — roughly 30 requests/min and a per-model daily token cap (smaller models like 8B get a higher daily cap than 70B-class ones); check your live limits at console.groq.com/docs/rate-limits | A 4-agent chain can burn request-per-minute limits fast — add backoff/retry, and keep Google AI Studio (Gemini Flash) as a manual fallback key |
| Embeddings | **sentence-transformers** (e.g. `all-MiniLM-L6-v2`) run locally inside FastAPI | $0, unlimited, no API | Slightly weaker quality than hosted embedding models, but zero rate-limit risk — the right trade for a free-tier build |
| Vector DB | **Pinecone** Starter | 2 GB storage, 2M write units/mo, 1M read units/mo, 5 indexes, 5M embedding tokens/mo (if you use their hosted embedding models instead of local ones) [\[source\]](https://www.pinecone.io/pricing/) | ⚠️ Re-embedding the same documents repeatedly burns write units fast — chunk once, cache the embedding |
| Document DB | **MongoDB Atlas** M0 (free forever) | 512 MB storage, shared RAM, \~100 ops/sec, no automated backups [\[source\]](https://www.mongodb.com/pricing) | ⚠️ Likely the first hard ceiling — store only metadata + chunk pointers in Mongo, not full document text |
| Backend hosting | **Render** free Web Service (Docker) | 750 instance-hours/month per workspace, 512 MB RAM, spins down after 15 min idle, \~60s cold start on wake [\[source\]](https://render.com/docs/free) | Cold starts are normal on free tiers — design the UI to show a "waking up the server" state rather than fighting it |
| Frontend hosting | **Vercel** Hobby plan | Generous free builds/bandwidth for Next.js 15 | Hobby plan is for personal/non-commercial projects — fine for a portfolio piece |
| CI/CD | **GitHub Actions** | Free for public repos | — |

**Why Groq over Gemini for the LLM:** Google cut Gemini's free-tier rate limits sharply in April 2026 (down to roughly 5 requests/minute on the free tier), which is too tight for a multi-agent chain that fires several LLM calls per user question. Groq's free tier is currently the more workable option for this project; treat any exact numbers above as approximate and re-check the provider's own limits page before you build around them, since free tiers change without much notice.

## Data model

**MongoDB Atlas (metadata + conversation state — never full chunk text, to protect the 512 MB cap):**

| Collection | Key fields | Purpose |
| --- | --- | --- |
| `users` | `_id`, `email`, `hashed_password`, `created_at` | Auth |
| `documents` | `_id`, `owner_id`, `filename`, `status` (uploading/parsing/indexed/failed), `page_count`, `chunk_count`, `uploaded_at` | Tracks each uploaded file |
| `conversations` | `_id`, `owner_id`, `title`, `created_at` | Groups messages into a chat thread |
| `messages` | `_id`, `conversation_id`, `role` (user/assistant), `content`, `citations` (array of `{document_id, chunk_id, page}`), `agent_trace` (which agents ran, in order), `created_at` | Full chat history + the receipts for each answer |

**Pinecone (one index, e.g. `documind-chunks`):**

| Field | Type | Purpose |
| --- | --- | --- |
| vector id | string | `{document_id}_{chunk_index}` |
| `owner_id` | metadata (string) | Filters retrieval to one user's documents |
| `document_id` | metadata (string) | Filters retrieval to specific document(s) for cross-document queries |
| `chunk_text` | metadata (string) | The actual chunk text (Pinecone metadata can hold it — this is also how you avoid storing chunk text in Mongo) |
| `page_number` | metadata (int) | Powers the citation shown to the user |

Filtering by `owner_id` (and optionally `document_id`) on every query is what keeps one user's documents out of another user's answers — build that filter in from day one rather than retrofitting it.

## Build roadmap

Eight phases, each with a working deployed artifact at the end — not eight phases of local code followed by one scary deployment at the finish. Deploy the skeleton in Phase 0 and keep it live throughout; that turns deployment from a final unknown into a non-event.

| Phase | Deliverable | What you're learning | Rough time |
| --- | --- | --- | --- |
| 0. Scaffold | Repo, FastAPI + Next.js hello-world, Mongo + Pinecone connected, `/health` route live on Render + Vercel | Project structure for a full-stack AI app, env/secrets management, Docker basics | 2–3 days |
| 1. Ingestion | Upload endpoint → parse (PyMuPDF/pdfplumber/python-docx) → chunk → embed → upsert to Pinecone + metadata to Mongo | Chunking strategy, embeddings, vector upsert | 4–5 days |
| 2. Single-agent RAG baseline | One LangGraph node: retrieve + generate with citations, SSE streaming to the frontend | LangGraph basics, SSE, grounding answers in retrieved text | 4–5 days |
| 3. Multi-agent orchestration | Router → Retrieval → Synthesis → Critique as a LangGraph `StateGraph`, with conditional edges (critique can send it back to synthesis) | Shared state schema, conditional routing, agent handoff | 6–7 days |
| 4. Frontend | Chat UI with streaming render, citation display, upload + document list | Next.js 15 App Router, consuming SSE client-side, UI state | 6–7 days |
| 5. Hardening | Structured logging, retry/backoff on Groq rate limits, API-key or JWT auth, file-size/type validation | Production patterns for an LLM-backed API | 3–4 days |
| 6. Testing | Unit tests for chunking/retrieval, one integration test for the full agent graph (mock the LLM calls), 2–3 end-to-end smoke tests | Testing a non-deterministic pipeline without hitting real APIs in CI | 3–4 days |
| 7. Deploy + document | Final deploy pass, README with architecture diagram, `ARCHITECTURE.md` explaining the agent graph, short demo video | CI/CD, cloud env config, writing for a technical reader | 3–4 days |
| 8. Portfolio polish (stretch) | Write-up for Amiru AI Lab / HuggingFace, add to CV and portfolio site | Communicating the project to a non-reader of the code | ongoing |

Total: roughly **6–8 weeks** part-time around coursework. Treat the week numbers as a budget, not a deadline — Phase 3 (multi-agent orchestration) is the one most likely to run long, since it's the part with no tutorial to copy.

## Repo and environment setup (Phase 0, in order)

1. **Create accounts first, before any code:** Groq console, Pinecone, MongoDB Atlas, GitHub (you likely have these), Render, Vercel. Note every API key/URI somewhere safe as you go.
2. **Repo layout** — one monorepo is simplest to manage solo:

```
documind-ai/
backend/ # FastAPI
app/
main.py
core/config.py # env var loading
routers/ # upload.py, chat.py, health.py
agents/ # router_agent.py, retrieval_agent.py, synthesis_agent.py, critique_agent.py, graph.py
services/ # pinecone_service.py, mongo_service.py, embedding_service.py
models/ # pydantic schemas
Dockerfile
requirements.txt
frontend/ # Next.js 15
.gitignore # must include .env, __pycache__/, node_modules/
README.md
```

3. **Backend init:** Python 3.11+, a virtual environment, `pip install fastapi uvicorn pymongo pinecone-client langgraph langchain-groq sentence-transformers python-multipart pypdf python-docx`.
4. **Frontend init:** `npx create-next-app@latest frontend --typescript --tailwind --app`.
5. **`.env.example`** at the repo root listing every key the app needs, with placeholder values — `GROQ_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `MONGODB_URI`, `JWT_SECRET`, `CORS_ORIGINS`. The real `.env` is never committed.
6. **Create the Pinecone index and the Mongo M0 cluster** now, in their consoles, so the connection strings exist before you write a line of ingestion code.
7. **First commit:** a `/api/health` route returning `{"status": "ok"}` — nothing else. Get this deployed to Render and the frontend deployed to Vercel before building any feature. This is the single highest-leverage step in the whole plan: once the pipe from your laptop to a public URL exists and works, every later phase is just filling it, instead of a cliff you face at the end.

## Deployment plan

| Component | Platform | Steps |
| --- | --- | --- |
| Backend (FastAPI) | Render Web Service, Docker | Push a `Dockerfile`; connect the GitHub repo in Render; set start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`; add all env vars in Render's dashboard (never in the Dockerfile) |
| Frontend (Next.js 15) | Vercel | Import the GitHub repo; set `NEXT_PUBLIC_API_URL` to the Render backend URL; auto-deploys on every push to `main` |
| Vector DB | Pinecone | Create the index once (serverless, AWS `us-east-1` on the free tier); same API key goes into both your local `.env` and Render's env vars |
| Document DB | MongoDB Atlas M0 | Create the cluster and a DB user with a strong password; under Network Access, allow `0.0.0.0/0` for simplicity on a free-tier solo project — this is a real trade-off (the DB is reachable from any IP if the connection string ever leaks), so treat the URI as seriously as a password and never commit it |
| Domain + SSL | Default subdomains | `*.onrender.com` and `*.vercel.app` both come with free SSL — no need to buy a domain for a portfolio project |

**The free-tier cold-start trade-off:** Render's free web service spins down after 15 minutes of no traffic and takes about a minute to wake back up. Two honest options, not a "fix":

1. **Accept it and design for it** — show a "waking up the demo server…" state in the UI on the first request. This is the more production-minded choice: you're being explicit about a real constraint rather than hiding it.
2. **Keep it warm with a scheduled ping** (e.g. a GitHub Actions cron hitting `/health` every 10 minutes) — this avoids cold starts for visitors, but it burns through your 750 free instance-hours/month faster and isn't "free" in the hours sense, so decide deliberately rather than defaulting to it.

Either way, say which one you picked and why in the README — that line alone signals you understand the infrastructure, not just the AI part.

## Production-readiness checklist

Be precise about what "production-ready" means here: this is production-ready **for a free-tier portfolio demo** — correct, hardened against obvious abuse, and honest about its limits — not production-ready for paying users at scale. That distinction, stated plainly in your README, reads as maturity rather than as a shortcoming.

- [ ] File upload validated: type allowlist (PDF/DOCX/TXT) and a size cap (e.g. 10 MB) before it ever reaches parsing
- [ ] Rate limiting on your own API (e.g. `slowapi`) — this protects your Groq/Pinecone free-tier quota from being burned by one bad client as much as it protects your server
- [ ] Structured logging (not `print()`) with a request ID per chat turn, so a failed agent run is traceable after the fact
- [ ] Retry-with-backoff around Groq calls for rate-limit (429) responses, rather than letting a user-facing request just fail
- [ ] `.env` in `.gitignore` from the first commit; a quick `git log -p | grep -i key` (or a tool like `gitleaks`) before your first push to confirm nothing leaked
- [ ] CORS locked to your actual Vercel domain in production, not `*`
- [ ] A simple auth layer (API key header or JWT) so the public demo URL can't be used by strangers to drain your free-tier limits
- [ ] `/health` endpoint, and a status badge or line in the README so a visitor (or you, three months later) can tell at a glance if it's up
- [ ] README with an architecture diagram, setup steps, and a named "Known limitations" section
- [ ] A short `ARCHITECTURE.md` walking through the agent graph — this doubles as material for your dissertation/CV and for defending design choices in an interview or viva

## Working with your AI coding tool (and still learning)

Using an AI coding tool and actually learning the stack aren't in tension if you control the unit of work you hand it:

- **Feed it one phase at a time**, not the whole plan. Paste the relevant section above (e.g. Phase 1's deliverable + the data model) and ask it to implement just that. A diff you can actually read beats a finished app you can't explain.
- **Review every file before accepting it.** If it writes something you don't recognize — a LangGraph pattern, a Pinecone call, a Pydantic validator — stop and ask it to walk you through that one piece before moving on. That pause is where the learning happens, not in reading the final code.
- **Ask "why," not just "what."** "Why a `StateGraph` instead of a plain function chain here?" or "why filter on `owner_id` at the Pinecone level instead of after retrieval?" gets you the reasoning you'll need to defend this project later — in a viva, an interview, or your own head in six months.
- **Keep commits scoped to one phase or feature** so `git diff` stays small enough to actually read, and so a bad generation is easy to isolate and roll back.
- **Keep a `DECISIONS.md`** — one line per real choice and why ("chose LangGraph over a plain chain for explicit agent handoff and conditional retry"). This becomes your answer key for "walk me through your architecture" questions, and it's the fastest way to reconstruct your own reasoning after the AI tool has moved on to the next phase.
- **Treat a working-but-unexplained piece of code as unfinished.** If Phase 3's graph runs but you can't say why the critique agent routes back to synthesis instead of ending, that's the signal to stop and understand it before Phase 4 builds on top of it.

## Sources

Free-tier figures above are as of late October 2026 and change without much notice — re-check before you build around an exact number.

- [Pinecone pricing](https://www.pinecone.io/pricing/)
- [MongoDB Atlas pricing](https://www.mongodb.com/pricing)
- [Render free tier docs](https://render.com/docs/free)
- [Groq rate limits docs](https://console.groq.com/docs/rate-limits) — check your own account's live limits here before designing around a specific number

Ready-to-paste build prompts for Antigravity, phase by phase: Antigravity Prompts
