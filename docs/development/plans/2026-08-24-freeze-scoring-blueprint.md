# Freeze Scoring Blueprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministically convert Planner-owned Role Dimension intent into one frozen `ScoringBlueprint` before the interview and reuse it during report generation without a second semantic model call.

**Architecture:** `EvidenceRequirement.planned_role_dimension_id` is the source of truth for new plans. `build_scoring_blueprint` builds bindings directly when all requirements declare it, while retaining the current LLM path only for legacy plans. A pre-interview node stores the blueprint in `MainState`; report generation validates and reuses that exact object.

**Tech Stack:** Python 3.12, Pydantic v2, LangGraph, unittest/pytest.

## Global Constraints

- Do not call a real model API; use only fakes and deterministic metadata.
- Preserve legacy compatibility: any missing planned dimension keeps the existing LLM fallback.
- Complete new plans must never call `llm_client.structured` for blueprint generation.
- Validate dimension references, duplicate/missing requirements, Role Pack identity, and weights.
- Do not add dependencies or change rubric matching/scoring semantics.

---

### Task 1: Deterministic Blueprint Builder

**Files:**
- Modify: `profile_agent/services/scoring_blueprint_service.py`
- Modify: `tests/test_scoring_blueprint_service.py`

**Interfaces:**
- Consumes: `InterviewPlan`, `RoleCompetencyProfile`, `planned_role_dimension_id`.
- Produces: deterministic-first `build_scoring_blueprint(...) -> ScoringBlueprint`.

- [ ] **Step 1: Write the failing no-LLM test**

Add an LLM fake that raises if called, annotate all three test requirements with `role_dim_01`, `role_dim_01`, `role_dim_02`, and assert the resulting binding order and weights `0.5`, `0.5`, `1.0`.

```python
class FailIfCalledLLM:
    def structured(self, messages, schema):
        raise AssertionError("complete planned bindings must not call LLM")
```

- [ ] **Step 2: Verify RED**

Run `python -m pytest tests/test_scoring_blueprint_service.py -q` and confirm failure occurs because the current service calls the fake.

- [ ] **Step 3: Implement minimal deterministic construction**

Add:

```python
def _draft_from_planned_dimensions(
    plan: InterviewPlan,
    role_profile: RoleCompetencyProfile,
) -> ScoringBlueprintDraft | None:
    """Return None only if at least one requirement lacks planned metadata."""
```

Use `_plan_requirements`, reject declared IDs absent from the Role Pack, and create `RequirementBindingDraft` where `rubric_id == primary_dimension_id`. Use the LLM only when the helper returns `None`. Keep `_validate_draft` and current Python weight normalization for both paths.

- [ ] **Step 4: Cover error and fallback behavior with RED/GREEN tests**

Add exact tests for an unknown planned dimension, a partially annotated legacy plan, and a fully unannotated legacy plan. Both legacy cases must call the fake LLM exactly once.

- [ ] **Step 5: Commit Task 1**

```powershell
git add profile_agent/services/scoring_blueprint_service.py tests/test_scoring_blueprint_service.py
git commit -m "feat: build scoring blueprint from planned dimensions"
```

### Task 2: Freeze Blueprint in Pre-Interview State

**Files:**
- Create: `profile_agent/nodes/scoring_blueprint.py`
- Create: `tests/test_scoring_blueprint_node.py`
- Modify: `profile_agent/state/main_state.py`
- Modify: `profile_agent/graphs/pre_interview.py`
- Modify: `tests/test_pre_interview_graph.py`

**Interfaces:**
- Consumes: `state["interview_plan"]` and Role Pack `ai_application_engineering/2026-H2`.
- Produces: `state["scoring_blueprint"]: ScoringBlueprint`.

- [ ] **Step 1: Write failing graph and node tests**

Assert the graph contains `interview_planner -> scoring_blueprint -> END`. Test the node by patching its builder/loader and asserting it returns the complete blueprint.

- [ ] **Step 2: Verify RED**

Run `python -m pytest tests/test_pre_interview_graph.py tests/test_scoring_blueprint_node.py -q`; confirm failure is caused by the missing node/state edge.

- [ ] **Step 3: Implement node, state field, and graph edge**

```python
def scoring_blueprint(state: MainState) -> dict:
    profile = load_role_profile("ai_application_engineering", "2026-H2")
    blueprint = build_scoring_blueprint(state["interview_plan"], profile)
    return {"scoring_blueprint": blueprint}
```

Add `scoring_blueprint: ScoringBlueprint` to `MainState`; replace the Planner-to-END edge with the two new edges. Run the focused tests to GREEN.

- [ ] **Step 4: Commit Task 2**

```powershell
git add profile_agent/nodes/scoring_blueprint.py profile_agent/state/main_state.py profile_agent/graphs/pre_interview.py tests/test_pre_interview_graph.py tests/test_scoring_blueprint_node.py
git commit -m "feat: freeze scoring blueprint before interview"
```

### Task 3: Reuse Frozen Blueprint During Reporting

**Files:**
- Modify: `profile_agent/services/assessment_report_service.py`
- Modify: `profile_agent/graphs/interview.py`
- Modify: `tests/test_assessment_report_service.py`
- Modify: `tests/test_interview_graph.py`

**Interfaces:**
- Consumes: optional `scoring_blueprint: ScoringBlueprint | None`.
- Produces: one validated Blueprint reused by matcher, assessment builder, and score engine.

- [ ] **Step 1: Write failing service reuse test**

Pass a frozen blueprint plus a `blueprint_builder` that raises. Assert report generation succeeds and downstream fakes receive the frozen bindings. Add a separate assertion that a caller without the optional value still invokes the builder once.

- [ ] **Step 2: Verify RED**

Run `python -m pytest tests/test_assessment_report_service.py -q`; confirm failure because the service does not accept `scoring_blueprint`.

- [ ] **Step 3: Implement optional reuse**

Add the keyword argument. Call `blueprint_builder(plan, profile)` only when it is `None`; otherwise validate the supplied object with `ScoringBlueprint.model_validate`. Always call `_validate_blueprint`.

- [ ] **Step 4: Test and implement graph forwarding**

Require the report-generator spy to receive `kwargs["scoring_blueprint"]` from initial state, then pass `state.get("scoring_blueprint")` in `generate_report_node`. Run `python -m pytest tests/test_interview_graph.py tests/test_interview_report_integration.py -q`.

- [ ] **Step 5: Commit Task 3**

```powershell
git add profile_agent/services/assessment_report_service.py profile_agent/graphs/interview.py tests/test_assessment_report_service.py tests/test_interview_graph.py
git commit -m "feat: reuse frozen scoring blueprint in reports"
```

### Task 4: Offline Regression and Verification

**Files:**
- Modify only compatibility tests/fixtures when strictly required; never weaken assertions.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: clean worktree, exact test counts, no external API use.

- [ ] **Step 1: Run focused tests**

```powershell
python -m pytest tests/test_scoring_blueprint_service.py tests/test_scoring_blueprint_node.py tests/test_pre_interview_graph.py tests/test_assessment_report_service.py tests/test_interview_graph.py tests/test_interview_report_integration.py -q
```

- [ ] **Step 2: Run all offline tests**

Run `python -m pytest -q`. Do not run either real calibration command.

- [ ] **Step 3: Verify compile and diff**

```powershell
python -m compileall -q profile_agent tests
git diff --check
git status --short --branch
```

- [ ] **Step 4: Report commits and remaining risk**

Do not merge or push. Return commit hashes, exact test counts, clean/dirty state, and note that legacy plans can still incur one semantic Blueprint call.
