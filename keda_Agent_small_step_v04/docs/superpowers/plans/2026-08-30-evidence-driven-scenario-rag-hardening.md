# Evidence-Driven Scenario RAG Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the answer-evidence-gap-follow-up loop, make Scenario RAG measurable, and improve candidate-facing questions without adding another Agent or another normal-path LLM call.

**Architecture:** `AnswerProcessor` extracts missing evidence only from a reviewed allowlist derived from the selected Scenario Module. The tags are persisted on `RequirementProgress`, read by `InterviewGraph`, and passed to the existing deterministic `ConstraintSelector`. Retrieval calibration, ranking diagnostics, content governance, and candidate-safe copy remain deterministic services around the existing Qdrant/BGE-M3/reranker path.

**Tech Stack:** Python 3.12, Pydantic 2, LangGraph, Qdrant, BGE-M3, SiliconFlow reranker, unittest/pytest, JSON Scenario Bank.

## Global Constraints

- Keep the current Planner → Supervisor → Scenario Retriever → Constraint Selector → QuestionGenerator responsibilities.
- Add no new Agent and no new graph node.
- Add no new LLM call on the valid normal path; the existing semantic-correction retry remains the only retry.
- Never expose `evidence_signals`, `critical_errors`, unreleased constraints, or score keys to candidates.
- Keep `Qdrant`, `BAAI/bge-m3`, the current reranker, ScoreEngine, and Report pipeline.
- Do not expand the Scenario Bank beyond 35 Modules in this plan.
- Paid retrieval calibration must require an explicit CLI flag and must never run in ordinary unit tests or CI.
- Preserve old plans/checkpoints by giving every new persisted field a safe default.

---

### Task 1: Persist the latest structured evidence gap

**Files:**
- Modify: `profile_agent/schemas/runtime_schema.py`
- Modify: `profile_agent/services/runtime_state_service.py`
- Modify: `tests/test_runtime_schema.py`
- Modify: `tests/test_runtime_state_service.py`

**Interfaces:**
- Produces: `RequirementAssessment.missing_evidence_tags: list[str]`
- Produces: `RequirementProgress.latest_gap_tags: list[str]`
- Produces: `record_requirement_evidence(..., latest_gap_tags: Sequence[str] = ())`

- [ ] **Step 1: Write failing schema tests**

```python
assessment = RequirementAssessment(
    requirement_id="req_01",
    recommended_status="in_progress",
    rationale="版本更新和引用核验尚未证明",
    missing_evidence_tags=["版本", "引用"],
)
assert assessment.missing_evidence_tags == ["版本", "引用"]
assert RequirementProgress(requirement_id="req_01").latest_gap_tags == []
```

- [ ] **Step 2: Run the tests and confirm the fields are absent**

```powershell
.venv\Scripts\python.exe -m pytest -q tests\test_runtime_schema.py tests\test_runtime_state_service.py
```

Expected: FAIL because the new fields/signature do not exist.

- [ ] **Step 3: Add backward-compatible fields and persistence rules**

```python
class RequirementAssessment(BaseModel):
    requirement_id: str
    recommended_status: Literal["in_progress", "sufficient", "contradictory"]
    rationale: str
    missing_evidence_tags: list[str] = Field(default_factory=list)


class RequirementProgress(BaseModel):
    requirement_id: str
    status: RequirementStatus = "not_started"
    attempt_count: int = Field(default=0, ge=0)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    latest_gap_tags: list[str] = Field(default_factory=list)
```

In `record_requirement_evidence`, replace the stored tags on every assessment. Preserve validated tags for both active statuses, `in_progress` and `contradictory`; clear them only for `sufficient`. A contradictory assessment may legitimately return `[]`, but the persistence layer must not clear its tags merely because the status is contradictory: `Supervisor` treats both active statuses as follow-up candidates.

- [ ] **Step 4: Add tests for replacement, clearing, deduplication, and input immutability**

