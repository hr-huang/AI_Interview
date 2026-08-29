# Scenario Module RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct retrieval of fixed interview questions with a versioned six-scenario knowledge base whose retrieval unit is one scenario-capability module, then let deterministic validation and constraint selection ground natural question generation.

**Architecture:** Versioned JSON is the canonical scenario store; Qdrant is a rebuildable index containing one vector per `ScenarioModule`. Supervisor decisions are converted to a narrow retrieval request, exact dimension/mode/type filters run before reranking, a validator reloads the canonical module, and a deterministic selector chooses at most one hidden constraint before the LLM phrases the question.

**Tech Stack:** Python 3.12, Pydantic v2, LangGraph, Qdrant local storage, SiliconFlow BGE-M3 embedding and BGE reranker clients, `unittest`, versioned JSON artifacts.

## Global Constraints

- Only the `ai_agent_engineer` role and role version `2026-H2` are in scope.
- The first release contains exactly six reviewed enterprise scenarios and covers all six `role_dim_01` through `role_dim_06` dimensions at least twice.
- `scenario`, `system_design`, and `coding` may use scenario RAG; `foundation`, `project_deep_dive`, and `follow_up` must bypass it.
- JSON remains the source of truth; do not add PostgreSQL or another dependency.
- Qdrant stores one vector per scenario-capability module, never one vector for a whole multi-capability card.
- The LLM must not choose retrieval filters, fallback modules, scenario switches, or hidden constraints.
- Never send names, schools, raw resumes, raw JDs, or raw candidate answers to embedding or reranking providers.
- The opening question must not reveal `evidence_signals`, `critical_errors`, or unused hidden constraints.
- Every follow-up may reveal at most one new reviewed constraint.
- Preserve the current fixed-question path until the new scenario path passes its end-to-end acceptance tests.

---

## File Structure

New focused files:

- `profile_agent/schemas/scenario_rag_schema.py`: scenario, module, constraint, request, selection, provenance, and manifest contracts.
- `profile_agent/services/scenario_bank_service.py`: canonical JSON loading, referential validation, coverage validation, and ID lookup.
- `profile_agent/knowledge/qdrant_scenario_store.py`: Qdrant module index writer/reader and hybrid candidate retrieval.
- `profile_agent/services/scenario_retrieval_service.py`: request building, safe query projection, reranking, canonical reload, validation, and fallback.
- `profile_agent/services/constraint_selector_service.py`: deterministic one-constraint selection.
- `profile_agent/knowledge/scenario_banks/ai_agent_engineer_2026_h2/`: manifest, scenarios, modules, constraints, and source registry.
- `run_scenario_bank.py`: validate, rebuild-index, and audit CLI.

Existing integration files:

- `profile_agent/schemas/interview_schema.py`: controlled `scenario_strategy` and question provenance fields.
- `profile_agent/schemas/runtime_schema.py`: active scenario/module and revealed-constraint state.
- `profile_agent/state/main_state.py`: transient scenario selection passed between graph nodes.
- `profile_agent/services/supervisor_service.py`: derive `new/continue/switch` without giving the generator planning authority.
- `profile_agent/services/question_generator_service.py`: phrase only locked scenario/module/constraint facts.
- `profile_agent/services/runtime_state_service.py`: persist scenario continuity and used constraint IDs.
- `profile_agent/graphs/interview.py`: replace question retrieval handoff with scenario retrieval, validation, constraint selection, and provenance recording.
- `profile_agent/web/container.py`: lazy scenario retriever wiring while retaining the legacy retriever during migration.

---

### Task 1: Define scenario RAG contracts

**Files:**
- Create: `profile_agent/schemas/scenario_rag_schema.py`
- Modify: `profile_agent/schemas/interview_schema.py`
- Test: `tests/test_scenario_rag_schema.py`
- Test: `tests/test_interview_schema.py`

**Interfaces:**
- Produces: `ScenarioCard`, `ScenarioModule`, `ScenarioConstraint`, `ScenarioRetrievalUnit`, `ScenarioRetrievalRequest`, `ScenarioCandidate`, `ScenarioCandidateSet`, `ScenarioSelection`, `LockedScenarioContext`, `QuestionProvenance`, `ScenarioBankManifest`, and `ScenarioSourceRegistry`.
- Produces: `AskAction.scenario_strategy: Literal["new", "continue", "switch"]` with default `"new"` for checkpoint compatibility.
- Produces: optional provenance fields on `GeneratedQuestion`; the LLM continues to return only `text` and services attach provenance afterward.

- [ ] **Step 1: Write failing schema tests**

