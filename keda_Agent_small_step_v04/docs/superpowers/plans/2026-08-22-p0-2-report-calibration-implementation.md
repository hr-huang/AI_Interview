# P0-2 Report Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repeatable six-case MiMo calibration harness for the frozen evidence-to-report boundary.

**Architecture:** Version-controlled Python case builders produce frozen `InterviewPlan`, terminal runtime, turns, evidences, claims, and explicit expectations. A runner wraps the existing semantic services to capture Blueprint and RubricMatch outputs, calls the existing deterministic assessment/report pipeline unchanged, evaluates hard business assertions, and writes timestamped artifacts outside Git. Default unit tests use fakes; only the explicit CLI uses the real `mimo-v2.5` API.

**Tech Stack:** Python 3.11+, Pydantic 2, LangChain OpenAI-compatible client, Xiaomi MiMo `mimo-v2.5`, `unittest`, existing report services.

## Global Constraints

- Only support `ai_application_engineering / 2026-H2` in P0-2.
- Do not let MiMo generate candidate answers.
- MiMo may perform semantic Blueprint binding, Rubric matching, and report writing; only the deterministic ScoreEngine may produce levels and numeric scores.
- Do not compare generated narrative text byte-for-byte.
- `UNVERIFIED` must keep `score=None`.
- Every scored conclusion must resolve to existing Evidence IDs.
- Real API tests must remain outside the default offline test suite.
- Do not add a dependency.
- Do not write or log `MIMO_API_KEY`.

---

## File Structure

- Create `profile_agent/calibration/__init__.py`: public calibration exports.
- Create `profile_agent/calibration/schemas.py`: immutable case, expectation, assertion, and run-result contracts.
- Create `profile_agent/calibration/report_cases.py`: six frozen report-boundary cases and shared input builders.
- Create `profile_agent/calibration/report_assertions.py`: pure assertion evaluation with no model calls.
- Create `profile_agent/calibration/report_runner.py`: capture semantic stages and run one case repeatedly.
- Create `profile_agent/calibration/artifacts.py`: JSON/Markdown artifact writer.
- Create `run_report_calibration.py`: explicit real-API command.
- Create `tests/test_calibration_schemas.py`: schema validation tests.
- Create `tests/test_report_calibration_cases.py`: six-case completeness tests.
- Create `tests/test_report_calibration_assertions.py`: business-boundary assertion tests.
- Create `tests/test_report_calibration_runner.py`: fake semantic-service orchestration tests.
- Create `tests/test_calibration_artifacts.py`: artifact serialization tests.
- Create `tests/test_run_report_calibration.py`: CLI argument and exit-code tests without network.
- Modify `.gitignore`: ignore generated `artifacts/calibration/` only.

### Task 1: Define Calibration Contracts

**Files:**
- Create: `profile_agent/calibration/__init__.py`
- Create: `profile_agent/calibration/schemas.py`
- Test: `tests/test_calibration_schemas.py`

**Interfaces:**
- Consumes: existing `InterviewPlan`, `InterviewRuntimeState`, `InterviewTurn`, `Evidence`, `ClaimRegistry`, `AssessmentReport`, `ScoringBlueprint`, and `RubricMatchBatch`.
- Produces: `LevelRange`, `ReportCalibrationExpectation`, `ReportCalibrationCase`, `CalibrationAssertion`, and `ReportCalibrationRun`.

- [ ] **Step 1: Write failing schema tests**

```python
import unittest

from pydantic import ValidationError

from profile_agent.calibration.schemas import LevelRange, ReportCalibrationExpectation


class CalibrationSchemaTest(unittest.TestCase):
    def test_level_range_accepts_ordered_levels(self) -> None:
        value = LevelRange(min_level="L2", max_level="L3")
        self.assertEqual((value.min_level, value.max_level), ("L2", "L3"))

    def test_level_range_rejects_reversed_levels(self) -> None:
        with self.assertRaises(ValidationError):
            LevelRange(min_level="L3", max_level="L2")

    def test_expectation_rejects_conflicting_required_and_forbidden_hits(self) -> None:
        with self.assertRaises(ValidationError):
            ReportCalibrationExpectation(
                required_rubric_hits={"req_01": ["role_dim_01_min_01"]},
                forbidden_rubric_hits={"req_01": ["role_dim_01_min_01"]},
            )
```