```python
updated = record_requirement_evidence(
    runtime,
    requirement_id="req_01",
    status="in_progress",
    supporting_evidence_ids=["evidence_001"],
    contradicting_evidence_ids=[],
    known_evidence_ids={"evidence_001"},
    latest_gap_tags=["版本", "引用", "版本"],
)
assert updated.requirement_progress["req_01"].latest_gap_tags == ["版本", "引用"]
assert runtime.requirement_progress["req_01"].latest_gap_tags == []

contradictory = record_requirement_evidence(
    updated,
    requirement_id="req_01",
    status="contradictory",
    supporting_evidence_ids=[],
    contradicting_evidence_ids=["evidence_002"],
    known_evidence_ids={"evidence_001", "evidence_002"},
    latest_gap_tags=["版本", "引用"],
)
assert contradictory.requirement_progress["req_01"].latest_gap_tags == ["版本", "引用"]

sufficient = record_requirement_evidence(
    contradictory,
    requirement_id="req_01",
    status="sufficient",
    supporting_evidence_ids=["evidence_003"],
    contradicting_evidence_ids=[],
    known_evidence_ids={"evidence_001", "evidence_002", "evidence_003"},
    latest_gap_tags=["版本"],
)
assert sufficient.requirement_progress["req_01"].latest_gap_tags == []
```

- [ ] **Step 5: Run focused tests and commit**

```powershell
.venv\Scripts\python.exe -m pytest -q tests\test_runtime_schema.py tests\test_runtime_state_service.py
git add profile_agent/schemas/runtime_schema.py profile_agent/services/runtime_state_service.py tests/test_runtime_schema.py tests/test_runtime_state_service.py
git commit -m "feat: persist requirement evidence gaps"
```

---

### Task 2: Extract missing tags through a reviewed allowlist

**Files:**
- Modify: `profile_agent/services/answer_processor_service.py`
- Modify: `tests/test_answer_processor_service.py`

**Interfaces:**
- Consumes: `allowed_gap_tags: Sequence[str]`
- Produces: validated `RequirementAssessment.missing_evidence_tags`

- [ ] **Step 1: Write failing AnswerProcessor tests**

Cover all six contracts:

```python
# Valid in-progress assessment persists only allowed tags.
process_answer(..., allowed_gap_tags=["Memory", "删除", "RAG", "版本", "引用"])

# A contradictory assessment for the primary Requirement may preserve allowed tags.
# An unknown tag such as "请求注入" triggers the existing correction retry.
# A sufficient assessment must persist no latest gap.
# An empty allowlist accepts only an empty missing_evidence_tags list.
# A non-primary RequirementAssessment may update status/evidence but must return missing_evidence_tags=[].
```

- [ ] **Step 2: Confirm RED**

```powershell
.venv\Scripts\python.exe -m pytest -q tests\test_answer_processor_service.py
```

Expected: FAIL because `process_answer` does not accept or validate `allowed_gap_tags`.

- [ ] **Step 3: Extend the prompt without adding a model call**

Change the function contract to:

```python
def process_answer(
    plan: InterviewPlan,
    runtime_state: InterviewRuntimeState,
    turn: InterviewTurn,
    existing_evidences: list[Evidence],
    claim_registry: ClaimRegistry | None = None,
    *,
    allowed_gap_tags: Sequence[str] = (),
    llm_client=llm,
) -> AnswerProcessingResult:
```

Add an explicit prompt block containing the JSON-encoded allowlist and the current `turn.primary_requirement_id`. Apply this exact contract:

```text
assessment.requirement_id == turn.primary_requirement_id
and recommended_status in {in_progress, contradictory}
  -> missing_evidence_tags may contain only values from allowed_gap_tags

recommended_status == sufficient
  -> missing_evidence_tags must be []

assessment.requirement_id != turn.primary_requirement_id
  -> missing_evidence_tags must be [] even when that Requirement belongs to the current Target
```

