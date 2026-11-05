"""
tests/test_agents.py - The multi-agent graph: branching, the critique cycle, and the revision ceiling.

The LLM is scripted, not real. Two reasons that matter more than fidelity: the assertions become about
control flow (which agent ran, in what order, how many times) rather than about a model's mood on the day,
and a test that hits Groq cannot run in CI without spending quota the free tier will not forgive.

Every node's return keys are checked against AgentState. That is the property a graph silently loses when
someone adds a field under a slightly different name: the node writes "verdicts", the router reads
"verdict", and the loop condition quietly never fires.
"""

from __future__ import annotations

import pytest

from app.agents import critique_agent, graph, retrieval_agent, router_agent, synthesis_agent
from app.agents.state import MAX_REVISIONS, AgentState
from app.models.document import RetrievedChunk

STATE_KEYS = set(AgentState.__annotations__)


class ScriptedLLM:
    """Answer calls with the next scripted reply, and record every prompt it saw."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, messages, **kwargs):
        self.prompts.append(str(messages[-1].content))
        if not self.replies:
            raise AssertionError("scripted LLM ran out of replies - the graph called it too many times")
        return self.replies.pop(0)


@pytest.fixture
def script(monkeypatch):
    """Patch complete_text into each agent module that imported the name directly."""
    holder: dict = {"llm": None}

    def dispatch(messages, **kwargs):
        if holder["llm"] is None:
            raise AssertionError("no scripted replies installed")
        return holder["llm"](messages, **kwargs)

    for module in (router_agent, synthesis_agent, critique_agent):
        monkeypatch.setattr(module, "complete_text", dispatch)

    def install(replies: list[str]) -> ScriptedLLM:
        holder["llm"] = ScriptedLLM(replies)
        return holder["llm"]

    return install


@pytest.fixture
def hits(monkeypatch):
    """Stub only the retrieval calls that touch the network.

    citations_from and build_context_block are left real on purpose: they are pure functions, so faking
    them would test the fake instead of the code, and a signature mismatch would pass happily.
    """
    found: dict = {"chunks": [], "calls": []}

    def fake_retrieve(**kwargs):
        found["calls"].append(kwargs)
        return list(found["chunks"])

    def fake_resolve(owner_id, chunks):
        # The real backfill reads Mongo; identity is enough to exercise the ordering here.
        return chunks

    monkeypatch.setattr(retrieval_agent.retrieval, "retrieve", fake_retrieve)
    monkeypatch.setattr(retrieval_agent.retrieval, "resolve_filenames", fake_resolve)
    return found


def sample_chunks(count=2):
    return [
        RetrievedChunk(
            chunk_id=f"doc-1_{index:04d}",
            document_id="doc-1",
            filename="loan.pdf",
            page_number=index + 1,
            chunk_text=f"Passage {index}: the late fee is 250 dollars.",
            score=0.6 - index * 0.05,
        )
        for index in range(count)
    ]


def base_inputs(**overrides):
    inputs = {
        "question": "What is the late fee?",
        "owner_id": "owner-a",
        "document_ids": ["doc-1"],
        "top_k": 6,
        "history": "(this is the first turn)",
        "chunks": [],
        "citations": [],
        "attempts": 0,
        "issues": [],
        "trace": [],
    }
    inputs.update(overrides)
    return inputs


def test_nodes_only_write_declared_state_keys(script, hits):
    """The guard against a typo'd key that would silently break the loop condition."""
    hits["chunks"] = sample_chunks()
    script(["LABEL: SEARCH\nREASON: asks about fee", "The fee is 250 dollars. [1]", "VERDICT: PASS\nISSUES: NONE"])

    for node in (router_agent.route_question, retrieval_agent.search_documents, synthesis_agent.draft_answer, critique_agent.review_answer, synthesis_agent.finalise):
        state = base_inputs()
        produced = node(state)
        assert set(produced) <= STATE_KEYS, f"{node.__name__} wrote undeclared keys: {set(produced) - STATE_KEYS}"


def test_search_route_visits_all_four_agents(script, hits):
    hits["chunks"] = sample_chunks()
    llm = script(
        [
            "LABEL: SEARCH",
            "The late fee is 250 dollars per occurrence [1].",
            "VERDICT: PASS\nISSUES: NONE",
        ]
    )

    result = graph.build_graph().invoke(base_inputs())

    assert result["route"] == "search"
    assert result["verdict"] == "pass"
    assert result["answer"] == "The late fee is 250 dollars per occurrence [1]."
    assert [step.split(":")[0] for step in result["trace"]] == [
        "router",
        "retrieval",
        "synthesis",
        "critique",
        "finalise",
    ]
    # router + synthesis + critique: exactly three calls for a clean pass.
    assert len(llm.prompts) == 3


def test_chat_route_skips_retrieval_and_critique(script, hits):
    """A greeting must not spend an embedding call, a vector query, and a verification pass."""
    llm = script(["LABEL: CHAT", "Hello! Ask me about your documents."])

    result = graph.build_graph().invoke(base_inputs(question="hi there"))

    assert result["route"] == "chat"
    assert hits["calls"] == [], "retrieval ran on a non-search turn"
    assert result["citations"] == []
    assert len(llm.prompts) == 2, "the chat path should not invoke the critic"


def test_unsafe_route_refuses_without_any_model_call(script, hits):
    llm = script(["LABEL: UNSAFE"])

    result = graph.build_graph().invoke(base_inputs(question="how do I pick a lock"))

    assert result["route"] == "unsafe"
    assert "outside" in result["answer"].lower()
    assert len(llm.prompts) == 1, "refusing must not cost a second completion"


