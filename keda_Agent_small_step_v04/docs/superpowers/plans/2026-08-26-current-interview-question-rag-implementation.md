# Current Interview Question RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a source-traceable, expiry-aware interview-question retrieval layer that converts the current Supervisor decision into a filtered BGE-M3/Qdrant lookup and grounds Question Generator without breaking the interview when retrieval is unavailable.

**Architecture:** Versioned JSON remains the source of truth; Qdrant is a disposable local search index. A deterministic intent builder converts `AskAction` plus plan/JD/resume/runtime facts into a query, a retriever filters and ranks active questions, and the graph passes at most one selected question to Question Generator while persisting a safe internal trace.

**Tech Stack:** Python 3.11+, Pydantic 2, httpx, SiliconFlow `BAAI/bge-m3`, qdrant-client local persistence, LangGraph, unittest.

## Global Constraints

- The only supported role is `ai_agent_engineer`.
- Qdrant contains interview-question records plus one manifest; it is never authoritative.
- Only `active` records with `valid_until >= today` may be returned.
- Supervisor remains deterministic and does not call Qdrant or an LLM.
- Secrets come only from environment variables and never appear in source, fixtures, logs, errors, or commits.
- Retrieval failure degrades to the existing generator and records the real failure status.
- No production corpus, crawler, scheduler, reranker, multi-role abstraction, or admin UI is added.
- Existing unrelated dirty files must not be staged, reverted, reformatted, or overwritten.
- The pre-existing unstaged edits in `question_generator_service.py` and `tests/test_question_generator_service.py` are user work, not part of this plan's baseline; do not stage those older hunks with RAG changes.

---

### Task 1: Define the Strict Question RAG Contract

**Files:**
- Create: `profile_agent/schemas/question_rag_schema.py`
- Create: `tests/test_question_rag_schema.py`

**Interfaces:**
- Produces: `InterviewQuestionRecord`, `QuestionRetrievalIntent`, `RetrievedQuestion`, `QuestionRetrievalTrace`, `QuestionRetrievalResult`.

- [ ] **Step 1: Write failing schema tests**

Construct one valid record and separately reject empty source URL, unsupported role, invalid lifecycle dates, empty expected signals, invalid status, and inconsistent hit/no-match results. Desired shape:

```python
record = InterviewQuestionRecord(
    question_id="q_agent_001",
    question_text="工具执行成功但响应丢失，如何避免重复执行？",
    role="ai_agent_engineer",
    role_version="2026-H2",
    dimension_id="role_dim_01",
    skills=["幂等", "失败恢复"],
    question_mode="scenario",
    difficulty="intermediate",
    expected_signals=["幂等键", "执行状态查询"],
    critical_errors=["所有失败都直接重试"],
    follow_up_seeds=["首次已成功但响应丢失时怎么办？"],
    company_tags=[],
    source_id="src_public_001",
    source_url="https://example.com/source",
    source_title="Public source",
    source_type="public_interview_experience",
    published_at=date(2026, 7, 1),
    verified_at=date(2026, 8, 26),
    valid_until=date(2027, 2, 26),
    trust_level="medium",
    status="active",
    version=1,
    content_hash="sha256:abc",
)
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_question_rag_schema -v
```

Expected: import failure because the schema module does not exist.

- [ ] **Step 3: Implement minimal strict models**

Reuse existing `QuestionMode`. Define literal types for difficulty, trust, lifecycle, and retrieval status. Enforce:

```python
class QuestionRetrievalIntent(BaseModel):
    query_text: str
    role: Literal["ai_agent_engineer"]
    dimension_id: str
    question_mode: QuestionMode
    difficulty: QuestionDifficulty
    excluded_question_ids: list[str] = Field(default_factory=list)

class QuestionRetrievalTrace(BaseModel):
    status: RetrievalStatus
    question_id: str | None = None
    source_id: str | None = None
    score: float | None = None
    index_version: str | None = None
```

A `hit` requires selected data and IDs. `no_match`, `unavailable`, and `index_mismatch` must not claim a selected record.