An empty allowlist always requires `missing_evidence_tags=[]`. This does not stop a single answer from producing Evidence or status updates for multiple Requirements in the current Target; it only prevents the current Scenario Module's gap vocabulary from leaking into secondary Requirements.

- [ ] **Step 4: Validate before persistence**

Normalize by exact trimmed string, preserve allowlist spelling, reject unknown/duplicate values, enforce the Primary Requirement/status rules above, and feed the validated tags into `record_requirement_evidence`. Reuse the current two-attempt semantic correction path; do not introduce another loop. Do not introduce a per-Requirement allowlist map in v1.

- [ ] **Step 5: Run focused tests and commit**

```powershell
.venv\Scripts\python.exe -m pytest -q tests\test_answer_processor_service.py
git add profile_agent/services/answer_processor_service.py tests/test_answer_processor_service.py
git commit -m "feat: constrain answer evidence gaps"
```

---

### Task 3: Wire Evidence Gap to Constraint Selector end to end

**Files:**
- Modify: `profile_agent/graphs/interview.py`
- Modify: `tests/test_interview_graph.py`
- Modify: `tests/test_scenario_rag_graph_integration.py`
- Modify: `tests/test_scenario_checkpoint.py`

**Interfaces:**
- Consumes: `RequirementProgress.latest_gap_tags`
- Consumes: current turn `QuestionProvenance.retrieval_unit_id`
- Produces: `prepare_question_context(..., evidence_gap_tags=latest_gap_tags)`

- [ ] **Step 1: Add the failing dynamic-follow-up integration test**

Use the real `knowledge_rag_memory` Module. The fake structured AnswerProcessor response must return:

```python
RequirementAssessment(
    requirement_id="req_01",
    recommended_status="in_progress",
    rationale="Memory 删除已说明，版本与引用未证明",
    missing_evidence_tags=["版本", "引用"],
)
```

After the next Supervisor `follow_up`, assert:

```python
assert context.selected_constraint.constraint_id == "knowledge_policy_version_stale"
assert context.selected_constraint.constraint_id != "knowledge_memory_delete"
```

- [ ] **Step 2: Confirm the existing graph chooses the wrong first constraint**

```powershell
.venv\Scripts\python.exe -m pytest -q tests\test_scenario_rag_graph_integration.py -k missing_evidence
```

Expected: FAIL with `knowledge_memory_delete` selected.

- [ ] **Step 3: Derive the AnswerProcessor allowlist from durable provenance**

In `process_answer_node`, resolve `turn.question_provenance.retrieval_unit_id` through the injected `ScenarioCatalog`; flatten and deduplicate `evidence_gap_tags` from that Module's active constraints. Pass the result only when the processor signature accepts `allowed_gap_tags` or `**kwargs`, matching the compatibility approach already used by `_call_question_generator`.

- [ ] **Step 4: Replace the hard-coded empty gap at question preparation**

```python
progress = state["runtime_state"].requirement_progress[action.primary_requirement_id]
context = preparer(
    ...,
    evidence_gap_tags=tuple(progress.latest_gap_tags),
)
```

Initial scenario questions continue to receive an empty list. Follow-ups receive the latest validated tags.

- [ ] **Step 5: Verify checkpoint compatibility**

Add a round-trip case showing `RequirementProgress.latest_gap_tags` survives the graph checkpoint. Also load a legacy payload without the field and assert the default is `[]`.

- [ ] **Step 6: Run the P0 suite and commit**

```powershell
.venv\Scripts\python.exe -m pytest -q tests\test_answer_processor_service.py tests\test_runtime_state_service.py tests\test_interview_graph.py tests\test_scenario_rag_graph_integration.py tests\test_scenario_checkpoint.py
git add profile_agent/graphs/interview.py tests/test_interview_graph.py tests/test_scenario_rag_graph_integration.py tests/test_scenario_checkpoint.py
git commit -m "feat: drive follow-ups from evidence gaps"
```

