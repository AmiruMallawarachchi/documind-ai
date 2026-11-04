# Decisions

One line per real choice, and the reason. This is the answer key for "walk me through your
architecture" — and the fastest way to reconstruct why a file looks the way it does after the code has
moved on. Ordered by layer, not by date: the interesting ones are the trade-offs, so each entry names
what was given up.

## Orchestration

| Decision | Why | What it cost |
|---|---|---|
| LangGraph `StateGraph` over four chained function calls | The critic→synthesis edge is a **cycle**; a chain needs recursion plus a hand-rolled counter, and `stream_mode="updates"` gives per-node progress for free | A dependency, and a state schema to maintain |
| Revision ceiling `MAX_REVISIONS = 2`, enforced in `should_revise` | The first rewrite fixes a real defect, a second is usually the critic and the model talking past each other, and a third spends Groq quota without changing the answer | A genuinely hard-but-wrong answer still ships after two passes |
| Put the loop bound in the routing function, not the critic | One place owns the ceiling, so editing the graph cannot silently remove it | A second file to read when tracing the loop |
| Typed `AgentState` (`TypedDict, total=False`) | Nodes update a subset of keys; `total=False` says so, and a typo becomes an error at the node that introduced it instead of a missing value at the far end | Slightly awkward `state.get(...)` everywhere |
| `trace` is `Annotated[list, operator.add]` | LangGraph's default channel overwrites, so every node would erase the history and the UI would show only "finalise" | One non-obvious annotation to explain |
| Chunks travel through state as plain dicts, not pydantic models | LangGraph merges state by key; swapping a model instance for a dict mid-chain is a subtle bug class, so the conversion happens once at the prompt boundary | Two representations of the same thing, deliberately |
| Compiled graph cached with `lru_cache(maxsize=1)` | A per-request compile rebuilds the node registry for no benefit, and the free tier's 0.1 CPU makes that measurable | No per-request graph variation (not needed) |

## Retrieval and grounding

| Decision | Why | What it cost |
|---|---|---|
| Filter `owner_id` inside the Pinecone query, not after | Cross-user leakage is a correctness bug, not only a privacy one; a caller cannot forget a filter that is built into the service | One less flexible ad-hoc query path |
| Cosine score floor 0.18 instead of always returning `top_k` | Six chunks where three score well is three pieces of evidence plus three distractions the model will happily cite | Recall drops on genuinely thin corpora |
| Collapse exact duplicate chunks after search | Overlap seeding puts a boundary sentence in two consecutive chunks; retrieving both wastes a slot the user paid for | A cheap O(n) pass, and no fuzzy dedupe |
| Numbered `[1] [2]` passages in the prompt, citations generated from the same list in the same module | The model needs something to point at, and a citation off by one is worse than none — it points at a passage that does not support the claim | Prompt looks annotated, and the model must be told to use the markers |
| Zero hits short-circuit to `finalise` | Asking a model to answer with no evidence buys a fluent, unsourced guess — and two Groq calls to produce it | "Not found" is a distinct code path, not a model output |
| Chunk ~1000 chars with 150 overlap | An answer sitting on a boundary would otherwise be split into two unusable halves | Duplicate text, which retrieval must collapse |
| Local `all-MiniLM-L6-v2` embeddings | $0 per chunk and no rate limit on the step that runs once per chunk, while Groq throttles everything else | Weaker than a hosted 1536-dim model; stated in Known limitations |
| Documents and queries embed through one module | Vectors from different models live in unrelated spaces and the cosine between them means nothing | A startup check that raises if dims disagree with config |
| Chunk text stored in Pinecone metadata, not Mongo | Atlas M0 is a hard 512 MB cap, and Pinecone needs the text attached anyway to build a citation without a second lookup | Two stores to keep in sync on delete |

## Agents and prompts

