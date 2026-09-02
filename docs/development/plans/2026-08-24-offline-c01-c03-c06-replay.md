# Offline C01 C03 C06 Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a zero-API replay path that executes the current Blueprint, evidence assessment, score engine, radar, and report invariants for C01, C03, and C06.

**Architecture:** Reuse the existing frozen report cases as questions, free-form answer snapshots, and Evidence inputs. Annotate their Requirements with the Role Dimension intent now emitted by Planner, let the production deterministic Blueprint builder and all deterministic downstream scoring services run unchanged, and replace only the semantic RubricMatcher/narrative calls with explicit frozen calibration services. Add C03 assertions proving a scenario transfer probe becomes limiting evidence.

**Tech Stack:** Python 3.12, Pydantic v2, unittest/pytest, argparse.

## Global Constraints

- Never read provider configuration or call a real LLM/API.
- Do not claim this validates arbitrary-answer semantic extraction; it validates the frozen question/evidence-to-radar contract.
- Use only the current single Role Pack `ai_application_engineering/2026-H2`.
- Preserve the existing paid calibration commands unchanged.

---

### Task 1: Frozen Cases and Offline Semantic Boundary

**Files:**
- Modify: `profile_agent/calibration/report_cases.py`
- Modify: `profile_agent/calibration/schemas.py`
- Modify: `profile_agent/calibration/report_assertions.py`
- Create: `profile_agent/calibration/offline_services.py`
- Test: `tests/test_offline_calibration_services.py`
- Test: `tests/test_report_calibration_cases.py`
- Test: `tests/test_report_calibration_assertions.py`

**Interfaces:**
- Consumes: `ReportCalibrationCase` with frozen turns, evidences, expectations.
- Produces: `build_offline_semantic_services(case) -> dict[str, Callable]` containing only `rubric_matcher` and `narrative_writer`.

- [ ] **Step 1: Write failing tests**

Assert every frozen Requirement has `planned_role_dimension_id` equal to `_DIMENSION_BY_REQUIREMENT`; C03 `req_01` requires a `scenario` turn and its contradicting Evidence ID appears in `limiting_evidence_ids`. Assert the offline services mapping contains no Blueprint builder and never touches the global LLM.

- [ ] **Step 2: Verify RED**

Run `python -m pytest tests/test_offline_calibration_services.py tests/test_report_calibration_cases.py tests/test_report_calibration_assertions.py -q`; expected failure is missing offline services/metadata/assertion fields.

- [ ] **Step 3: Implement minimal metadata and services**

Set each frozen `EvidenceRequirement.planned_role_dimension_id` from `_DIMENSION_BY_REQUIREMENT`. Extend `ReportCalibrationExpectation` with:

```python
required_question_modes: dict[str, list[QuestionMode]] = Field(default_factory=dict)
required_limiting_evidence_ids: dict[str, list[str]] = Field(default_factory=dict)
```

Generate one `RubricMatch` per frozen Evidence. Use only the case's required rubric IDs, strong quality for supporting Evidence, unverified transferability except for explicitly successful scenario transfer, and deterministic `fallback_report_narrative`. Do not expose a Blueprint builder so the production deterministic builder is exercised.

- [ ] **Step 4: Run focused tests to GREEN**

Run the same focused command and confirm all pass.

### Task 2: Zero-API Runner and CLI

**Files:**
- Create: `profile_agent/calibration/offline_runner.py`
- Create: `run_offline_calibration.py`
- Create: `tests/test_offline_calibration_runner.py`
- Create: `tests/test_run_offline_calibration.py`

**Interfaces:**
- Consumes: case IDs `C01`, `C03`, `C06` and frozen cases.
- Produces: `run_offline_calibration_case(case) -> ReportCalibrationRun` and CLI exit code 0/1/2.

- [ ] **Step 1: Write failing runner and CLI tests**

Assert the runner passes all three cases, Blueprint bindings come from planned dimensions, C03 has transfer limiting evidence, C06 keeps four dimensions `UNVERIFIED`, and a fake global LLM that raises is never called. Assert CLI defaults to all three cases, supports `--case`, prints PASS/FAIL, rejects an invalid case, and does not require an API key.

- [ ] **Step 2: Verify RED**

Run `python -m pytest tests/test_offline_calibration_runner.py tests/test_run_offline_calibration.py -q`; expected failure is missing modules.

- [ ] **Step 3: Implement runner and CLI**

The runner calls existing `run_report_calibration_case(case, semantic_services=build_offline_semantic_services(case))` for one run and returns it. The CLI only selects `C01/C03/C06`, runs them, prints every assertion, and returns 1 when any assertion fails; it must not validate environment variables or write paid calibration artifacts.

- [ ] **Step 4: Run the actual zero-API replay**

Run `python run_offline_calibration.py --case ALL`; expected output contains PASS for C01, C03, and C06 with exit code 0.

- [ ] **Step 5: Run full verification and commit**

Run `python -m pytest -q`, `python -m compileall -q profile_agent tests`, and `git diff --check`; then commit all scoped files with `feat: add zero-api assessment replay`.