```python
def test_module_has_one_official_dimension_and_unique_identity(self):
    module = ScenarioModule(
        module_id="ecommerce_agent_architecture",
        scenario_id="ecommerce_service",
        primary_dimension_id="role_dim_01",
        supported_requirement_types=["system_design"],
        supported_modes=["system_design", "scenario"],
        difficulties=["foundation", "intermediate"],
        opening_goal="验证整体组件划分和任务路由",
        semantic_text="电商客服 Agent 整体架构 任务路由 工具边界 人工接管",
        evidence_signals=["任务拆分", "人工接管"],
        critical_errors=["让模型无约束直接退款"],
        constraint_ids=["refund_timeout_after_success"],
        default_for_dimension=True,
        status="active",
        valid_from=date(2026, 8, 29),
        valid_until=date(2027, 2, 28),
    )
    self.assertEqual(module.retrieval_unit_id, "ecommerce_service::ecommerce_agent_architecture")
    self.assertEqual(module.primary_dimension_id, "role_dim_01")


def test_generated_question_preserves_llm_text_only_compatibility(self):
    self.assertEqual(GeneratedQuestion.model_validate({"text": "问题"}).text, "问题")
```

- [ ] **Step 2: Run tests and verify missing imports fail**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_scenario_rag_schema tests.test_interview_schema`

Expected: FAIL because `scenario_rag_schema` and the new fields do not exist.

- [ ] **Step 3: Implement strict Pydantic contracts**

Define exact enums and immutable identity properties:

```python
ScenarioLifecycleStatus = Literal["active", "needs_review", "retired"]
ScenarioRetrievalStatus = Literal[
    "hit", "fallback", "bypass", "no_match", "unavailable", "invalid_result"
]

class ScenarioModule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    primary_dimension_id: Literal[
        "role_dim_01", "role_dim_02", "role_dim_03",
        "role_dim_04", "role_dim_05", "role_dim_06",
    ]
    supported_requirement_types: list[TargetType] = Field(min_length=1)
    supported_modes: list[QuestionMode] = Field(min_length=1)
    difficulties: list[QuestionDifficulty] = Field(min_length=1)
    opening_goal: str = Field(min_length=1)
    semantic_text: str = Field(min_length=1, max_length=1000)
    evidence_signals: list[str] = Field(min_length=1)
    critical_errors: list[str] = Field(default_factory=list)
    constraint_ids: list[str] = Field(default_factory=list)
    default_for_dimension: bool = False
    status: ScenarioLifecycleStatus
    valid_from: date
    valid_until: date | None = None

    @property
    def retrieval_unit_id(self) -> str:
        return f"{self.scenario_id}::{self.module_id}"
```

`QuestionProvenance` must contain `target_requirement_id`, `primary_dimension_id`, `retrieval_unit_id`, `scenario_id`, `module_id`, `selected_constraint_id`, cumulative `revealed_constraint_ids`, `retrieval_status`, and optional `fallback_reason`.

- [ ] **Step 4: Run schema tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_scenario_rag_schema tests.test_interview_schema`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/schemas/scenario_rag_schema.py profile_agent/schemas/interview_schema.py tests/test_scenario_rag_schema.py tests/test_interview_schema.py
git commit -m "feat: define scenario rag contracts"
```

### Task 2: Build the canonical JSON scenario store

**Files:**
- Create: `profile_agent/services/scenario_bank_service.py`
- Create: `tests/test_scenario_bank_service.py`
- Create: `tests/fixtures/scenario_bank/minimal_valid/ScenarioBankManifest.json`
- Create: `tests/fixtures/scenario_bank/minimal_valid/scenarios.json`
- Create: `tests/fixtures/scenario_bank/minimal_valid/modules.json`
- Create: `tests/fixtures/scenario_bank/minimal_valid/constraints.json`
- Create: `tests/fixtures/scenario_bank/minimal_valid/ScenarioSourceRegistry.json`

**Interfaces:**
- Consumes: contracts from Task 1.
- Produces: `ScenarioCatalog.load(root: Path, as_of: date) -> ScenarioCatalog`.
- Produces: `active_modules`, `get_scenario`, `get_module`, `get_constraint`, `get_retrieval_unit`, `default_module_for_dimension`; the last method is the concrete `DefaultModuleRegistry` view required by the design.

- [ ] **Step 1: Write failing loader and integrity tests**

```python
def test_load_rejects_constraint_owned_by_another_module(self):
    root = self.copy_fixture("minimal_valid")
    replace_json_value(root / "constraints.json", "module_id", "missing_module")
    with self.assertRaisesRegex(ValueError, "constraint.*missing_module"):
        ScenarioCatalog.load(root, as_of=date(2026, 8, 29))