| Decision | Why | What it cost |
|---|---|---|
| `temperature=0.2` for synthesis, `0.0` for router and critic | The task is "say what the documents say and cite it"; creativity here is hallucination, and a hotter draft gets rejected — wasting the call that produced it | Stiff prose, deliberately |
| Unparseable critic output counts as **pass** | Failing on garbage would spin the revision loop into the same unreadable response and burn quota | A silent pass when the critic misbehaves |
| Router's tolerant `parse_route` (scan for a known word, default `search`) | Instruction-following models answer "LABEL: SEARCH", "search", and "Route: SEARCH - needs the contract" | A misrouted turn is possible; the search path handles it |
| Every agent absorbs its own LLM failure | One rate-limited call should not surface as a 500 to a user who asked a reasonable question | An outage looks like a degraded answer rather than an error page |
| `NOT_FOUND_IN_CONTEXT` sentinel detected and stripped in `finalise` | Leaking the sentinel turns an honest refusal into an unreadable protocol error | A magic string that both prompt and code must agree on |
| Answer buffered, then replayed in ~6-word frames | The critic must read the whole draft before any of it streams | Several seconds of first-token latency, mitigated by `status` frames |
| Per-node `status` frames | Verification means seconds of work before the first token; an unlabelled wait reads as a hang | Two more event types in the wire contract |
| `agent_trace` persisted with the message | "The critic rejected the draft twice" is the information you need when an answer looks evasive — and it is the difference between debugging and guessing | Storage, and a UI disclosure to build |

## Hardening

| Decision | Why | What it cost |
|---|---|---|
| Hand-rolled sliding-window limiter instead of `slowapi` | The rule is three lines and a deque; a dependency would also have had to learn that chat is per-owner and login is per-address | Per-process state: a second worker doubles the allowance |
| JWT (`PyJWT`, HS256) with `sub = owner_id` | The owner id must not be client-forgeable, and a signed token with `exp` is the smallest thing that guarantees it | A secret to manage, and a login round trip |
| `hmac.compare_digest` for the access code | Constant-time compare costs one line and removes a timing question from the review | Nothing |
| `JWT_SECRET` refused at boot under 32 chars | RFC 7518 minimum for HS256; PyJWT warns on every call otherwise | A confusing first boot if you paste a short secret |
| `X-Demo-Key` accepted only outside production | Keeps local work frictionless without shipping an unauthenticated identity path to the deployed demo | Two code paths in one dependency |
| Access log is **pure ASGI**, not `BaseHTTPMiddleware` | `BaseHTTPMiddleware` wraps the response body in its own stream — exactly wrong between a chat endpoint and an SSE stream that must flush frame by frame | Hand-written scope/receive/send plumbing |
| `request_id` in a `ContextVar`, not a parameter | The call sites that want it are twenty frames below the middleware; none should take a logging parameter | ContextVars are copied into Starlette's threadpool — verified, not assumed |
| Client-supplied `X-Request-ID` bounded to 64 chars | Not a secret, but a caller could otherwise stuff a kilobyte into every log line and back out of the response | Long trace ids get truncated |
| `/api/health` never touches a dependency; `/api/health/deep` does | Render restarts a container that fails its health check, so an Atlas blip must not become a restart loop | Two endpoints, and one is unauthenticated (up/down only, no versions or names) |
| Deep health reports the exception **class**, never its message | pymongo and Pinecone both put the connection string — with the password — into their exception text | Slightly less detail when debugging |
| CORS allowlist from config, never `*` | A wildcard lets any website in the world spend your Groq and Pinecone quota on a stranger's behalf | One more env var to set correctly at deploy |
| Extension + size validation before parsing | A hostile PDF should cost nothing but the request | Two rejections to explain in the UI |
| Rate limit enforced before reading upload bytes | Otherwise the limit only bounds the damage after the damage | — |

## Frontend

| Decision | Why | What it cost |
|---|---|---|
| One `lib/api.ts` owns every fetch | Auth headers, error unwrapping, and SSE decoding live in one reviewable file instead of per component | A slightly fat module |
| Manual SSE body reader, not `EventSource` | `EventSource` only does GET and the question must travel in a body | Buffering, frame-splitting, and a trailing-frame check |
| Session in a `useSyncExternalStore` over `localStorage` | Avoids `setState` in an effect and the hydration flash a naive version produces | A store module and a `SESSION_PENDING` server snapshot |
| `Workspace` keyed on `ownerId` | Switching owners must not leave the previous corpus on screen | A remount on sign-in (which is the point) |
| 401 anywhere clears the session and shows the gate | A wall of failing requests is worse than one honest "sign in again" | A global side effect in the error path |
| Inline `process.env.NEXT_PUBLIC_*`, never destructured | Next only inlines literal member expressions into the client bundle | Repeated spelling of the same variable |
| Access code held only in component state | It is not a secret and should not outlive the tab | Retyped after a hard reload of the gate |