---

### Task 4: Add a reusable Scenario Retrieval calibration suite

**Files:**
- Create: `profile_agent/schemas/scenario_calibration_schema.py`
- Create: `profile_agent/services/scenario_calibration_service.py`
- Create: `tests/fixtures/scenario_rag/retrieval_cases.json`
- Create: `tests/test_scenario_calibration_service.py`
- Modify: `run_scenario_bank.py`
- Modify: `tests/test_run_scenario_bank.py`

**Interfaces:**
- Produces: `ScenarioRetrievalCase`
- Produces: `ScenarioCalibrationReport`
- Produces: `evaluate_scenario_retrieval(cases, retriever, as_of)`

- [ ] **Step 1: Define strict case and report schemas**

```python
class ScenarioRetrievalCase(BaseModel):
    case_id: str
    query: str
    primary_dimension_id: OfficialDimensionId
    requirement_type: TargetType
    question_mode: QuestionMode
    difficulty: QuestionDifficulty
    acceptable_module_ids: list[str] = Field(min_length=1)
    forbidden_module_ids: list[str] = Field(default_factory=list)


class ScenarioCalibrationReport(BaseModel):
    case_count: int
    top1_acceptable_rate: float
    top3_recall: float
    forbidden_hit_count: int
    forbidden_top1_hit_count: int
    fallback_count: int
    case_results: list[ScenarioCalibrationCaseResult]
```

`ScenarioCalibrationCaseResult` also records `top1_forbidden: bool`. For backward compatibility,
old reports that omit the new fields load with `False`/`0`; every newly evaluated report fills both
fields from the actual ranked result.

- [ ] **Step 2: Create exactly 24 reviewed cases**

Use four atomic cases per radar dimension. Required case IDs include:

```text
role_dim_01: task_routing, multi_agent_handoff, shared_state_conflict, termination_loop
role_dim_02: ambiguous_business_goal, workflow_decomposition, success_metric, human_boundary
role_dim_03: memory_lifecycle, knowledge_version, tool_context_boundary, citation_traceability
role_dim_04: ai_delivery_pipeline, regression_verification, model_change_rollout, coding_review
role_dim_05: tool_idempotency, prompt_injection, retrieval_trust, call_chain_attribution
role_dim_06: llm_cost_reduction, model_routing, cache_quality_tradeoff, latency_budget
```

Each case must list multiple acceptable Modules where multiple business worlds are valid; no case may use a cross-dimension query. Every case with a meaningful negative must include at least one `forbidden_module_id` from the same `primary_dimension_id`, so the case tests whether retrieval chose the wrong business world after the dimension filter already succeeded. Do not use a cross-dimension Module as the only forbidden result.

Keep cross-dimension rejection as a separate deterministic hard-filter guard in `tests/test_qdrant_scenario_store.py`; it verifies filter integrity, not semantic ranking quality, and therefore does not contribute to `forbidden_hit_count` in the 24-case calibration report.

- [ ] **Step 3: Implement metrics with an injected retriever**

Unit tests use a fake retriever and make no provider calls. `forbidden_hit_count` examines Top 3
and remains a diagnostic for business-world confusion; `forbidden_top1_hit_count` counts only
Top-1 forbidden selections because runtime consumes only Top-1. `fallback_count` counts both
explicit fallback and unavailable/no-match outcomes.

- [ ] **Step 4: Add explicit paid CLI evaluation**

Extend the action choices to `validate`, `rebuild-index`, and `evaluate`. `evaluate` without `--apply` prints the case count and estimated embedding/rerank calls; `evaluate --apply` uses the configured real retriever, writes a JSON report beneath ignored `artifacts/scenario_rag/`, and prints the Top-1 gate summary alongside the Top-3 forbidden diagnostic. The pure `ScenarioCalibrationAcceptance` decision uses Top-1 acceptable rate `>= 0.75`, Top-3 recall `>= 0.90`, `forbidden_top1_hit_count == 0`, and `fallback_count == 0`; a non-zero Top-3 `forbidden_hit_count` alone must not fail the paid evaluation. The CLI writes the complete report and prints all diagnostics before returning `0` for a passing gate or `1` for a failed gate.