def test_every_dimension_has_two_active_modules(self):
    catalog = ScenarioCatalog.load(CANONICAL_ROOT, as_of=date(2026, 8, 29))
    counts = Counter(m.primary_dimension_id for m in catalog.active_modules)
    self.assertTrue(all(counts[f"role_dim_0{i}"] >= 2 for i in range(1, 7)))
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_scenario_bank_service`

Expected: FAIL because `ScenarioCatalog` does not exist.

- [ ] **Step 3: Implement fail-closed loading and lookup**

The loader must reject duplicate IDs, dangling references, multiple defaults for one dimension, missing defaults, expired active defaults, invalid source IDs, manifest hash mismatches, and a coverage count below two active modules per dimension. It must expose dictionaries keyed by stable IDs and never read Qdrant. Implement `_load_unique_records` as a JSON-list loader that validates every item with the supplied Pydantic model and raises on a repeated ID; implement `_validate_catalog` as the single referential-integrity pass covering those rules.

```python
@dataclass(frozen=True)
class ScenarioCatalog:
    manifest: ScenarioBankManifest
    scenarios: Mapping[str, ScenarioCard]
    modules: Mapping[str, ScenarioModule]
    constraints: Mapping[str, ScenarioConstraint]

    @classmethod
    def load(cls, root: Path, *, as_of: date) -> "ScenarioCatalog":
        manifest = ScenarioBankManifest.model_validate_json(
            (root / "ScenarioBankManifest.json").read_text(encoding="utf-8")
        )
        scenarios = _load_unique_records(root / "scenarios.json", ScenarioCard, "scenario_id")
        modules = _load_unique_records(root / "modules.json", ScenarioModule, "module_id")
        constraints = _load_unique_records(root / "constraints.json", ScenarioConstraint, "constraint_id")
        catalog = cls(manifest, scenarios, modules, constraints)
        _validate_catalog(catalog, root=root, as_of=as_of)
        return catalog

    def resolve(self, retrieval_unit_id: str) -> tuple[ScenarioCard, ScenarioModule]:
        scenario_id, separator, module_id = retrieval_unit_id.partition("::")
        if not separator or module_id not in self.modules:
            raise KeyError(retrieval_unit_id)
        module = self.modules[module_id]
        if module.scenario_id != scenario_id or scenario_id not in self.scenarios:
            raise KeyError(retrieval_unit_id)
        return self.scenarios[scenario_id], module
```

- [ ] **Step 4: Run catalog tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_scenario_bank_service`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/services/scenario_bank_service.py tests/test_scenario_bank_service.py tests/fixtures/scenario_bank
git commit -m "feat: add canonical scenario catalog"
```

### Task 3: Add deterministic retrieval request building and validation

**Files:**
- Create: `profile_agent/services/scenario_retrieval_service.py`
- Test: `tests/test_scenario_retrieval_service.py`

**Interfaces:**
- Consumes: `AskAction`, `InterviewPlan`, `InterviewRuntimeState`, optional safe profile tags, `ScenarioCatalog`.
- Produces: `build_scenario_retrieval_request(action, plan, runtime_state, safe_profile_tags, excluded_retrieval_unit_ids, excluded_scenario_ids) -> ScenarioRetrievalRequest`.
- Produces: `validate_scenario_selection(request, selection, catalog, as_of) -> ScenarioSelection`.
- Produces: `select_fallback_module(request, catalog, reason) -> ScenarioSelection`.

- [ ] **Step 1: Write failing request safety and exact-match tests**

```python
def test_request_uses_current_gap_without_raw_profile_text(self):
    request = build_scenario_retrieval_request(
        action=AskAction(
            target_id="target_01",
            primary_requirement_id="req_01",
            question_mode="system_design",
        ),
        plan=make_plan("role_dim_03", "验证 Memory 写入和删除边界"),
        runtime_state=make_runtime(gaps=["长期记忆", "隐私删除"]),
        safe_profile_tags=["Memory", "RAG"],
    )
    self.assertEqual(request.primary_dimension_id, "role_dim_03")
    self.assertIn("长期记忆", request.semantic_query)
    self.assertNotIn("某大学", request.semantic_query)


def test_validator_rejects_cross_dimension_result(self):
    with self.assertRaisesRegex(ValueError, "primary_dimension_id"):
        validate_scenario_selection(
            request=make_request(primary_dimension_id="role_dim_03"),
            selection=make_selection(module_id="cross_region_observability"),
            catalog=make_catalog(module_dimension="role_dim_05"),
            as_of=date(2026, 8, 29),
        )
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_scenario_retrieval_service`

Expected: FAIL because request building and validation are not implemented.

- [ ] **Step 3: Implement request building and validation**

The semantic query must order content as `objective`, `evidence_gap`, then safe tags; it must not append broad JD/resume terms that are unrelated to the selected requirement. Validation must require exact dimension and compatible mode/type before any selection reaches generation.

```python
def validate_scenario_selection(
    request: ScenarioRetrievalRequest,
    selection: ScenarioSelection,
    catalog: ScenarioCatalog,
    as_of: date,
) -> ScenarioSelection:
    scenario, module = catalog.resolve(selection.retrieval_unit_id)
    if module.primary_dimension_id != request.primary_dimension_id:
        raise ValueError("primary_dimension_id mismatch")
    if request.question_mode not in module.supported_modes:
        raise ValueError("question_mode mismatch")
    if request.requirement_type not in module.supported_requirement_types:
        raise ValueError("requirement_type mismatch")
    if request.difficulty not in module.difficulties:
        raise ValueError("difficulty mismatch")
    return selection