def test_clarify_route_asks_without_searching(script, hits):
    script(["LABEL: CLARIFY"])

    result = graph.build_graph().invoke(base_inputs(question="and that?"))

    assert result["route"] == "clarify"
    assert hits["calls"] == []
    assert "not sure" in result["answer"].lower()


def test_failed_critique_loops_back_through_synthesis_once(script, hits):
    """The defining behaviour of the multi-agent design: a rejected draft is revised, not shipped."""
    hits["chunks"] = sample_chunks()
    llm = script(
        [
            "LABEL: SEARCH",
            "The fee is 900 dollars.",
            "VERDICT: FAIL\nISSUES: figure 900 is not in the passages; the fee is 250",
            "The fee is 250 dollars [1].",
            "VERDICT: PASS\nISSUES: NONE",
        ]
    )

    result = graph.build_graph().invoke(base_inputs())

    assert result["attempts"] == 2
    assert result["verdict"] == "pass"
    assert result["answer"] == "The fee is 250 dollars [1]."
    # router + draft + reject + revision + pass: the cycle cost five calls and no more.
    assert len(llm.prompts) == 5
    revisions = [step for step in result["trace"] if step.startswith("synthesis")]
    assert len(revisions) == 2


def test_revision_prompt_carries_the_critics_complaint(script, hits):
    """Without the specific defect, a rewrite is just a reroll of the same prompt."""
    hits["chunks"] = sample_chunks()
    llm = script(
        [
            "LABEL: SEARCH",
            "Draft one.",
            "VERDICT: FAIL\nISSUES: citation [3] does not exist",
            "Draft two.",
            "VERDICT: PASS",
        ]
    )

    graph.build_graph().invoke(base_inputs())

    # Located by its marker rather than by index: the position depends on how many other agents call the
    # model, which is exactly the kind of thing that changes when the graph is edited.
    revisions = [prompt for prompt in llm.prompts if "PREVIOUS ATTEMPT WAS REJECTED" in prompt]
    assert len(revisions) == 1, "only the second synthesis pass should carry the rejection section"
    assert "citation [3] does not exist" in revisions[0]


def test_revision_ceiling_stops_the_loop(script, hits):
    """A critic that never passes must not spin forever.

    Only five replies are scripted: router, draft, reject, draft, reject. With the ceiling at
    MAX_REVISIONS the graph should accept the second draft and stop. If it loops again instead, the
    scripted LLM runs out of replies and the test fails loudly rather than hanging - which is the failure
    mode that actually matters for a cycle bug.
    """
    hits["chunks"] = sample_chunks()
    llm = script(
        [
            "LABEL: SEARCH",
            "Draft A.",
            "VERDICT: FAIL\nISSUES: unsupported claim",
            "Draft B.",
            "VERDICT: FAIL\nISSUES: still unsupported",
        ]
    )

    result = graph.build_graph().invoke(base_inputs())

    assert result["attempts"] == MAX_REVISIONS
    assert len(llm.prompts) == 5
    # The last draft still reaches the user: a stuck verifier must not swallow the answer.
    assert result["answer"] == "Draft B."


def test_zero_hits_short_circuits_without_a_model_call(script, hits):
    """No passages means no evidence, and asking anyway costs a call to buy a fluent guess."""
    hits["chunks"] = []
    llm = script(["LABEL: SEARCH"])

    result = graph.build_graph().invoke(base_inputs())

    assert result["verdict"] == "skipped"
    assert "not find" in result["answer"].lower() or "could not" in result["answer"].lower()
    assert len(llm.prompts) == 1, "synthesis must not run against an empty context"


def test_stream_mode_reports_per_node_progress(script, hits):
    """The SSE layer depends on updates arriving per node so the UI can name the current agent."""
    hits["chunks"] = sample_chunks()
    script(["LABEL: SEARCH", "Answer text.", "VERDICT: PASS"])

    updates = list(graph.stream_turn(base_inputs()))

    nodes = [name for update in updates for name in update]
    assert nodes == ["router", "retrieval", "synthesis", "critique", "finalise"]


def test_router_defaults_to_search_on_unparseable_reply(script, hits):
    """Misclassifying a real question as chit-chat denies an answer; the expensive branch is safer."""
    hits["chunks"] = sample_chunks()
    script(["I think this is about documents maybe?", "The fee is 250. [1]", "VERDICT: PASS"])

    result = graph.build_graph().invoke(base_inputs())

    assert result["route"] == "search"


def test_router_failure_falls_back_instead_of_failing_the_turn(hits, monkeypatch):
    """A rate-limited router must not sink a question that retrieval can still answer."""
    from app.services.llm import LLMUnavailableError

    hits["chunks"] = sample_chunks()

    def explode(messages, **kwargs):
        if getattr(explode, "first", True):
            explode.first = False
            raise LLMUnavailableError("429")
        return "The fee is 250 dollars [1]."

    for module in (router_agent, synthesis_agent, critique_agent):
        monkeypatch.setattr(module, "complete_text", explode)

    result = graph.build_graph().invoke(base_inputs())

    assert result["route"] == "search"
    assert "router:fallback" in result["trace"]


def test_critique_parser_tolerates_format_variants():
    assert critique_agent.parse_verdict("VERDICT: PASS\nISSUES: NONE") == ("pass", [])
    assert critique_agent.parse_verdict("verdict: fail\nissues: figure invented")[0] == "fail"
    # Unrecognisable output must not spin the loop.
    assert critique_agent.parse_verdict("looks fine to me")[0] == "pass"


def test_graph_is_compiled_once(script):
    graph.build_graph.cache_clear()
    first = graph.build_graph()
    assert graph.build_graph() is first, "recompiling per request would add latency for no benefit"