## Deployment and dependencies

| Decision | Why | What it cost |
|---|---|---|
| Monorepo, one repo for both apps | Simplest to manage solo; `render.yaml` and Vercel's root-directory setting both handle it | Two toolchains in one `git status` |
| Multi-stage Docker, `python:3.11-slim` | The builder layer keeps pip's cache and build tools out of the runtime image | Two stages to reason about |
| Pin `torch==2.15.0+cpu` from PyTorch's CPU index | The default wheel drags ~2 GB of CUDA runtime into a container that will never see a GPU; 512 MB RAM is the binding constraint | A pin that must be re-checked when sentence-transformers is upgraded |
| Bake the embedding model into the image | Otherwise the first question after every cold start spends seconds and ~100 MB of egress downloading MiniLM — and a "no network to HF" failure becomes a failed build instead of a container that never answers | A larger image (~100 MB) and a slower build |
| Non-root container user, `PYTHONUNBUFFERED=1` | The container should not be able to write its own layers; and block-buffered stdout makes Render's JSON log stream lag | Two extra Dockerfile lines |
| `.dockerignore` with context = `backend/` | Docker tars the whole context before the first RUN; a root-wide context would upload `frontend/node_modules` on every build | One more file to keep honest |
| `render.yaml` with every secret `sync: false` | The deployment shape becomes reviewable source; a Blueprint with a literal key in it is the same mistake as committing `.env`, applied automatically to every copy | Values still set by hand, once |
| Accept Render cold starts; do not cron-ping | Being explicit about a real constraint beats hiding it, and a keep-alive cron burns the 750 free instance-hours | ~60 s first request after idle, with UI copy that says so |
| Pins copied from the working venv, not guessed | "Works locally, dead on Render" is usually a drifted transitive dependency | Manual re-pinning on upgrades |
| Model name in `GROQ_MODEL`, default `qwen/qwen3.8-27b` | Groq retired the `llama-3.1-8b-instant` this was built against, so a hard-coded pin became a provider error on every call; and reasoning models (`openai/gpt-oss-*`) are not substitutes — they bill hidden thinking tokens against `max_tokens`, and the router asks for 48, so it answers `''` and loses document scoping | A default that will itself age out, and a test to keep the pin in config |
| `PyJWT` listed explicitly | A direct import that arrives only as somebody else's transitive dependency is how a deploy one day cannot find `jwt` | One more pin |
| pytest and ruff in `requirements-dev.txt` only | Test tooling must not ride into an image with a 512 MB RAM budget | Two requirement files |

## Testing

| Decision | Why | What it cost |
|---|---|---|
| Mock the LLM, never the pipeline | LLM output is non-deterministic and costs quota; everything else is deterministic and free to test | A fake model to maintain |
| A separate `test_llm_retry.py` with a model that *fails* | The agent tests need a model that answers; the interesting rules are in the failure paths — which statuses retry, how long the user waits, and never restarting a stream that already showed words | A scripted stub per failure shape |
| Assert the **query shape** against fakes that record calls (`test_vector_store`, `test_metadata_store`) | An in-memory fake store never notices a missing `owner_id` filter — which is the one bug class that leaks another user's documents | Tests that break when a filter is legitimately restructured |
| Tests run in `development` mode by default; `test_auth.py` flips it per test | Keeps the 73 older tests on the `X-Demo-Key` path while production-mode rules get real coverage | Two mode conventions in one suite |
| An autouse fixture resets the limiter between tests | A shared `deque` would make unrelated tests fail depending on execution order | One more fixture to understand |
| `time.sleep` patched in backoff tests | Three exponential waits would otherwise hang the suite on real delays | Waits asserted by recorded value, not by elapsed time |