```

- [ ] **Step 4: Run service tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_scenario_retrieval_service`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/services/scenario_retrieval_service.py tests/test_scenario_retrieval_service.py
git commit -m "feat: build safe scenario retrieval requests"
```

### Task 4: Index and retrieve scenario modules in Qdrant

**Files:**
- Create: `profile_agent/knowledge/qdrant_scenario_store.py`
- Modify: `profile_agent/services/scenario_retrieval_service.py`
- Test: `tests/test_qdrant_scenario_store.py`
- Test: `tests/test_scenario_retrieval_service.py`

**Interfaces:**
- Consumes: active `ScenarioModule` objects and embeddings of only `semantic_text`.
- Produces: `QdrantScenarioStore.rebuild(catalog, vectors, manifest_hash)`.
- Produces: `hybrid_search(request, query_vector, as_of, limit=20) -> ScenarioCandidateSet`.
- Produces: `ScenarioRetriever.retrieve(request) -> ScenarioSelection`.

- [ ] **Step 1: Write failing index projection and hard-filter tests**

```python
def test_rebuild_embeds_one_text_per_module_not_whole_scenario(self):
    store.rebuild(catalog, embedder=embedder)
    self.assertEqual(embedder.texts, [m.semantic_text for m in catalog.active_modules])


def test_cross_dimension_candidate_never_reaches_reranker(self):
    result = retriever.retrieve(make_request(primary_dimension_id="role_dim_03"))
    self.assertTrue(reranker.document_ids)
    self.assertTrue(all(
        catalog.modules[id_].primary_dimension_id == "role_dim_03"
        for id_ in reranker.module_ids
    ))
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_qdrant_scenario_store tests.test_scenario_retrieval_service`

Expected: FAIL because the scenario index does not exist.

- [ ] **Step 3: Implement hybrid retrieval**

Reuse the existing SiliconFlow embedding and reranker clients, but keep a separate Qdrant collection such as `interview_scenario_modules`. Apply role, exact dimension, lifecycle, validity, supported mode, supported requirement type, difficulty, and exclusions before dense/BM25/RRF ranking. Send at most 20 eligible module summaries to the reranker and return only stable IDs and audit scores.

```python
payload = {
    "retrieval_unit_id": module.retrieval_unit_id,
    "scenario_id": module.scenario_id,
    "module_id": module.module_id,
    "primary_dimension_id": module.primary_dimension_id,
    "supported_modes": module.supported_modes,
    "supported_requirement_types": module.supported_requirement_types,
    "difficulties": module.difficulties,
    "status": module.status,
    "valid_from": module.valid_from.isoformat(),
    "valid_until": module.valid_until.isoformat() if module.valid_until else None,
    "semantic_text": module.semantic_text,
}
```

- [ ] **Step 4: Run store and retrieval tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_qdrant_scenario_store tests.test_scenario_retrieval_service tests.test_siliconflow_rerank_service`

Expected: PASS without external API calls.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/knowledge/qdrant_scenario_store.py profile_agent/services/scenario_retrieval_service.py tests/test_qdrant_scenario_store.py tests/test_scenario_retrieval_service.py
git commit -m "feat: index scenario modules in qdrant"
```

### Task 5: Add deterministic constraint selection and runtime continuity

**Files:**
- Create: `profile_agent/services/constraint_selector_service.py`
- Modify: `profile_agent/schemas/runtime_schema.py`
- Modify: `profile_agent/services/runtime_state_service.py`
- Test: `tests/test_constraint_selector_service.py`
- Test: `tests/test_runtime_state_service.py`

**Interfaces:**
- Produces: `select_constraint(module, constraints, evidence_gap_tags, revealed_ids, difficulty) -> ScenarioConstraint | None`.
- Produces runtime fields: `active_scenario_id`, `active_module_id`, `active_retrieval_unit_id`, `revealed_constraint_ids`.
- Produces: `record_scenario_question(runtime_state, selection, selected_constraint_id, scenario_strategy) -> InterviewRuntimeState`.

- [ ] **Step 1: Write failing deterministic selection tests**

```python
def test_selects_one_unused_exact_gap_constraint(self):
    selected = select_constraint(
        module=module,
        constraints=[refund_timeout, stale_policy],
        evidence_gap_tags=["幂等", "失败恢复"],
        revealed_ids=[],
        difficulty="intermediate",
    )
    self.assertEqual(selected.constraint_id, "refund_timeout_after_success")