- [ ] **Step 4: Verify GREEN and regressions**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_question_rag_schema -v
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/schemas/question_rag_schema.py tests/test_question_rag_schema.py
git commit -m "feat: define interview question rag contract"
```

---

### Task 2: Build the Canonical JSON Loader and Lifecycle Audit

**Files:**
- Create: `profile_agent/services/question_bank_service.py`
- Create: `tests/test_question_bank_service.py`
- Create: `tests/fixtures/question_rag/minimal_question_bank.json`

**Interfaces:**
- Produces: `normalize_question_text`, `build_question_content_hash`, `load_question_bank`, `audit_question_bank`.

- [ ] **Step 1: Write failing normalization, duplicate, and lifecycle tests**

Test whitespace-stable hashes, semantic-field hash changes, duplicate IDs, duplicate hashes, stored-hash mismatch, invalid JSON roots, unsupported dimension IDs, expired and expiring records, and no mutation during audit.

```python
self.assertEqual(normalize_question_text("  Agent  \n  失败恢复  "), "Agent 失败恢复")
self.assertEqual(build_question_content_hash(a), build_question_content_hash(whitespace_variant))
self.assertNotEqual(build_question_content_hash(a), build_question_content_hash(changed_skill))
```

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_question_bank_service -v
```

- [ ] **Step 3: Implement canonical loading and hashing**

Hash only normalized semantic fields:

```python
payload = {
    "question_text": normalize_question_text(record.question_text),
    "role": record.role,
    "dimension_id": record.dimension_id,
    "skills": sorted(normalize_question_text(value) for value in record.skills),
    "question_mode": record.question_mode,
    "difficulty": record.difficulty,
}
digest = hashlib.sha256(
    json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
).hexdigest()
return f"sha256:{digest}"
```

The six-record fixture is synthetic, uses `example.com`, covers all six dimensions, and is explicitly rejected by the production CLI unless a test dependency is injected.

- [ ] **Step 4: Verify GREEN and regressions**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_question_bank_service -v
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/services/question_bank_service.py tests/test_question_bank_service.py tests/fixtures/question_rag/minimal_question_bank.json
git commit -m "feat: validate versioned interview question banks"
```

---

### Task 3: Add the Secret-Safe SiliconFlow BGE-M3 Client

**Files:**
- Create: `profile_agent/services/siliconflow_embedding_service.py`
- Create: `tests/test_siliconflow_embedding_service.py`
- Modify: `.env.example`

**Interfaces:**
- Produces protocol `EmbeddingClient.embed(texts: Sequence[str]) -> list[list[float]]`.
- Produces `SiliconFlowEmbeddingClient.from_env()` and `EmbeddingProviderError`.

- [ ] **Step 1: Write failing HTTP-boundary tests**

Using `httpx.MockTransport`, test exact `/embeddings` endpoint, default model, batching, response ordering by `data[].index`, empty-input rejection, missing-key behavior, bounded 429/5xx retry, and secret-free errors/logs.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_siliconflow_embedding_service -v
```

- [ ] **Step 3: Implement the client**

```python
class SiliconFlowEmbeddingClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "BAAI/bge-m3",
        base_url: str = "https://api.siliconflow.cn/v1",
        timeout_seconds: float = 30.0,
        max_attempts: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
```

Add only placeholders:

```dotenv
SILICONFLOW_API_KEY=
SILICONFLOW_EMBEDDING_MODEL=BAAI/bge-m3
SILICONFLOW_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
```

Never interpolate authorization headers, response bodies, or full inputs into exceptions.

- [ ] **Step 4: Verify GREEN and regressions**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_siliconflow_embedding_service -v
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/services/siliconflow_embedding_service.py tests/test_siliconflow_embedding_service.py .env.example
git commit -m "feat: call siliconflow bge embeddings safely"
```

---

### Task 4: Implement the Disposable Qdrant Question Index

**Files:**
- Create: `profile_agent/knowledge/qdrant_question_store.py`
- Create: `tests/test_qdrant_question_store.py`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `uv.lock` if generated by `uv sync`

**Interfaces:**
- Produces: `QdrantQuestionStore.rebuild`, `sync`, and `search`.

- [ ] **Step 1: Write failing local-mode Qdrant tests**

Using a temporary directory or `:memory:`, prove the collection manifest records provider/model/dimension/version; payload contains filter fields; filters exclude wrong role/dimension/mode/status/date/IDs; sync removes retired records; fingerprint mismatch returns `index_mismatch`.

- [ ] **Step 2: Add the approved dependency**

Add the same bounded requirement to both dependency files:

```text
qdrant-client>=1.15,<2
```

Run `uv sync`. This is the only new runtime dependency.

- [ ] **Step 3: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_qdrant_question_store -v
```

Expected: adapter import failure.