- [ ] **Step 2: Run the tests and verify import failure**

Run: `python -m unittest tests.test_calibration_schemas -v`

Expected: FAIL because `profile_agent.calibration.schemas` does not exist.

- [ ] **Step 3: Implement the contracts**

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.schemas.report_schema import AssessmentReport, RubricMatchBatch, ScoringBlueprint
from profile_agent.schemas.runtime_schema import Evidence, InterviewRuntimeState, InterviewTurn

Level = Literal["L0", "L1", "L2", "L3", "L4"]
_LEVEL_ORDER = {level: index for index, level in enumerate(("L0", "L1", "L2", "L3", "L4"))}


class CalibrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LevelRange(CalibrationModel):
    min_level: Level
    max_level: Level

    @model_validator(mode="after")
    def validate_order(self) -> "LevelRange":
        if _LEVEL_ORDER[self.min_level] > _LEVEL_ORDER[self.max_level]:
            raise ValueError("min_level 不能高于 max_level")
        return self


class ReportCalibrationExpectation(CalibrationModel):
    required_rubric_hits: dict[str, list[str]] = Field(default_factory=dict)
    forbidden_rubric_hits: dict[str, list[str]] = Field(default_factory=dict)
    requirement_level_ranges: dict[str, LevelRange] = Field(default_factory=dict)
    expected_unverified_requirements: list[str] = Field(default_factory=list)
    expected_unverified_dimensions: list[str] = Field(default_factory=list)
    job_match_published: bool | None = None
    required_claim_statuses: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_conflicting_hits(self) -> "ReportCalibrationExpectation":
        for requirement_id, required in self.required_rubric_hits.items():
            conflict = set(required) & set(self.forbidden_rubric_hits.get(requirement_id, []))
            if conflict:
                raise ValueError(f"required/forbidden rubric hit 冲突: {sorted(conflict)}")
        return self


class ReportCalibrationCase(CalibrationModel):
    id: str
    title: str
    description: str
    target_role: str
    plan: InterviewPlan
    runtime_state: InterviewRuntimeState
    turns: list[InterviewTurn]
    evidences: list[Evidence]
    claim_registry: ClaimRegistry
    expectation: ReportCalibrationExpectation


class CalibrationAssertion(CalibrationModel):
    code: str
    passed: bool
    message: str


class ReportCalibrationRun(CalibrationModel):
    case_id: str
    run_number: int
    blueprint: ScoringBlueprint
    rubric_matches: RubricMatchBatch
    report: AssessmentReport
    assertions: list[CalibrationAssertion]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.assertions)
```

Export these names from `profile_agent/calibration/__init__.py`.

- [ ] **Step 4: Run the focused tests**

Run: `python -m unittest tests.test_calibration_schemas -v`

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/calibration/__init__.py profile_agent/calibration/schemas.py tests/test_calibration_schemas.py
git commit -m "feat: define report calibration contracts"
```

### Task 2: Build the Six Frozen Cases

**Files:**
- Create: `profile_agent/calibration/report_cases.py`
- Test: `tests/test_report_calibration_cases.py`

**Interfaces:**
- Consumes: `ReportCalibrationCase` and the real `load_role_profile("ai_application_engineering", "2026-H2")` identifiers.
- Produces: `load_report_calibration_cases() -> tuple[ReportCalibrationCase, ...]` and `get_report_calibration_case(case_id: str) -> ReportCalibrationCase`.

- [ ] **Step 1: Write failing completeness tests**