def test_continue_never_reuses_revealed_constraint(self):
    selected = select_constraint(
        module=module,
        constraints=[refund_timeout],
        evidence_gap_tags=["幂等"],
        revealed_ids=["refund_timeout_after_success"],
        difficulty="intermediate",
    )
    self.assertIsNone(selected)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_constraint_selector_service tests.test_runtime_state_service`

Expected: FAIL because selector and continuity state do not exist.

- [ ] **Step 3: Implement stable selection and state recording**

Rank eligible constraints by descending exact gap-tag overlap, ascending difficulty distance, declared `constraint_ids` order, then `constraint_id`. `record_scenario_question` must append at most one new constraint ID and reject a module change when `scenario_strategy="continue"`.

- [ ] **Step 4: Run selector and runtime tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_constraint_selector_service tests.test_runtime_state_service`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/services/constraint_selector_service.py profile_agent/schemas/runtime_schema.py profile_agent/services/runtime_state_service.py tests/test_constraint_selector_service.py tests/test_runtime_state_service.py
git commit -m "feat: select scenario constraints deterministically"
```

### Task 6: Lock question generation to selected facts

**Files:**
- Modify: `profile_agent/services/question_generator_service.py`
- Modify: `profile_agent/schemas/interview_schema.py`
- Test: `tests/test_question_generator_service.py`

**Interfaces:**
- Consumes: `ScenarioSelection`, canonical `ScenarioCard`, `ScenarioModule`, optional selected `ScenarioConstraint`, and current revealed IDs.
- Produces: `GeneratedQuestion` with LLM-generated `text` plus service-attached `QuestionProvenance`.

- [ ] **Step 1: Write failing prompt-boundary and provenance tests**

```python
def test_opening_prompt_hides_rubric_and_unused_constraints(self):
    generate_question(
        action=action,
        plan=plan,
        scenario_context=opening_context,
        llm_client=fake_llm,
    )
    prompt = fake_llm.last_messages[-1][1]
    self.assertIn("支持商品咨询、订单查询和退款", prompt)
    self.assertNotIn("evidence_signals", prompt)
    self.assertNotIn("退款已经成功但响应超时", prompt)


def test_followup_contains_only_selected_constraint(self):
    generated = generate_question(
        action=action,
        plan=plan,
        scenario_context=refund_context,
        llm_client=fake_llm,
    )
    prompt = fake_llm.last_messages[-1][1]
    self.assertIn("退款实际上已经执行成功", prompt)
    self.assertNotIn("售后政策索引尚未刷新", prompt)
    self.assertEqual(generated.provenance.selected_constraint_id, "refund_timeout_after_success")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_question_generator_service`

Expected: FAIL because generation still consumes fixed questions and lacks provenance.

- [ ] **Step 3: Replace fixed-question grounding with scenario grounding**

Keep the structured LLM response as `{"text": "问题文本"}`. The service must attach provenance after validation:

```python
llm_question = llm_client.structured(messages, GeneratedQuestion)
return llm_question.model_copy(update={
    "text": llm_question.text.strip(),
    "provenance": QuestionProvenance(
        target_requirement_id=action.primary_requirement_id,
        primary_dimension_id=module.primary_dimension_id,
        retrieval_unit_id=module.retrieval_unit_id,
        scenario_id=scenario.scenario_id,
        module_id=module.module_id,
        selected_constraint_id=constraint.constraint_id if constraint else None,
        revealed_constraint_ids=revealed_constraint_ids,
        retrieval_status=selection.status,
        fallback_reason=selection.fallback_reason,
    ),
})
```

- [ ] **Step 4: Run generator tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_question_generator_service`

Expected: PASS, including unchanged legacy `GeneratedQuestion(text="问题")` construction.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/services/question_generator_service.py profile_agent/schemas/interview_schema.py tests/test_question_generator_service.py
git commit -m "feat: ground questions in selected scenario facts"
```

### Task 7: Integrate scenario retrieval into the LangGraph interview lifecycle

**Files:**
- Modify: `profile_agent/state/main_state.py`
- Modify: `profile_agent/services/supervisor_service.py`
- Modify: `profile_agent/graphs/interview.py`
- Modify: `profile_agent/web/container.py`
- Test: `tests/test_supervisor_service.py`
- Test: `tests/test_interview_graph.py`
- Test: `tests/test_question_rag_graph_integration.py`
- Test: `tests/test_web_container.py`

**Interfaces:**
- Adds transient `scenario_selection` and canonical `scenario_context` to `MainState`.
- Adds graph nodes `retrieve_scenario`, `validate_scenario`, and `select_constraint` before `generate_question`.
- Preserves legacy question retriever behind a migration flag until Task 9 acceptance passes.

- [ ] **Step 1: Write failing route and continuity tests**

```python
def test_foundation_bypasses_scenario_rag(self):
    state = run_until_question(action_mode="foundation")
    self.assertEqual(scenario_retriever.calls, 0)
    self.assertEqual(state["current_question"].provenance.retrieval_status, "bypass")