- [ ] **Step 4: Implement the adapter**

```python
COLLECTION_NAME = "interview_questions"

class QdrantQuestionStore:
    def rebuild(
        self,
        records: Sequence[InterviewQuestionRecord],
        vectors: Sequence[Sequence[float]],
        fingerprint: IndexFingerprint,
    ) -> None: ...

    def search(
        self,
        *,
        intent: QuestionRetrievalIntent,
        query_vector: Sequence[float],
        today: date,
        limit: int = 3,
    ) -> QuestionStoreSearchResult: ...
```

Use a reserved manifest point excluded by `record_type == "question"`. Validate record/vector counts and dimensions before replacing a usable collection.

- [ ] **Step 5: Verify GREEN and regressions**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_qdrant_question_store -v
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

- [ ] **Step 6: Commit**

```powershell
git add profile_agent/knowledge/qdrant_question_store.py tests/test_qdrant_question_store.py pyproject.toml requirements.txt uv.lock
git commit -m "feat: index interview questions in qdrant"
```

---

### Task 5: Build Deterministic Retrieval Intent and Ranking

**Files:**
- Create: `profile_agent/services/question_retrieval_service.py`
- Create: `tests/test_question_retrieval_service.py`

**Interfaces:**
- Consumes: `AskAction`, plan, resume, JD, recent turns/evidence, and asked question IDs.
- Produces: `build_question_retrieval_intent` and `QuestionRetriever.retrieve`.

- [ ] **Step 1: Write failing intent tests**

Prove exact target/requirement resolution; bounded use of planned dimension, objective, requirement, JD, resume and last two answered turns; deterministic output; sorted unique exclusions; no complete resume/JD, secrets, expected signals, or critical errors.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_question_retrieval_service -v
```

- [ ] **Step 3: Implement the builder and retriever**

```python
def build_question_retrieval_intent(
    *,
    action: AskAction,
    plan: InterviewPlan,
    resume_profile: ResumeProfile | None = None,
    job_profile: JobProfile | None = None,
    recent_turns: Sequence[InterviewTurn] = (),
    evidence_summaries: Sequence[str] = (),
    excluded_question_ids: Sequence[str] = (),
) -> QuestionRetrievalIntent: ...
```

`QuestionRetriever.retrieve()` embeds `query_text`, gets at most three filtered candidates, then uses vector score with bounded trust/freshness tie-breakers. Provider/store failures become `unavailable`; invalid plan IDs still raise.

- [ ] **Step 4: Verify GREEN and regressions**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_question_retrieval_service -v
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/services/question_retrieval_service.py tests/test_question_retrieval_service.py
git commit -m "feat: retrieve questions from supervisor intent"
```

---

### Task 6: Ground Question Generator and Persist Retrieval Provenance

**Files:**
- Modify: `profile_agent/services/question_generator_service.py`
- Modify: `profile_agent/graphs/interview.py`
- Modify: `profile_agent/schemas/runtime_schema.py`
- Modify: `profile_agent/state/main_state.py`
- Modify: `profile_agent/web/container.py`
- Modify: `tests/test_question_generator_service.py`
- Modify: `tests/test_interview_graph.py`
- Create: `tests/test_question_rag_graph_integration.py`

**Interfaces:**
- Consumes: optional `QuestionRetrievalResult`.
- Produces: private `InterviewTurn.retrieval_trace`; public report remains unchanged.

- [ ] **Step 1: Write failing grounding tests**