- [ ] **Step 5: Test and commit the harness before tuning data**

```powershell
.venv\Scripts\python.exe -m pytest -q tests\test_scenario_calibration_service.py tests\test_run_scenario_bank.py
git add profile_agent/schemas/scenario_calibration_schema.py profile_agent/services/scenario_calibration_service.py tests/fixtures/scenario_rag/retrieval_cases.json tests/test_scenario_calibration_service.py run_scenario_bank.py tests/test_run_scenario_bank.py
git commit -m "feat: add scenario retrieval calibration"
```

---

### Task 5: Tune existing Modules and make eligibility metadata meaningful

**Files:**
- Modify: `profile_agent/knowledge/scenario_banks/ai_application_engineering_2026_h2/modules.json`
- Modify: `profile_agent/knowledge/scenario_banks/ai_application_engineering_2026_h2/constraints.json`
- Modify: `tests/test_scenario_bank_release.py`
- Modify: `tests/fixtures/scenario_rag/retrieval_cases.json`

**Interfaces:**
- Keeps: 10 Scenarios and 35 Modules
- Adds: one reviewed Constraint `coding_architecture_multi_agent_loop`

- [ ] **Step 1: Write release tests that reject wildcard metadata**

Assert that not all 35 Modules share identical `supported_modes`, `supported_requirement_types`, and `difficulties`. Add an explicit reviewed expectation for every one of the 35 `module_id` values; fail on missing or unexpected Module IDs so a prefix-based default cannot silently classify a new Module. Assert coding mode is present only for Modules whose reviewed content can be validly assessed through an executable implementation or code artifact.

- [ ] **Step 2: Strengthen three existing semantic texts**

Use these exact concepts while keeping each Module within one official dimension:

```text
cost_monitor_observability (role_dim_05):
企业 Agent 成本监控，LLM/Tool/API 调用链，Token 成本，Trace，调用归因，异常调用量，预算异常，指标延迟，数据源降级。

cost_monitor_performance (role_dim_06):
企业 Agent 成本优化，LLM/Tool/API 成本，模型路由，缓存，批处理，调用次数，延迟，质量成本权衡，预算治理，持续优化。

coding_agent_architecture (role_dim_01):
AI 编程 Multi-Agent，Planner/Executor/Reviewer/Test，任务分派，handoff，共享状态，状态所有权，冲突处理，循环检测，终止条件，人工 Review。
```

- [ ] **Step 3: Add one Multi-Agent constraint, not a new Module**

```json
{
  "constraint_id": "coding_architecture_multi_agent_loop",
  "scenario_id": "coding_review_agent",
  "module_id": "coding_agent_architecture",
  "evidence_gap_tags": ["Multi-Agent", "循环检测", "终止条件"],
  "difficulty": "intermediate",
  "description": "Executor 与 Reviewer 反复退回任务且没有终止条件",
  "fact": "Executor 与 Reviewer 反复退回任务，工作流目前没有明确的终止条件",
  "source_refs": ["source_internal_review"]
}
```

- [ ] **Step 4: Review metadata per Module, using family policy only as a starting point**

```text
Default review suggestion for architecture/business_modeling/cost_performance:
  modes = scenario, system_design

Default review suggestion for context_tools/safety_evaluation/observability:
  modes = scenario, system_design; coding only when the Module explicitly asks for executable verification

Default review suggestion for ai_delivery Modules:
  modes = scenario, system_design; add coding only when an executable artifact is a valid way to prove the Module's evidence signals

foundation difficulty:
  only Modules with a concept/explanation opening

advanced difficulty:
  only Modules with an advanced reviewed constraint or explicit scale/reliability trade-off
```