def test_continue_uses_current_module_without_retrieval(self):
    state = make_state(active_module_id="ecommerce_agent_architecture")
    result = run_next_question(state, scenario_strategy="continue")
    self.assertEqual(scenario_retriever.calls, 0)
    self.assertEqual(result["current_question"].provenance.module_id, "ecommerce_agent_architecture")
```

- [ ] **Step 2: Run graph tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_supervisor_service tests.test_interview_graph tests.test_question_rag_graph_integration tests.test_web_container`

Expected: FAIL because the scenario nodes and container wiring do not exist.

- [ ] **Step 3: Implement the graph handoff**

Required routing:

```text
supervisor
  -> bypass? generate_question
  -> new/switch? retrieve_scenario -> validate_scenario -> select_constraint
  -> continue? load_active_scenario -> select_constraint
  -> generate_question -> wait_for_answer -> process_answer -> supervisor
```

The graph must clear transient scenario selection after it is copied into the private turn provenance. A retrieval failure must resolve through `ScenarioCatalog.default_module_for_dimension`; it must never pass an unvalidated candidate to generation.

- [ ] **Step 4: Run graph and container tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_supervisor_service tests.test_interview_graph tests.test_question_rag_graph_integration tests.test_web_container`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/state/main_state.py profile_agent/services/supervisor_service.py profile_agent/graphs/interview.py profile_agent/web/container.py tests/test_supervisor_service.py tests/test_interview_graph.py tests/test_question_rag_graph_integration.py tests/test_web_container.py
git commit -m "feat: route interview questions through scenarios"
```

### Task 8: Migrate the reviewed content into six scenario cards

**Files:**
- Create: `profile_agent/knowledge/scenario_banks/ai_agent_engineer_2026_h2/ScenarioBankManifest.json`
- Create: `profile_agent/knowledge/scenario_banks/ai_agent_engineer_2026_h2/scenarios.json`
- Create: `profile_agent/knowledge/scenario_banks/ai_agent_engineer_2026_h2/modules.json`
- Create: `profile_agent/knowledge/scenario_banks/ai_agent_engineer_2026_h2/constraints.json`
- Create: `profile_agent/knowledge/scenario_banks/ai_agent_engineer_2026_h2/ScenarioSourceRegistry.json`
- Test: `tests/test_scenario_bank_release.py`

**Interfaces:**
- Consumes: ScenarioCatalog from Task 2.
- Produces: exactly six active scenarios and exactly twenty-three single-dimension retrieval modules.

- [ ] **Step 1: Write failing release inventory tests**

```python
EXPECTED_SCENARIOS = {
    "ecommerce_service", "travel_recommendation", "enterprise_cost_monitor",
    "enterprise_knowledge_assistant", "marketing_operations", "recruitment_interview",
}

def test_release_has_exact_six_scenarios_and_full_coverage(self):
    catalog = ScenarioCatalog.load(RELEASE_ROOT, as_of=date(2026, 8, 29))
    self.assertEqual(set(catalog.scenarios), EXPECTED_SCENARIOS)
    self.assertEqual(len(catalog.active_modules), 23)
    counts = Counter(m.primary_dimension_id for m in catalog.active_modules)
    self.assertTrue(all(counts[f"role_dim_0{i}"] >= 2 for i in range(1, 7)))
    self.assertGreaterEqual(sum("coding" in m.supported_modes for m in catalog.active_modules), 3)
```

- [ ] **Step 2: Run release test and verify failure**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_scenario_bank_release`

Expected: FAIL because release JSON does not exist.

- [ ] **Step 3: Author the six reviewed cards and module inventory**

Use this exact minimum module allocation:

```text
ecommerce_service: role_dim_01, role_dim_03, role_dim_05, role_dim_06
travel_recommendation: role_dim_01, role_dim_02, role_dim_03, role_dim_06
enterprise_cost_monitor: role_dim_02, role_dim_04, role_dim_05, role_dim_06
enterprise_knowledge_assistant: role_dim_03, role_dim_04, role_dim_05
marketing_operations: role_dim_02, role_dim_04, role_dim_05, role_dim_06
recruitment_interview: role_dim_01, role_dim_02, role_dim_03, role_dim_05
```

Migrate q004 constraints into ecommerce performance, q009 into knowledge-assistant memory, q013 into knowledge-assistant tool engineering, and q023 into cost-monitor observability. Preserve source IDs, publication/verification dates, trust levels, and hashes. Do not copy published interview wording as the generated opening question.

- [ ] **Step 4: Run release and governance tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_scenario_bank_release tests.test_scenario_bank_service`