A selected record may add original question, business constraint, skill names, source type, and source date to the LLM prompt. Assert expected signals, critical errors, and follow-up seeds never appear as candidate answer hints. No-match/unavailable preserves the legacy prompt.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_question_generator_service -v
```

- [ ] **Step 3: Write failing graph tests**

Inject a fake retriever and prove:

```text
supervisor -> retrieve_question -> generate_question -> wait_for_answer
```

One retrieval per turn; chosen record reaches generator; trace persists; resume does not re-retrieve; unavailable retrieval still asks; no rubric signal enters the interrupt payload.

- [ ] **Step 4: Implement integration**

Extend the graph builder:

```python
def build_interview_graph(
    question_generator: QuestionGenerator | None = None,
    question_retriever: QuestionRetrieverCallable | None = None,
    answer_processor: AnswerProcessor | None = None,
    ...,
): ...
```

Add transient `question_retrieval_result` to `MainState`, a retrieval node, and optional trace to `InterviewTurn`. Default construction is lazy: missing configuration returns `unavailable` without HTTP during startup.

- [ ] **Step 5: Verify public-data boundaries**

Run report-view tests and explicitly project existing public transcript fields. Never expose question/source IDs, scores, index versions, provider names, rubric signals, or critical errors in public APIs.

- [ ] **Step 6: Verify GREEN and regressions**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_question_generator_service tests.test_interview_graph tests.test_question_rag_graph_integration -v
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

- [ ] **Step 7: Commit carefully**

Before editing, save the exact pre-existing diff for the two already-dirty question-generator files under the ignored `.superpowers/sdd/` directory. After implementation, stage only RAG hunks from those two files; stage the other clean-baseline files normally. Inspect `git diff --cached` and verify that none of the earlier prompt/content hunks are included. If the RAG hunks cannot be separated safely, leave the overlapping files unstaged and report the boundary instead of committing user work.

```powershell
git add profile_agent/graphs/interview.py profile_agent/schemas/runtime_schema.py profile_agent/state/main_state.py profile_agent/web/container.py tests/test_interview_graph.py tests/test_question_rag_graph_integration.py
git diff --cached
git commit -m "feat: ground dynamic interview questions with rag"
```

---

### Task 7: Add Safe Question-Bank Management Commands

**Files:**
- Create: `run_question_bank.py`
- Create: `tests/test_run_question_bank.py`
- Modify: `README.md`

**Interfaces:**
- Produces CLI actions `validate`, `audit`, `rebuild`, and `sync`.

- [ ] **Step 1: Write failing CLI tests**

Call `main(argv, ...)` with fakes. Validate/audit must be read-only. Rebuild/sync without `--apply` must be dry-run. Invalid banks fail before embedding/Qdrant mutation. Missing key errors are secret-safe.

- [ ] **Step 2: Verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_run_question_bank -v
```

- [ ] **Step 3: Implement commands**

```powershell
python run_question_bank.py validate --bank path\to\questions.json
python run_question_bank.py audit --bank path\to\questions.json
python run_question_bank.py rebuild --bank path\to\questions.json
python run_question_bank.py rebuild --bank path\to\questions.json --apply
python run_question_bank.py sync --bank path\to\questions.json --apply
```

Dry-run output includes counts, model, collection, and expected writes, never input text, vectors, headers, or secrets.

- [ ] **Step 4: Document boundaries**

README states JSON is authoritative, Qdrant is rebuildable, environment variable names have no values, fixtures are not production questions, and no real corpus exists until the next specification.

- [ ] **Step 5: Verify tests and secret absence**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_run_question_bank -v
.\.venv\Scripts\python.exe -m unittest discover -s tests
git grep -n -E "sk-[A-Za-z0-9]{20,}" -- . ":(exclude).env"
```

Expected: tests pass and the tracked-secret scan returns no match.

- [ ] **Step 6: Commit**

```powershell
git add run_question_bank.py tests/test_run_question_bank.py README.md
git commit -m "feat: manage interview question indexes safely"
```

---

### Task 8: Verify the Complete Foundation Without Paid Calls

**Files:**
- Modify only when a test exposes a defect in scoped RAG files.

- [ ] **Step 1: Run backend tests**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

- [ ] **Step 2: Run frontend/build/real-API E2E**

```powershell
npm --prefix web test -- --run
npm --prefix web run build
npm --prefix web run e2e
```

- [ ] **Step 3: Run offline and repository checks**

```powershell
.\.venv\Scripts\python.exe run_offline_calibration.py --case ALL
git diff --check
git status --short
```

- [ ] **Step 4: Rehearse zero-cost integration**

With six synthetic records, deterministic fake embeddings, and local Qdrant, demonstrate:

```text
Supervisor AskAction
-> retrieval intent
-> filtered match
-> grounded generator prompt
-> persisted private trace
-> candidate-facing question without rubric leakage
```

- [ ] **Step 5: Commit only real scoped fixes**

Do not create an empty commit. Any verification fix requires its failing regression test and a targeted rerun first.

---

## Completion Boundary

This plan is complete when synthetic records can be indexed and retrieved, one question can ground the real graph, a private audit trace persists, and failures degrade safely. It does not claim a real current question corpus. The next specification will research and review 30 current AI Agent interview questions, build at least 30 labeled retrieval intents, calibrate Recall@3, and then grow the competition bank to 60 questions.