```python
import unittest

from profile_agent.calibration.report_cases import load_report_calibration_cases


class ReportCalibrationCasesTest(unittest.TestCase):
    def test_exactly_six_frozen_cases_exist(self) -> None:
        cases = load_report_calibration_cases()
        self.assertEqual([case.id for case in cases], ["C01", "C02", "C03", "C04", "C05", "C06"])

    def test_all_cases_are_terminal_and_provenance_safe(self) -> None:
        for case in load_report_calibration_cases():
            with self.subTest(case=case.id):
                self.assertTrue(case.runtime_state.stop_requested)
                turn_ids = {turn.id for turn in case.turns}
                requirement_ids = {
                    requirement.id
                    for target in case.plan.targets
                    for requirement in target.evidence_requirements
                }
                self.assertTrue(all(evidence.turn_id in turn_ids for evidence in case.evidences))
                self.assertTrue(all(set(evidence.requirement_ids) <= requirement_ids for evidence in case.evidences))

    def test_candidate_answers_are_frozen_nonempty_text(self) -> None:
        for case in load_report_calibration_cases():
            self.assertTrue(case.turns)
            self.assertTrue(all(turn.answer and turn.answer.strip() for turn in case.turns))
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_report_calibration_cases -v`

Expected: FAIL because `report_cases.py` does not exist.

- [ ] **Step 3: Implement shared case builders**

Implement deterministic helpers `_plan()`, `_terminal_runtime(plan)`, `_turn(...)`, `_evidence(...)`, and `_case(...)`. Use fixed UTC timestamps beginning at `2026-08-22T09:00:00Z`. Use stable IDs `target_01` through `target_06`, `req_01` through `req_06`, `turn_C01_001`, and `ev_C01_001`.

The shared plan must bind one Requirement to each frozen role dimension:

```python
_REQUIREMENTS = (
    ("req_01", "验证状态、节点、工具边界与动态路径设计", "system_design"),
    ("req_02", "验证业务问题到可测任务的建模能力", "scenario"),
    ("req_03", "验证 Context、RAG、记忆与工具集成设计", "system_design"),
    ("req_04", "验证使用 AI 协作交付并以测试日志验收的能力", "project_deep_dive"),
    ("req_05", "验证失败恢复、评测、安全边界与人工接管", "scenario"),
    ("req_06", "验证成本、性能、复杂度与持续演进取舍", "follow_up"),
)
```

- [ ] **Step 4: Encode every case explicitly**

Do not generate answers with a model. Encode the following frozen observations and expectations directly in `report_cases.py`:

| Case | Frozen evidence boundary | Required expectation |
|---|---|---|
| C01 | Concrete state/node/tool boundaries, measurable task modeling, RAG evaluation, test/log delivery, retry/idempotency/human takeover, and cost/latency trade-offs | `req_01..req_06` L3; job match published |
| C02 | Only names Agent/RAG/Workflow/Memory/benchmark, with no concrete implementation or validation | `req_01..req_05` L1 or UNVERIFIED; no excellence hit; job match unpublished |
| C03 | Strong existing workflow explanation, but proposes copying it unchanged into a new regulated scenario | `req_01` L2-L3; transfer signal forbidden; no L4 |
| C04 | Covers state ownership, join semantics, failure recovery, and human takeover but omits optional orchestration variants | `req_01` and `req_05` L3; exhaustive excellence signals are not required |
| C05 | Explicitly allows autonomous high-risk external writes without authorization or human confirmation | `req_05` L0-L1; safety critical-error hit required |
| C06 | Only `req_01` and `req_02` have evidence | dimensions 03-06 UNVERIFIED; job match unpublished |

Each Evidence must quote the corresponding fixed candidate answer through `source_excerpt`; a contradicting safety or transfer statement must use `polarity="contradicting"`. Look up the exact criterion IDs from `load_role_profile(...)` in the builder instead of inventing shortened IDs.

- [ ] **Step 5: Run focused and existing golden-case tests**

Run: `python -m unittest tests.test_report_calibration_cases tests.test_requirement_evidence_assessment_service tests.test_score_engine_service -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add profile_agent/calibration/report_cases.py tests/test_report_calibration_cases.py
git commit -m "test: add six report calibration cases"
```