Expected: PASS with six scenarios and all dimensions covered at least twice.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/knowledge/scenario_banks/ai_agent_engineer_2026_h2 tests/test_scenario_bank_release.py
git commit -m "data: add reviewed agent interview scenarios"
```

### Task 9: Add CLI, rebuild the index, and run acceptance tests

**Files:**
- Create: `run_scenario_bank.py`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `tests/test_run_scenario_bank.py`
- Create: `tests/test_scenario_rag_e2e.py`
- Create: `artifacts/scenario_bank/acceptance_local.json`

**Interfaces:**
- Produces CLI commands `validate`, `rebuild-index`, and `audit`.
- Produces a secret-free acceptance artifact with query, hard filters, candidate IDs, scores, selected module, selected constraint, and final generated provenance.

- [ ] **Step 1: Write failing CLI and zero-cost E2E tests**

```python
def test_validate_command_is_zero_cost(self):
    with patch.dict(os.environ, {}, clear=True):
        result = run_cli(["validate", "--as-of", "2026-08-29"])
    self.assertEqual(result.exit_code, 0)


def test_memory_goal_cannot_select_cross_region_observability(self):
    result = run_zero_cost_e2e(
        dimension="role_dim_03",
        mode="system_design",
        objective="验证短期记忆、长期记忆、写入、召回、更新和删除边界",
    )
    self.assertEqual(result.selection.module.primary_dimension_id, "role_dim_03")
    self.assertNotEqual(result.selection.module.module_id, "cost_monitor_cross_region_observability")
```

- [ ] **Step 2: Run CLI/E2E tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_run_scenario_bank tests.test_scenario_rag_e2e`

Expected: FAIL because CLI and acceptance harness do not exist.

- [ ] **Step 3: Implement CLI and documentation**

`validate` and `audit` must never construct provider clients. `rebuild-index` is the only command that embeds canonical module `semantic_text`. Document:

```text
SCENARIO_RAG_BANK_PATH=profile_agent/knowledge/scenario_banks/ai_agent_engineer_2026_h2
SCENARIO_RAG_INDEX_PATH=data/scenario_rag/qdrant
SCENARIO_RAG_COLLECTION=interview_scenario_modules
SCENARIO_RAG_RERANK_THRESHOLD=
```

Do not write a guessed threshold into the release configuration. An empty value disables real provider retrieval with an explicit configuration error, while zero-cost validation remains available. The acceptance harness must calibrate against labeled positive/negative examples and write the selected value plus sample counts into the artifact before production wiring is enabled.

- [ ] **Step 4: Run all verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_run_scenario_bank tests.test_scenario_rag_e2e
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m compileall -q profile_agent run_scenario_bank.py
git diff --check
```

Expected: all tests PASS, compileall emits no error, and `git diff --check` emits no output.

- [ ] **Step 5: Run one authorized real retrieval acceptance**

Only after zero-cost tests pass and the user authorizes provider cost, run one embedding batch for the reviewed module index and a bounded labeled retrieval set. Do not invoke a generative LLM. The acceptance artifact must prove:

```text
Memory target -> role_dim_03 Memory module
Agent architecture target -> role_dim_01 architecture module
Cost/performance target -> role_dim_06 performance module
No query selects a module from another primary dimension
No API key, raw resume, raw JD, name, or school appears in the artifact
```

- [ ] **Step 6: Commit**

```powershell
git add run_scenario_bank.py .env.example README.md tests/test_run_scenario_bank.py tests/test_scenario_rag_e2e.py artifacts/scenario_bank/acceptance_local.json
git commit -m "test: verify scenario rag end to end"
```

### Task 10: Expose understandable scenario provenance in the report UI

**Files:**
- Modify: `profile_agent/web/schemas.py`
- Modify: `profile_agent/web/report_view.py`
- Modify: `web/src/api/types.ts`
- Modify: `web/src/features/report/InterviewTranscript.tsx`
- Modify: `web/src/features/report/report.css`
- Test: `tests/test_web_report_view.py`
- Test: `web/src/features/report/InterviewTranscript.test.tsx`

**Interfaces:**
- Consumes: private `QuestionProvenance` recorded on interview turns.
- Produces: candidate-safe `question_reason` with scenario title, ability name, and constraint summary; technical IDs appear only inside an expandable audit object.

- [ ] **Step 1: Write failing API projection and UI tests**

```python
def test_report_turn_explains_followup_without_exposing_hidden_constraints(self):
    view = build_report_view(report_with_scenario_turn())
    turn = view.interview_transcript[0]
    self.assertEqual(turn.question_reason.scenario_title, "电商智能客服")
    self.assertEqual(turn.question_reason.ability_name, "Agent架构与任务编排")
    self.assertEqual(turn.question_reason.trigger, "需要继续验证高风险工具的失败恢复")
    self.assertNotIn("expected_signals", turn.model_dump_json())