These are review defaults, not generation rules. Inspect each Module's `opening_goal`, `evidence_signals`, `critical_errors`, and constraints before writing its final metadata. In particular, never infer `coding` mode from a `coding_*` ID: `coding_agent_architecture` may assess shared state, ownership, handoff, loop detection, and termination more reliably through scenario/system-design questioning than by requiring code.

Map requirement types through the same per-Module review: architecture/business modeling excludes generic `coding`; implementation/delivery retains `implementation` and `debugging` only when supported by observable evidence; `experience_verification` stays only where the Module has project-verifiable signals. Encode the final 35-Module decision table explicitly in `tests/test_scenario_bank_release.py`, and make `modules.json` match that table.

- [ ] **Step 5: Validate, rebuild, and calibrate**

```powershell
.venv\Scripts\python.exe run_scenario_bank.py validate
.venv\Scripts\python.exe -m pytest -q tests\test_scenario_bank_release.py tests\test_scenario_bank_service.py tests\test_qdrant_scenario_store.py
.venv\Scripts\python.exe run_scenario_bank.py rebuild-index --apply
.venv\Scripts\python.exe run_scenario_bank.py evaluate --apply
```

Acceptance: Top-1 acceptable rate `>= 0.75`, Top-3 recall `>= 0.90`, `forbidden_top1_hit_count == 0`, and fallback count `= 0`. Top-3 `forbidden_hit_count` remains a diagnostic only and does not fail the gate. Record provider/model/index versions with the report.

- [ ] **Step 6: Commit**

```powershell
git add profile_agent/knowledge/scenario_banks/ai_application_engineering_2026_h2/modules.json profile_agent/knowledge/scenario_banks/ai_application_engineering_2026_h2/constraints.json tests/test_scenario_bank_release.py tests/fixtures/scenario_rag/retrieval_cases.json
git commit -m "feat: tune scenario module coverage"
```

---

### Task 6: Expose retrieval ranking diagnostics without calling them confidence