### Task 3: Implement Pure Calibration Assertions

**Files:**
- Create: `profile_agent/calibration/report_assertions.py`
- Test: `tests/test_report_calibration_assertions.py`

**Interfaces:**
- Consumes: `ReportCalibrationCase`, `ScoringBlueprint`, `RubricMatchBatch`, `Evidence`, and `AssessmentReport`.
- Produces: `evaluate_report_invariants(evidences, report) -> list[CalibrationAssertion]`, `evaluate_report_calibration(...) -> list[CalibrationAssertion]`, and `require_calibration_pass(...) -> None`.

- [ ] **Step 1: Write failing assertion tests**

```python
import unittest

from profile_agent.calibration.report_assertions import require_calibration_pass
from profile_agent.calibration.schemas import CalibrationAssertion


class ReportCalibrationAssertionsTest(unittest.TestCase):
    def test_require_pass_raises_with_failed_codes(self) -> None:
        assertions = [
            CalibrationAssertion(code="evidence_refs", passed=True, message="ok"),
            CalibrationAssertion(code="level:req_05", passed=False, message="expected L0-L1, got L3"),
        ]
        with self.assertRaisesRegex(AssertionError, "level:req_05"):
            require_calibration_pass(assertions)
```

Add fixture-driven tests proving: C04 accepts L3 without every excellence signal; C05 fails when its critical error is absent; C06 fails when an unverified dimension has a numeric score; unknown Evidence references fail.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_report_calibration_assertions -v`

Expected: FAIL because the assertion module does not exist.

- [ ] **Step 3: Implement the evaluator**

```python
def evaluate_report_calibration(
    case: ReportCalibrationCase,
    blueprint: ScoringBlueprint,
    rubric_matches: RubricMatchBatch,
    report: AssessmentReport,
) -> list[CalibrationAssertion]:
    """Return all hard-boundary checks; never call an LLM and never mutate inputs."""


def evaluate_report_invariants(
    evidences: list[Evidence],
    report: AssessmentReport,
) -> list[CalibrationAssertion]:
    """Check provenance, UNVERIFIED scores, and radar reason counts without fixed Requirement IDs."""


def require_calibration_pass(assertions: list[CalibrationAssertion]) -> None:
    failures = [item for item in assertions if not item.passed]
    if failures:
        detail = "; ".join(f"{item.code}: {item.message}" for item in failures)
        raise AssertionError(detail)
```

`evaluate_report_calibration` must include the results of `evaluate_report_invariants` and then emit case-specific codes for `blueprint_coverage`, `required_hit:<req>`, `forbidden_hit:<req>`, `level:<req>`, `unverified_requirement:<req>`, `unverified_dimension:<dim>`, `job_match_publication`, and `claim:<id>`. The shared invariant function emits `evidence_refs`, `unverified_score:<dim>`, and `radar_reason_count:<dim>`. Level ordering must use the same L0-L4 order as `schemas.py`.

- [ ] **Step 4: Run assertion and scoring tests**

Run: `python -m unittest tests.test_report_calibration_assertions tests.test_score_engine_service -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/calibration/report_assertions.py tests/test_report_calibration_assertions.py
git commit -m "feat: assert report calibration boundaries"
```

### Task 4: Add the Capturing Report Runner

**Files:**
- Create: `profile_agent/calibration/report_runner.py`
- Test: `tests/test_report_calibration_runner.py`

**Interfaces:**
- Consumes: a `ReportCalibrationCase`, run count, and injectable Blueprint/Rubric/Writer callables.
- Produces: `run_report_calibration_case(case, runs=1, semantic_services=None) -> list[ReportCalibrationRun]`.

- [ ] **Step 1: Write failing orchestration tests**

Build fake services returning a valid Blueprint, RubricMatchBatch, and deterministic fallback narrative. Assert the runner calls services in order, captures intermediate outputs, returns one `ReportCalibrationRun` per requested repetition, and never changes `case.model_dump()`.

```python
runs = run_report_calibration_case(case, runs=2, semantic_services=fakes)
self.assertEqual(len(runs), 2)
self.assertTrue(all(run.case_id == case.id for run in runs))
self.assertEqual(fakes.calls, ["blueprint", "rubric", "writer"] * 2)
self.assertEqual(case.model_dump(), before)
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_report_calibration_runner -v`

Expected: FAIL because the runner is missing.

- [ ] **Step 3: Implement stage capture without changing production scoring**

```python
def run_report_calibration_case(
    case: ReportCalibrationCase,
    *,
    runs: int = 1,
    semantic_services: object | None = None,
) -> list[ReportCalibrationRun]:
    if runs <= 0:
        raise ValueError("runs 必须大于 0")
    # Resolve defaults to build_scoring_blueprint, match_evidence_to_rubric,
    # and write_report_narrative. Wrap only Blueprint and Rubric functions to
    # retain their validated return values, then delegate the full flow to
    # generate_assessment_report. Evaluate the completed report with the pure
    # assertion module and return one immutable run record.