```

```tsx
it("shows a human explanation before technical audit ids", async () => {
  render(<InterviewTranscript turns={[scenarioTurn]} />)
  expect(screen.getByText("为什么继续追问"))
  expect(screen.getByText("需要继续验证高风险工具的失败恢复"))
  expect(screen.queryByText("ecommerce_service::agent_architecture")).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole("button", { name: "查看技术追溯" }))
  expect(screen.getByText("ecommerce_service::agent_architecture")).toBeInTheDocument()
})
```

- [ ] **Step 2: Run backend and frontend tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_web_report_view
corepack pnpm --dir web test -- InterviewTranscript.test.tsx
```

Expected: FAIL because `question_reason` and the expandable trace do not exist.

- [ ] **Step 3: Implement the safe projection and expandable UI**

The default view shows only business language:

```ts
export type QuestionReason = {
  scenarioTitle: string
  abilityName: string
  trigger: string
  retrievalStatus: "hit" | "fallback" | "bypass"
  audit: {
    retrievalUnitId: string | null
    scenarioId: string | null
    moduleId: string | null
    selectedConstraintId: string | null
  }
}
```

Do not expose unused constraints, expected signals, critical errors, provider scores, or source excerpts in the default report. The technical audit area is collapsed initially and uses the label `查看技术追溯`.

- [ ] **Step 4: Run UI and backend verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_web_report_view
corepack pnpm --dir web test -- InterviewTranscript.test.tsx
corepack pnpm --dir web build
```

Expected: backend test PASS, frontend test PASS, and production build succeeds.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/web/schemas.py profile_agent/web/report_view.py web/src/api/types.ts web/src/features/report/InterviewTranscript.tsx web/src/features/report/InterviewTranscript.test.tsx web/src/features/report/report.css tests/test_web_report_view.py
git commit -m "feat: explain dynamic interview question paths"
```

### Task 11: Retire the fixed-question production path after parity

**Files:**
- Modify: `profile_agent/graphs/interview.py`
- Modify: `profile_agent/web/container.py`
- Modify: `README.md`
- Test: `tests/test_question_rag_graph_integration.py`
- Test: `tests/test_web_container.py`

**Interfaces:**
- Consumes: passing Task 9 acceptance artifact.
- Produces: scenario RAG as the default production path; fixed-question assets remain read-only migration evidence until a separate deletion decision.

- [ ] **Step 1: Write failing default-path tests**

```python
def test_default_container_uses_scenario_retriever_when_configured(self):
    container = WebContainer.default()
    self.assertIsNotNone(container.scenario_retriever)
    self.assertIsNone(container.question_retriever)
```

- [ ] **Step 2: Run tests and verify the legacy path is still active**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_question_rag_graph_integration tests.test_web_container`

Expected: FAIL because the legacy question retriever is still wired.

- [ ] **Step 3: Make scenario RAG the default without deleting history**

Remove the legacy retriever from active graph construction, retain old JSON and diagnostic artifacts for reproducibility, and mark old environment keys as deprecated in README. Do not delete files in this task.

- [ ] **Step 4: Run the full suite and inspect the worktree**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m compileall -q profile_agent run_scenario_bank.py
git diff --check
git status --short
```

Expected: all tests PASS; only files intentionally changed by Task 10 appear in the staged diff; pre-existing untracked traces remain untouched.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/graphs/interview.py profile_agent/web/container.py README.md tests/test_question_rag_graph_integration.py tests/test_web_container.py
git commit -m "refactor: make scenario rag the interview default"
```

## Final Review Gate

Before claiming completion:

1. Confirm the six-scenario coverage test passes for all six dimensions.
2. Confirm the Memory regression cannot select cross-region observability.
3. Confirm opening questions do not contain hidden evidence signals.
4. Confirm each follow-up reveals zero or one new constraint.
5. Confirm `continue` performs no retrieval call.
6. Confirm fallback always names a reviewed default module and records a reason.
7. Confirm every generated question has auditable provenance.
8. Confirm no secret or raw PII appears in query traces or artifacts.
9. Confirm the full test suite, compileall, and diff check pass.
10. Keep legacy fixed-question files until the user separately approves deletion.