**Files:**
- Modify: `profile_agent/schemas/scenario_rag_schema.py`
- Modify: `profile_agent/knowledge/qdrant_scenario_store.py`
- Modify: `tests/test_scenario_rag_schema.py`
- Modify: `tests/test_qdrant_scenario_store.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `ScenarioCandidate.dense_score`
- Produces: `ScenarioCandidate.lexical_score`
- Produces: `ScenarioCandidate.raw_reranker_score`
- Produces: `ScenarioCandidate.normalized_reranker_score`
- Keeps: `ScenarioCandidate.score` as the final within-query combined ranking score
- Produces: `ScenarioCandidateSet.top1_margin`

- [ ] **Step 1: Write failing component-score tests**

Use fixed dense, lexical, and reranker inputs. Assert every candidate carries all available components, `score` matches the current `0.6 * hybrid + 0.4 * normalized_reranker` formula, and `top1_margin == top1.score - top2.score`.

- [ ] **Step 2: Refactor `_rank_points` to preserve components**

Use an internal immutable row rather than a `(unit_id, score)` tuple. Preserve raw reranker values before per-query min-max normalization. If reranking fails, set both reranker fields to `None` and keep the hybrid score.

- [ ] **Step 3: Document semantics**

State explicitly in README:

```text
score and top1_margin are within-query ranking diagnostics.
They are not calibrated confidence and must not be compared across unrelated queries.
```

- [ ] **Step 4: Test and commit**

```powershell
.venv\Scripts\python.exe -m pytest -q tests\test_scenario_rag_schema.py tests\test_qdrant_scenario_store.py
git add profile_agent/schemas/scenario_rag_schema.py profile_agent/knowledge/qdrant_scenario_store.py tests/test_scenario_rag_schema.py tests/test_qdrant_scenario_store.py README.md
git commit -m "feat: expose scenario ranking diagnostics"
```

---

### Task 7: Add candidate-facing copy while keeping deterministic generation

**Files:**
- Modify: `profile_agent/schemas/interview_schema.py`
- Modify: `profile_agent/schemas/scenario_rag_schema.py`
- Modify: `profile_agent/services/interview_planner_service.py`
- Modify: `profile_agent/services/question_generator_service.py`
- Modify: `profile_agent/knowledge/scenario_banks/ai_application_engineering_2026_h2/scenarios.json`
- Modify: `tests/test_interview_planner.py`
- Modify: `tests/test_question_generator_service.py`
- Modify: `tests/test_scenario_bank_release.py`

**Interfaces:**
- Produces: `EvidenceRequirementDraft.candidate_focus: str | None = None`
- Produces: `EvidenceRequirement.candidate_focus: str | None = None`
- Produces: `ScenarioCard.candidate_brief: str | None = None`

- [ ] **Step 1: Add backward-compatible fields and Planner propagation tests**

The Planner uses the same existing structured call to produce a short noun phrase, not a second LLM call. Copy `candidate_focus` from Draft to final Requirement. Legacy plans fall back to `_candidate_focus(description)`.

- [ ] **Step 2: Add reviewed briefs to all 10 Scenario Cards**

Each brief must be one or two candidate-visible sentences containing only the business setting and normal operating goal. It must omit `base_constraints`, Module evidence signals, critical errors, and hidden constraints.

- [ ] **Step 3: Replace the mechanical opening template**

```python
brief = scenario_context.scenario.candidate_brief or scenario_context.business_goal
focus = requirement.candidate_focus or _candidate_focus(requirement.description)
text = f"{brief}如果需要重点处理“{focus}”，你会怎么设计？"
```

Keep the existing safe deterministic follow-up behavior; selected constraints remain the only facts exposed later.

- [ ] **Step 4: Test leakage and natural wording**

Assert the first question contains `candidate_brief` and `candidate_focus`, contains exactly one question mark, and contains none of the Module's `evidence_signals`, `critical_errors`, or unreleased constraint facts.

- [ ] **Step 5: Test and commit**

```powershell
.venv\Scripts\python.exe -m pytest -q tests\test_interview_planner.py tests\test_question_generator_service.py tests\test_scenario_bank_release.py
git add profile_agent/schemas/interview_schema.py profile_agent/schemas/scenario_rag_schema.py profile_agent/services/interview_planner_service.py profile_agent/services/question_generator_service.py profile_agent/knowledge/scenario_banks/ai_application_engineering_2026_h2/scenarios.json tests/test_interview_planner.py tests/test_question_generator_service.py tests/test_scenario_bank_release.py
git commit -m "feat: add candidate-facing scenario copy"
```

---

### Task 8: Add source provenance and production hardening

**Files:**
- Modify: `profile_agent/knowledge/scenario_banks/ai_application_engineering_2026_h2/ScenarioSourceRegistry.json`
- Modify: `profile_agent/knowledge/scenario_banks/ai_application_engineering_2026_h2/scenarios.json`
- Modify: `profile_agent/knowledge/scenario_banks/ai_application_engineering_2026_h2/modules.json`
- Modify: `profile_agent/knowledge/scenario_banks/ai_application_engineering_2026_h2/constraints.json`
- Modify: `profile_agent/llm.py`
- Modify: `.env.example`
- Modify: `profile_agent/schemas/scenario_rag_schema.py`
- Modify: `profile_agent/services/question_context_service.py`
- Modify: `tests/test_llm_error_handling.py`
- Modify: `tests/test_prepare_question_context_service.py`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: externally traceable source records with publisher, URL, publication date, retrieval date, and supported dimensions
- Produces: `LLM_TRACE_ENABLED=false` default
- Narrows: `LockedScenarioContext` to candidate-safe projections only

- [ ] **Step 1: Seed the Scenario registry from already reviewed Role Pack sources**

Import these four existing records with stable IDs and original dates/URLs from `profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2_sources.json`:

```text
jd-agent-2026-219868
shlab-agent-intern-2026-04-28
anthropic-writing-tools-for-agents-2025-09-11
anthropic-demystifying-evals-2026-01-09
```

Before adding any further source, browse and verify it is current and primary/official. Map every Module to at least one role/JD source and one engineering-practice source where applicable. Keep `source_internal_review` only as the record for internal wording approval, never as the sole support for a Module.

- [ ] **Step 2: Make LLM tracing opt-in**

```python
if os.getenv("LLM_TRACE_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
    return
```

Add `LLM_TRACE_ENABLED=false` to `.env.example`. Tests must assert no file is created by default and a JSONL record is created only when explicitly enabled.

- [ ] **Step 3: Remove full canonical objects from `LockedScenarioContext`**

Replace `scenario: ScenarioCard | None` and `module: ScenarioModule | None` with only the fields needed by QuestionGenerator and provenance. Keep complete objects inside `ScenarioSelection` and `ScenarioCatalog`, not inside the candidate-safe hand-off model.

- [ ] **Step 4: Add CI**

Create a Windows or Ubuntu workflow that runs from `keda_Agent_small_step_v04`:

```yaml
- uses: astral-sh/setup-uv@v6
- run: uv sync --frozen --dev
- run: uv run pytest -q
```

CI must not set provider API keys and must not execute paid calibration.

- [ ] **Step 5: Run the full suite and commit**

```powershell
.venv\Scripts\python.exe -m pytest -q
git diff --check
git add profile_agent/knowledge/scenario_banks profile_agent/llm.py .env.example profile_agent/schemas/scenario_rag_schema.py profile_agent/services/question_context_service.py tests/test_llm_error_handling.py tests/test_prepare_question_context_service.py .github/workflows/ci.yml
git commit -m "chore: harden scenario provenance and tracing"
```

Expected baseline: at least `789 passed` and `576 subtests passed`, plus the new tests introduced above.

---

## Execution Order and Gates

```text
P0 gate: Tasks 1–3
  Must prove the exact primary-Requirement answer gap selects the exact next constraint.
  Must prove contradictory preserves valid gap tags, sufficient clears them,
  and secondary Requirements cannot inherit the current Module's gap tags.

P1 measurement gate: Task 4
  Must land before retrieval content or ranking changes.

P1 quality work: Tasks 5–7
  Each task reruns the 24-case calibration; paid runs are explicit.

P2 release gate: Task 8
  Must finish before a public competition Demo using real candidate data.
```

Do not start Tasks 5–7 until Task 4 can record a baseline, otherwise content and ranking changes cannot be compared objectively.

## Self-Review

- Spec coverage: all verified P0/P1/P2 findings are assigned; no new Module, Agent, graph node, vector database, embedding model, ScoreEngine, or Report change is included.
- Existing test correction: the plan retains the current hard-coded Memory retrieval test and adds a reusable 24-case suite rather than claiming no retrieval test exists.
- Score semantics correction: combined scores are documented as within-query ranking diagnostics, not cross-query confidence.
- Type consistency: `missing_evidence_tags` is the AnswerProcessor output; `latest_gap_tags` is the persisted runtime field; `evidence_gap_tags` remains the existing ConstraintSelector input.
- Active-status consistency: `in_progress` and `contradictory` preserve validated Primary Requirement gaps; `sufficient` clears them.
- Requirement scope: only the turn's Primary Requirement may emit Module-scoped gap tags; secondary Requirements may still receive Evidence/status updates.
- Calibration negatives: semantic calibration uses same-dimension forbidden Modules; cross-dimension rejection remains a separate hard-filter guard.
- Metadata governance: family names provide review defaults only; the release test records an explicit decision for all 35 Modules.
- Privacy: paid probe reports and LLM traces remain under ignored `artifacts/`; CI never receives provider keys.