```

The wrapper must pass the existing function signatures unchanged. Do not reproduce `RequirementEvidenceAssessment`, Claim Verification, ScoreEngine, or writer fallback logic inside the runner.

- [ ] **Step 4: Run runner and report-service tests**

Run: `python -m unittest tests.test_report_calibration_runner tests.test_assessment_report_service -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add profile_agent/calibration/report_runner.py tests/test_report_calibration_runner.py
git commit -m "feat: run captured report calibrations"
```

### Task 5: Persist Safe Calibration Artifacts

**Files:**
- Create: `profile_agent/calibration/artifacts.py`
- Test: `tests/test_calibration_artifacts.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `ReportCalibrationCase` and `ReportCalibrationRun`.
- Produces: `write_report_calibration_artifacts(root: Path, case, runs) -> Path`.

- [ ] **Step 1: Write failing temporary-directory tests**

Use `tempfile.TemporaryDirectory()` and assert creation of `summary.json`, `summary.md`, and per-run `input.json`, `blueprint.json`, `rubric_matches.json`, `report.json`, and `assertions.json`. Assert none of the serialized files contains the configured API key sentinel.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_calibration_artifacts -v`

Expected: FAIL because `artifacts.py` does not exist.

- [ ] **Step 3: Implement deterministic UTF-8 serialization**

```python
def write_report_calibration_artifacts(
    root: Path,
    case: ReportCalibrationCase,
    runs: list[ReportCalibrationRun],
) -> Path:
    case_dir = root / case.id
    case_dir.mkdir(parents=True, exist_ok=True)
    # Use model_dump(mode="json") and json.dumps(..., ensure_ascii=False,
    # indent=2, sort_keys=True). Write only case inputs and model outputs;
    # never read or serialize environment variables.
    return case_dir
