# Your RAG app is lying to you. Here's how I made mine prove it isn't.

**Building DocuMind AI: a document Q&A product where a critic agent reads every answer before you do — on a stack that costs $0 a month, and every bug I hit on the way.**

*Live demo: [documind-kappa-indol.vercel.app](https://documind-kappa-indol.vercel.app) · Source: [github.com/AmiruMallawarachchi/documind-ai](https://github.com/AmiruMallawarachchi/documind-ai) · 445 tests, no credentials required to run them*

---

A retrieval-augmented generation app is easy to demo and hard to trust.

The demo is twelve lines. Embed your documents, embed the question, take the nearest six chunks, paste them into a prompt, stream the answer. It works on the first try. Everyone's does.

Then you upload a contract and ask "what is the termination notice period?" and get back a confident, well-formatted, completely unsupported paragraph — because the passage that answered it was seventh, or the chunk boundary cut the clause in half, or the model simply wrote something plausible over the top of what it was given. The app cannot tell the difference. Neither can you, unless you go and read the source yourself, which is the work you were trying to avoid.

That is the actual problem with RAG as a product: not that it can't retrieve, but that it has no idea whether it said something true.

So I built DocuMind AI around one stubborn idea: **nothing reaches the browser until a separate agent has checked it against the retrieved passages.** A critic reads the whole draft, marks claims it can't support, and can send the draft back for one rewrite. Only then does the text stream out.

This is the story of that system — the architecture, the five bugs that mattered, and the honest list of what's still wrong with it. Every number below is measured, and the whole thing runs on permanently free tiers.

---

## What it actually does

Upload PDFs, DOCX or TXT. Ask a question. Four LangGraph agents do the work:

1. A **router** decides whether the question needs retrieval at all, is off-topic, or is unsafe.
2. A **retrieval agent** queries Pinecone, filtered to your documents, drops weak matches, and numbers the surviving passages.
3. A **synthesis agent** drafts an answer from those numbered passages, with `[1]`, `[2]`-style citations.
4. A **critic** checks every claim in the draft against the passages it was given. Fail, and the draft goes back for one rewrite (two at most).

Citations become clickable chips in the UI that open the exact page of the exact file the claim came from. Threads are saved, renameable, pinnable, deletable — and shareable as a read-only link that shows the prose but never the documents.

That last part matters more than it sounds, and I'll come back to it.

---

## The shape of the system

```
 Browser ──> Next.js 16 on Vercel ──> FastAPI on Render (Docker)
                    │                          │
               SSE frames              LangGraph StateGraph
             (meta/citations/           router → retrieval → synthesis
              status/token/done)        │            ↑           │
                          direct ───────┘            └── critique ┘
                                                           │
                                     ┌─────────────────────┴──────┐
                                     ▼                            ▼
                              Pinecone (chunks)            MongoDB Atlas
                              + local embeddings       (metadata, conversations)
```

Four layers, each with one job it doesn't share:

| Layer | Owns | Never owns |
|---|---|---|
| `frontend/` | rendering, streaming into the DOM, which documents are in scope | retrieval logic, trust decisions |
| `routers/` | HTTP contract, auth, rate limiting, SSE encoding | prompt text, agent ordering |
| `agents/` + `services/` | the graph, prompts, retrieval rules, ingestion | transport concerns |
| Pinecone + Atlas | vectors and chunk text / metadata and history | business rules |

The split that saved the most time is **routers vs agents**. An agent returns a dict; a router decides how that dict reaches the browser. Swapping LangGraph for a plain function chain, or SSE for WebSockets, would each touch exactly one layer.

**Stack:** Python 3.11 + FastAPI on Render's free Docker tier; Next.js 16 App Router + React 19 + Tailwind 4 on Vercel Hobby; Groq for generation; `all-MiniLM-L6-v2` (384 dims) running in-process for embeddings; Pinecone Starter for vectors; Atlas M0 for everything else. No credit card anywhere.

---

## Ingestion: the boring part that decides answer quality

The pipeline is ordinary. The sequencing is not.

`POST /api/upload` validates the extension and a 10 MB cap *before* reading a byte — a malicious PDF costs the request and nothing else. It inserts a `status='uploading'` row *before* parsing, so a crash leaves a visibly stuck document instead of a vanished one. It returns `202` immediately; parsing, chunking, embedding and upserting happen in a background task, because on 0.1 CPU a long PDF takes minutes and no HTTP client should wait for it.

Then:

- **Chunk at ~1000 characters with 150 characters of overlap.** Overlap exists so a clause that straddles a boundary isn't cut into two useless halves. It also manufactures near-duplicate chunks that retrieval then has to collapse — a cost I accepted knowingly.
- **Embed locally.** Documents and queries go through the same module. Two embedding models produce vectors in unrelated spaces, and the cosine score between them is noise wearing a number.
- **Write twice, deliberately.** Chunk text, page number and filename live in Pinecone's metadata, because retrieval needs the text attached to build a citation without a second lookup. Counts and status live in Atlas, which stores *no document body at all* — M0 is a hard 512 MB.
- **Re-indexing deletes first.** A re-upload must not leave a stale chunk behind that can later be cited as if it were current.

Every page keeps its page number through the whole pipeline, because that number is what a citation points at. A citation that opens the wrong page is worse than no citation; it teaches the user to distrust the feature.

---

## The graph, and the trade I made on purpose

Six nodes, deployed as a LangGraph `StateGraph`:

```
router ──search──> retrieval ──hits──> synthesis ──> critique
   │                   │                                 │
   │                   └──0 hits──> finalise <──pass─────┘
   └──chat/unsafe/clarify──> direct                    │
                                    critique ──fail──> synthesis  (max 2 rewrites)
```

Two things here are worth defending.

**Why a graph and not four chained calls.** Because `critique → synthesis` is a *cycle*. Chaining it means recursion plus a hand-rolled counter; a conditional edge says it directly, and the counter lives in shared state. The second reason is observability: `stream_mode="updates"` yields each node's delta the moment it commits, which is what lets the UI say "Searching your documents" instead of spinning at you for five seconds.

**Why the bound lives in `should_revise`, not in the critic.** So that it cannot be forgotten when the graph is edited. Two revisions, because the first rewrite fixes a real defect, the second is usually the critic and the model talking past each other, and a third burns quota without changing the answer.

And then the trade that defines the product:

> The critic needs the whole draft before it can verify it, so **nothing streams during synthesis.** Once verified, the finished text is replayed in ~6-word frames.

The user still watches the answer type itself out. What they never watch is an unverified claim. Verification costs first-token latency — several seconds on a multi-agent turn — and that is not a bug to be optimised away later. It *is* the feature. It's also why per-node `status` frames exist at all: an unlabelled five-second wait reads as a hang, and a user who thinks the app hung doesn't care that the answer was going to be accurate.

Two more small rules that paid for themselves:

- **Every agent degrades alone.** A rate-limited router still searches. An unparseable critic verdict counts as *pass* — failing on garbage would spin the loop and burn quota. A dead model produces a readable sentence, not a 500.
- **"Not found" is an answer.** Zero hits short-circuits to a final message instead of spending two more model calls to buy a fluent guess.

---

## Six bugs, and what each one taught me

This is the part I'd want to read if I were hiring. Five of the six came from looking at a real running system — a browser tab, a deploy log, or a measurement — and exactly one came from a unit test. None came from a tutorial.

### 1. CORS: the bug no backend test can catch

The conversation rows gained a rename button. The endpoint existed, had six tests covering owner scoping and query shape, and every one of them passed. In a browser, the rename button failed instantly with one honest sentence: *"That did not reach the server."*

The backend was fine. The request never arrived.

```
OPTIONS /api/conversations/{id}  ->  400 "Disallowed CORS method"
```

`PATCH` was missing from the server's `allow_methods` list. And here's the part that stings: **`TestClient` calls routes directly and never asks permission first.** A browser does. It sends a preflight naming the verb it intends, and the server answers from a list a human typed. No amount of route testing can catch a verb the browser isn't allowed to send, because the test skips the only step where the list matters.

The fix was one word. The commit was not, because the same mistake sat one route away from happening again. `tests/test_cors.py` now preflights every verb `frontend/lib/api.ts` uses, on the path it uses it on — the list is checked against the client rather than against each author's memory. It also keeps a `PUT` preflight at 400 and an unknown origin without an `Access-Control-Allow-Origin`, so "just make it `*`" is not a way to make the test green.

**Lesson:** for every boundary between two systems, ask which side your tests are standing on. Mine were standing on the wrong side.

### 2. The citation markers were rendering as `[1`

Every answer in the product carried its sources as broken text:

```
The document is a Q3 Vendor Agreement [1.
```

The `]` was gone, no chip appeared, and the click-through to the passage didn't exist. The type check passed. ESLint passed. `next build` passed. Clicking through the app passed — because **the sentence still reads.** It looks like a slightly odd citation style, not a defect.

Two regexes were wrong. `String.split` hands back exactly what a capture group matched, and the group ended after the digits — so the parser received `[1`, `[2][3`, `[10`, none of which its full-marker test recognised. The bracket the split consumed never came back. And when the marker *was* recognised, `[2][3]` matched, the group held `2][3`, and `split("]")` produced one real chip and one labelled **"Go to source NaN."**

The old parser was only ever correct for a single marker — which is exactly the shape local testing produces. A unit test with a chained citation caught it in 15 milliseconds.

**Lesson:** this is the class of defect that survives every green check you own. If a broken feature still renders as readable prose, only an assertion about the output catches it.

### 3. The router didn't know what "this document" meant

A user with one file selected asked *"what is this document?"* and got back:

> "I am not sure which part of your documents you mean."

Two failures in one exchange. The router prompt only ever saw the question — `AgentState` carried the `document_ids`, but the routing call passed the string and nothing else. To a classifier, a demonstrative with no visible referent *is* the "ambiguous" case its system prompt describes. It was behaving correctly on the wrong information.

Then the answer parser compounded it: it scanned the whole model reply for any route word, so `REASON: user wants to search` in a *clarify* verdict parsed as `search`. Worse, it iterated a `set`, so identical replies could route differently in two different processes.

Now the scoped filenames travel with the question, and `parse_route` reads the `LABEL:` line on its own, matches whole words (`research` is not `search`), and takes the earliest hit.

### 4. Cosine similarity hates whole-document questions

With the routing fixed, "what is this document?" became a search — and still answered *"I could not find anything about that in your indexed documents."* Which was true, and useless.

A question about a document as a whole shares no vocabulary with any passage inside it. That is precisely the signal cosine similarity reads. Measured against the very file being asked about, the query scored **0.077**. "Summarise it" scored **0.032**. Both fell under the 0.18 floor, both were correctly called noise by a rule designed for a different *shape* of question.

So retrieval makes one more attempt when the floor produced nothing **and the scope is narrow**: rank again with no threshold, same `top_k`, same de-duplication. The owner and document filters are untouched, so the retry cannot reach outside the scope the caller already authorised. With a library in scope, zero hits genuinely does mean "not in your documents" and the floor stays where it is.

**Lesson:** a threshold encodes an assumption about what a good question looks like. Find that assumption before you tune the number.

### 5. `await params` built fine and served a page that never loaded

The newest feature — sharing a conversation — had a page problem that looked like nothing.

The first version did the obvious server-side thing:

```tsx
export default async function SharedConversationPage({ params }) {
  const { token } = await params;      // inside <Suspense>, as the build asked for
  return <SharedThread token={token} />;
}
```

It type-checked. It linted. It built. The route table even labelled it `◐` Partial Prerender, which sounds like success.

In a browser, the page sat on "Loading this thread…" forever. The network log had **no request to the API at all** — not a failure, not a timeout, nothing. The served HTML contained a `<!--$?-->` placeholder: the static shell Next.js is allowed to send before its runtime data arrives. Nothing ever arrived. The boundary stayed suspended, so the client component never mounted, so it never fetched.

The fix was to stop resolving the token on the server at all. The page takes no props; the component reads the token with `useParams()` and fetches. A shared thread is read by a browser that has to fetch it anyway — there was never a server-side reason to know the token first. (`/verify` in the same repo had read its identifier client-side all along, for the same class of reason. The pattern was already there; `await params` just *looked* equivalent.)

**Lesson:** "it builds" and "it prerenders" are statements about compilation, not about a page working. The only proof that a page loads is a page loading.

### 6. Render blocks SMTP, and my Blueprint kept rewriting the fix

The deployed app could not send a confirmation email. The reason was not Gmail, not credentials, not spam folders. Two faults stacked:

1. `render.yaml` declared `MAIL_TRANSPORT` as a literal `console`, so every Blueprint sync overwrote the dashboard's `smtp` value. The log line `mail (console) to=…` proved it, after an hour of reasoning about quotas.
2. With the switch finally honoured, the send died as a bare `OSError` after a 10-second timeout. **Free Render instances drop outbound traffic to SMTP ports 25, 465 and 587.** No credential will ever fix that.

So verification mail leaves over HTTPS instead: a `brevo` transport that POSTs the same text-only message to an API on port 443, using stdlib `urllib` because one fixed POST doesn't earn a declared dependency. `smtp` stays available for a laptop, where it works.

**Lesson:** a platform's networking rules are part of your architecture. Read them before you debug your credentials.

---

## Multi-tenancy is the part that has to be right

Everything below is enforced server-side; the frontend is untrusted by definition.

**Owner scoping at the store, never after.** `owner_id` is injected into every Pinecone filter inside the store module, so a forgetful caller *cannot* omit it. The same id is part of every Mongo filter, and there's a test that asserts the query shape of each one — because conversation ids are ObjectIds, which are sequential and guessable. An unscoped `find_one` would hand over someone else's entire chat history from a URL parameter.

**404, not 403, for a foreign id.** A `403` says *this thread exists and is not yours* — exactly the enumeration the filters exist to prevent. The cost is honest: the API can't tell its own user that a thread was deleted from under them.

**Register and resend answer one identical `202`.** An unknown address and a wrong password are one identical `401`. The unknown-address branch still spends a real Argon2 verification, so response time isn't an enumeration oracle either. The single deliberate exception is the `403` "confirm your email first", which reveals an account that cannot do anything until it's confirmed.

**Passwords** are Argon2id at `m=19456, t=2, p=1` — OWASP's memory-hard floor, and the ceiling is the 512 MB instance the code runs on.

**Rate limits** are a dependency-free sliding window, because the rule is three lines and one deque: 10 chats and 6 uploads per owner per minute, 5 sign-in attempts per IP per minute, 30 shared-page reads per IP per minute, and 8 account creations per IP per *hour*. The minute buckets protect CPU; the hourly one protects a 512 MB Atlas cluster and a personal mailbox.

The one deliberate exception to scoping is the shared-link read, and it's exception-shaped: the caller has no identity to scope by, so a **128-bit `secrets.token_urlsafe(16)`** carries the whole authority. Unlike an ObjectId, it cannot be walked. That single fact is the only reason an unscoped query is allowed anywhere in the codebase.

---

## Sharing a thread without leaking the documents

You can hand someone a link to a conversation. What they get is the title and the prose — questions and answers. Not filenames, not passages, not citations, not the thread id.

That decision drove three design choices:

**One token per thread, minted idempotently.** The Share button gets double-pressed. A second mint would replace the token while the first link — already pasted into someone's chat — stayed live and readable with nothing left to revoke. So the server reads the row, returns the token it already holds, and only mints when there is none.

**Revoking `$unset`s the key instead of nulling it.** That's what lets the unique index on `share_id` be *partial*: two nulls collide, two absent keys do not. It also means every read asks "is the key there?" rather than comparing to a value.

**The payload cannot be widened by accident.** A three-field Mongo projection plus a `SharedMessage` response model that has no citation, trace, owner or conversation fields at all. A stripped copy of the normal message model would be a filter somebody has to remember; a model with no such fields is a filter nobody can forget.

The trade-off is real: a shared thread is less useful than the real one, and a title seeded from the first question can still quote a document — so the residual leak is the author's own wording, not a stored filename. I'd rather write that down than pretend it doesn't exist.

---

## What $0 actually costs you

Every dependency is free. That is not a footnote — it's a constraint that shaped the code.

- **512 MB RAM** means local embeddings (`all-MiniLM-L6-v2`, 384 dims) and a Docker image that pins `torch==2.15.0+cpu` from PyTorch's CPU index. The default wheel drags ~2 GB of CUDA runtime into a container that will never see a GPU. The embedding model is baked in at build time, otherwise the first question after every cold start spends seconds and ~100 MB of egress downloading it.
- **0.1 CPU** means ingestion is a background task and `202` is the honest response to an upload.
- **A 15-minute idle sleep** means the first visitor of the day waits. I measured it rather than estimating: in the service log, the container's own boot line and the first request that reached it were **81 seconds apart.** I chose to accept it and design for it — the sign-in error copy tells the visitor the server may be waking up, and the retry is one click. The alternative, a cron pinging health every 10 minutes, hides the limitation from visitors while quietly burning the 750 free instance-hours. Being explicit about a real constraint is the more production-minded answer.
- **Shared quotas** mean the rate limiter protects *everyone's* Groq key from one runaway client, not just the server.

There's also a rule I'd defend in any environment: secrets never enter the image. `render.yaml` declares every secret as `sync: false`, so values live in the dashboard and never in a `Dockerfile`'s `ENV` lines, which are readable by anyone who can pull the image. The one non-secret that deployment needs, `CORS_ORIGINS`, *is* a literal in the YAML — because an origin is visible in every preflight response anyway, and keeping it out of the dashboard means a Blueprint sync can't quietly drop it and reopen the API to any page on the internet.

---

## How it's tested

**445 tests: 371 backend, 74 frontend.** No network, no credentials, no model downloads. The backend suite runs in about 45 seconds; the frontend suite in about 8.

The rule that keeps it worth running: **mock the LLM, never the pipeline.** Model output is non-deterministic and costs quota; everything else is deterministic and free. A router test uses a fake model that returns a malformed reply, because "the model answered in an unexpected format" is the failure the parser exists for.

Some of it is unusual on purpose:

- **Query-shape tests.** A fake store that records the filter it was handed catches a missing `owner_id` that an in-memory fake would happily execute.
- **One test per edge of the graph**, including the revision ceiling — otherwise the boundary lives in a comment.
- **`test_cors.py`** preflights every verb the client uses, as described above.
- **A regex test per citation shape**: single, chained `[1][3]`, two digits, and brackets that are not markers.

And what it deliberately doesn't test matters just as much. `SharedThread` — the component that fetches a shared thread — has no unit test. It is one `fetch` and four branches, and stubbing `fetch` in jsdom would be testing the stub. The whole risk in that component is what the *real* server answers to a *real* token, so it's verified by driving the running app. Same rule for SSE streaming and the `localStorage` session: a jsdom stand-in for a browser feature is a test of the stand-in.

That principle is also how the CORS and pre-rendering bugs were found — both were invisible to every test I had, and both were obvious the moment a real browser touched the real deployment.

---

## What's still wrong with it

The most honest part of any portfolio project:

- **No OCR.** An image-only PDF produces no text and says so instead of guessing.
- **MiniLM is a deliberate downgrade.** 384 dims is weaker than a hosted 1536-dim model. It buys $0 and no rate limit on the one step that runs per chunk.
- **Accounts, but no recovery.** No forgotten password, no 2FA, no address change. A locked-out visitor registers again.
- **Mail goes through one provider's free tier** — no queue, no retries beyond one bounded timeout.
- **The demo account is public.** Its documents are visible to everyone who signs in with it. Use your own account for anything you don't want read.
- **Rate limiting is per process.** A second instance would double the effective allowance. Correct for one free instance; it would need Redis above that.
- **Answer quality is non-deterministic.** Same question, different wording, occasionally different citations. The citation chips are how you check any individual answer — which is the whole design philosophy in one sentence.
- **Verification latency is real.** Several seconds before the first token on a multi-agent turn.

---

## Five things I'd tell someone building the same thing

1. **Decide what "verified" means before you write the critic.** Mine is "every factual claim is supported by the numbered passages it was given." That sentence determines the prompt, the response format, the retry rule, and what the UI can honestly promise. Without it you'll build a critic that agrees with everything.
2. **Test the boundary where your tests don't run.** Unit tests stand inside your code. CORS, cold starts, proxy buffering, and pre-rendering all happen outside it. One run in a real browser is worth a hundred green checks — and it's the only thing that would have caught two bugs in this list.
3. **Put the security filter where the call can't forget it.** Owner scoping lives inside the store module, not at each call site. A rule enforced by convention is a rule with a bug ticket already written.
4. **Mock the expensive, deterministic-ise the rest.** Never let a test call the model; never let a test skip the pipeline.
5. **Publish your limits.** Every constraint you state is a decision you've already made. A limitations section is the difference between "this person shipped a demo" and "this person knows what they shipped."

---

## Try it

- **App:** [documind-kappa-indol.vercel.app](https://documind-kappa-indol.vercel.app)
- **Code:** [github.com/AmiruMallawarachchi/documind-ai](https://github.com/AmiruMallawarachchi/documind-ai) (MIT)
- **API health:** [documind-backend-erw5.onrender.com/api/health](https://documind-backend-erw5.onrender.com/api/health)

Sign in with the demo account `demo@documind.app` / `documind-demo-2026`, or register your own address and click the confirmation link (it goes through an HTTPS mail API, because the host can't speak SMTP).

Fair warning: the backend sleeps after fifteen idle minutes. If the first request takes a minute and a half, that's the free tier waking up — and it's the one number in this article I'd most like to make disappear.

---

*Built to be read as much as used: `ARCHITECTURE.md` explains every rule in this article with file and line references, and `DECISIONS.md` records what each choice cost. If you're hiring for backend or AI product work, the repository is the application.*