```

Generate `summary.md` from assertion codes and pass/fail status, not from an LLM.

- [ ] **Step 4: Ignore generated artifacts**

Append exactly:

```gitignore
artifacts/calibration/
```

- [ ] **Step 5: Run tests and verify Git ignores a probe path**

Run: `python -m unittest tests.test_calibration_artifacts -v`

Run: `git check-ignore artifacts/calibration/probe.json`

Expected: tests PASS and Git prints `artifacts/calibration/probe.json`.

- [ ] **Step 6: Commit**

```powershell
git add .gitignore profile_agent/calibration/artifacts.py tests/test_calibration_artifacts.py
git commit -m "feat: persist calibration artifacts safely"
```

### Task 6: Add the Explicit Real-MiMo CLI

**Files:**
- Create: `run_report_calibration.py`
- Create: `tests/test_run_report_calibration.py`

**Interfaces:**
- Consumes: `--case`, `--runs`, `--artifact-root`, the case registry, runner, and artifact writer.
- Produces: process exit code 0 on all assertions passing, 1 on calibration failure, and 2 on argument/configuration errors.

- [ ] **Step 1: Write failing CLI tests with an injected runner**

Test case selection, positive run-count validation, a passing summary, and a failed assertion exit code. Patch `run_report_calibration_case`; do not call the network.

```python
code = main(["--case", "C04", "--runs", "3"], runner=fake_runner)
self.assertEqual(code, 0)
self.assertEqual(calls, [("C04", 3)])
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_run_report_calibration -v`

Expected: FAIL because the CLI does not exist.

- [ ] **Step 3: Implement the CLI**

```python
def main(
    argv: list[str] | None = None,
    *,
    runner=run_report_calibration_case,
) -> int:
    parser = argparse.ArgumentParser(description="运行真实 MiMo 报告校准")
    parser.add_argument("--case", choices=["ALL", "C01", "C02", "C03", "C04", "C05", "C06"], default="ALL")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/calibration"))
    # Validate runs > 0, execute selected cases, write artifacts, print only
    # case/run/assertion summaries, and return nonzero if any assertion fails.
```

Catch `LLMProviderError`, `OpenAIError`, and `ValueError` at the CLI boundary and print actionable text without credentials.

- [ ] **Step 4: Run CLI unit tests**

Run: `python -m unittest tests.test_run_report_calibration -v`

Expected: all tests PASS without network access.

- [ ] **Step 5: Commit**

```powershell
git add run_report_calibration.py tests/test_run_report_calibration.py
git commit -m "feat: add real report calibration command"
```

### Task 7: Run the Real Baseline and Verify Phase One

**Files:**
- Modify only if a hard calibration assertion exposes a reproducible semantic defect: `profile_agent/services/scoring_blueprint_service.py` or `profile_agent/services/rubric_matcher_service.py`
- Test corresponding service file if changed: `tests/test_scoring_blueprint_service.py` or `tests/test_rubric_matcher_service.py`

**Interfaces:**
- Consumes: configured `MIMO_API_KEY`, six cases, and the real semantic services.
- Produces: a local timestamped baseline under `artifacts/calibration/` and a passing offline test suite.

- [ ] **Step 1: Run the highest-risk single cases once**

Run: `python run_report_calibration.py --case C04 --runs 1`

Expected: process reaches MiMo, writes artifacts, and either passes or reports exact failed assertion codes.

Run: `python run_report_calibration.py --case C05 --runs 1`

Expected: safety critical-error assertion passes.

- [ ] **Step 2: Fix only reproducible semantic defects with TDD**

If C04 or C05 fails, add one failing unit test containing the exact model-shaped structured response that exposed the defect, run it red, make the smallest prompt/validation change, and run it green. Do not widen expected level ranges and do not modify the frozen Role Pack in this task.

- [ ] **Step 3: Run all six cases three times**

Run: `python run_report_calibration.py --case ALL --runs 3`

Expected: 18 runs complete; every hard assertion passes. If provider instability prevents all 18 calls, preserve completed artifacts and report the provider failure separately from a calibration failure.

- [ ] **Step 4: Run complete offline verification**

Run: `python -m unittest discover -s tests -v`

Expected: all tests PASS without depending on calibration artifacts.

Run: `python check_without_llm.py`

Expected: PASS.

Run: `python -m compileall profile_agent run_report_calibration.py`

Expected: compilation succeeds.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 5: Commit any verified defect fix separately**

```powershell
git add profile_agent/services/scoring_blueprint_service.py profile_agent/services/rubric_matcher_service.py tests/test_scoring_blueprint_service.py tests/test_rubric_matcher_service.py
git commit -m "fix: calibrate report semantic boundaries"
```

Skip this commit when no production service changed.

## Completion Gate

Phase one is complete only when all six cases have versioned inputs and expectations, default tests remain offline, C04 demonstrates non-exhaustive L3 behavior, C05 consistently identifies the critical error, C06 remains unverified without numeric scores, and the 18-run real baseline has machine-readable artifacts.
