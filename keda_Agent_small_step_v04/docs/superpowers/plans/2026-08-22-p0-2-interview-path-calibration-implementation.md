# P0-2 Interview Path Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse the six frozen candidate profiles to verify that the real dynamic interview asks for the necessary evidence before producing a calibrated report.

**Architecture:** Each case adds fixed resume/JD text and deterministic answer rules keyed by Requirement semantics, not generated question wording. A driver runs the real pre-interview graph, real Supervisor, real QuestionGenerator, real AnswerProcessor, and real report pipeline; it resumes LangGraph interrupts with the matching frozen answer, captures the path, applies phase-one report invariants plus stable radar-dimension and path assertions, and writes artifacts through the existing calibration writer.

**Tech Stack:** Python 3.11+, Pydantic 2, LangGraph interrupt/resume, Xiaomi MiMo `mimo-v2.5`, existing pre-interview/interview graphs, `unittest`.

## Global Constraints

- This plan starts only after `2026-08-22-p0-2-report-calibration-implementation.md` passes its completion gate.
- Only support `ai_application_engineering / 2026-H2`.
- Candidate resume, JD, and answers are fixed in source control; MiMo never generates candidate answers.
- Answer selection must not depend on exact generated question text.
- Use the real deterministic Supervisor rules.
- Missing evidence remains `UNVERIFIED`; path calibration must not force coverage.
- Default unit tests remain network-free.
- Do not add a dependency or expose `MIMO_API_KEY`.

---

## File Structure

- Modify `profile_agent/calibration/schemas.py`: add scripted answer and interview-path expectation/run contracts.
- Create `profile_agent/calibration/interview_cases.py`: frozen resume/JD and answer rules for C01-C06.
- Create `profile_agent/calibration/scripted_candidate.py`: deterministic rule matching and answer selection.
- Create `profile_agent/calibration/interview_assertions.py`: path coverage and repetition checks.
- Create `profile_agent/calibration/interview_runner.py`: drive pre-interview and LangGraph interrupt/resume.
- Modify `profile_agent/calibration/artifacts.py`: add interview-path artifact files.
- Create `run_interview_calibration.py`: explicit real-API command.
- Create corresponding focused tests under `tests/`.

### Task 1: Add Scripted Interview Contracts

**Files:**
- Modify: `profile_agent/calibration/schemas.py`
- Test: `tests/test_interview_calibration_schemas.py`

**Interfaces:**
- Produces: `ScriptedAnswerRule`, `InterviewPathExpectation`, `InterviewCalibrationCase`, and `InterviewCalibrationRun`.

- [ ] **Step 1: Write failing schema tests**

Test that answer rules require at least one semantic match term, reject an empty answer, and reject overlapping required/forbidden path topics.

```python
rule = ScriptedAnswerRule(
    id="C03_transfer",
    match_any=["迁移", "新场景", "适配"],
    answer="我会直接复制旧流程，不重新验证。",
    max_uses=1,
)
self.assertEqual(rule.max_uses, 1)
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_interview_calibration_schemas -v`

Expected: FAIL because the new contracts do not exist.

- [ ] **Step 3: Implement exact contracts**

```python
class ScriptedAnswerRule(CalibrationModel):
    id: str
    match_any: list[str]
    answer: str
    max_uses: int = 1

class InterviewPathExpectation(CalibrationModel):
    required_topics: dict[str, list[str]] = Field(default_factory=dict)
    forbidden_repeated_topics: list[str] = Field(default_factory=list)
    radar_level_ranges: dict[str, LevelRange] = Field(default_factory=dict)
    expected_unverified_dimensions: list[str] = Field(default_factory=list)
    required_critical_dimensions: list[str] = Field(default_factory=list)
    job_match_published: bool | None = None
    max_questions: int

class InterviewCalibrationCase(CalibrationModel):
    id: str
    title: str
    resume_text: str
    jd_text: str
    target_role: str
    answer_rules: list[ScriptedAnswerRule]
    path_expectation: InterviewPathExpectation

class InterviewCalibrationRun(CalibrationModel):
    case_id: str
    run_number: int
    initial_state: dict
    final_state: dict
    assertions: list[CalibrationAssertion]
```

Add validators for stripped nonempty strings, `match_any`, unique rule IDs, and positive `max_uses`/`max_questions`.

- [ ] **Step 4: Run tests and commit**

Run: `python -m unittest tests.test_interview_calibration_schemas -v`

Expected: all tests PASS.

```powershell
git add profile_agent/calibration/schemas.py tests/test_interview_calibration_schemas.py
git commit -m "feat: define scripted interview calibration contracts"
```

### Task 2: Implement Deterministic Candidate Answer Selection

**Files:**
- Create: `profile_agent/calibration/scripted_candidate.py`
- Test: `tests/test_scripted_candidate.py`

**Interfaces:**
- Consumes: interrupt payload, current `InterviewPlan`, rules, and prior usage counts.
- Produces: `select_scripted_answer(...) -> tuple[str, str]`, returning answer text and matched rule ID.

- [ ] **Step 1: Write failing selector tests**

Cover matching against Requirement description and action reason, case-insensitive matching, max-use exhaustion, ambiguous equal matches, and no-match failure. Exact question wording must not be part of the match input.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_scripted_candidate -v`

Expected: FAIL because the selector module is missing.

- [ ] **Step 3: Implement deterministic scoring**

```python
def select_scripted_answer(
    *,
    payload: Mapping[str, object],
    plan: InterviewPlan,
    rules: list[ScriptedAnswerRule],
    usage_counts: Mapping[str, int],
) -> tuple[str, str]:
    action = AskAction.model_validate(payload["action"])
    requirement = next(
        requirement
        for target in plan.targets
        for requirement in target.evidence_requirements
        if requirement.id == action.primary_requirement_id
    )
    haystack = f"{requirement.description}\n{action.reason}".casefold()
    candidates = []
    for index, rule in enumerate(rules):
        if usage_counts.get(rule.id, 0) >= rule.max_uses:
            continue
        hit_count = sum(term.casefold() in haystack for term in rule.match_any)
        if hit_count:
            candidates.append((-hit_count, index, rule))
    if not candidates:
        raise ScriptedAnswerSelectionError(
            f"没有脚本回答可匹配 requirement {action.primary_requirement_id}"
        )
    _, _, selected = min(candidates)
    return selected.answer, selected.id
```

Raise on duplicate Requirement IDs and malformed interrupt payloads.

- [ ] **Step 4: Run tests and commit**

Run: `python -m unittest tests.test_scripted_candidate -v`

Expected: all tests PASS.

```powershell
git add profile_agent/calibration/scripted_candidate.py tests/test_scripted_candidate.py
git commit -m "feat: select frozen candidate answers deterministically"
```

### Task 3: Define Six Interview Cases

**Files:**
- Create: `profile_agent/calibration/interview_cases.py`
- Test: `tests/test_interview_calibration_cases.py`

**Interfaces:**
- Produces: `load_interview_calibration_cases() -> tuple[InterviewCalibrationCase, ...]` and `get_interview_calibration_case(case_id: str)`.

- [ ] **Step 1: Write failing registry tests**

Assert exact IDs C01-C06, nonempty resume/JD, unique answer rule IDs, and explicit path expectations for C03 migration, C05 safety, and C06 partial coverage.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_interview_calibration_cases -v`

Expected: FAIL because the registry is absent.

- [ ] **Step 3: Encode frozen inputs**

Use one common AI Agent JD covering all six frozen dimensions. Give each case a concise resume matching its profile and fixed answer rules covering every planned Requirement it is allowed to answer. C02 answers must remain keyword-only; C03 must include one strong project answer and one weak transfer answer; C05 must contain the exact unsafe recommendation; C06 rules must answer only the two in-scope dimensions and use explicit low-information answers for the rest rather than fabricate expertise.

Set path expectations with stable Role Pack dimension IDs rather than phase-one Requirement IDs, because the real Planner creates IDs such as `target_01_req_01` dynamically:

```python
{
    "C01": {"required_topics": {"agent": ["状态", "节点"], "reliability": ["恢复", "安全"]}},
    "C02": {"required_topics": {"depth_probe": ["具体", "验证", "取舍"]}},
    "C03": {"required_topics": {"transfer": ["迁移", "新场景", "适配"]}, "radar_level_ranges": {"role_dim_01": LevelRange(min_level="L2", max_level="L3")}},
    "C04": {"required_topics": {"boundary": ["状态", "恢复", "人工"]}},
    "C05": {"required_topics": {"safety": ["高风险", "授权", "人工确认"]}, "required_critical_dimensions": ["role_dim_05"]},
    "C06": {"required_topics": {}, "forbidden_repeated_topics": ["未验证"], "expected_unverified_dimensions": ["role_dim_03", "role_dim_04", "role_dim_05", "role_dim_06"], "job_match_published": False},
}
```

- [ ] **Step 4: Run tests and commit**

Run: `python -m unittest tests.test_interview_calibration_cases -v`

Expected: all tests PASS.

```powershell
git add profile_agent/calibration/interview_cases.py tests/test_interview_calibration_cases.py
git commit -m "test: add six scripted interview cases"
```

### Task 4: Add Path Assertions

**Files:**
- Create: `profile_agent/calibration/interview_assertions.py`
- Test: `tests/test_interview_calibration_assertions.py`

**Interfaces:**
- Consumes: case, final LangGraph state, selected rule IDs, and phase-one `evaluate_report_invariants`.
- Produces: `evaluate_interview_path(...) -> list[CalibrationAssertion]`.

- [ ] **Step 1: Write failing path tests**

Test required topic coverage, question-limit enforcement, no unanswered final turns, no duplicate exhausted rules, C03 migration coverage, C05 safety coverage, and C06 preservation of unverified dimensions.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_interview_calibration_assertions -v`

Expected: FAIL because the assertion module is absent.

- [ ] **Step 3: Implement pure path evaluation**

```python
def evaluate_interview_path(
    case: InterviewCalibrationCase,
    final_state: Mapping[str, object],
    selected_rule_ids: list[str],
) -> list[CalibrationAssertion]:
    turns = [InterviewTurn.model_validate(item) for item in final_state.get("interview_turns", [])]
    evidences = [Evidence.model_validate(item) for item in final_state.get("evidences", [])]
    # Emit independent assertion codes: terminal_state, question_limit,
    # answered_turns, required_topic:<topic>, repeated_topic:<topic>,
    # evidence_provenance, scripted_rule_usage, radar_level:<dimension>,
    # critical_dimension:<dimension>, and job_match_publication. Append the
    # shared evaluate_report_invariants(evidences, assessment_report) results.
```

Topic matching may inspect generated question plus Requirement description for assertions, but answer selection remains independent of question wording.

- [ ] **Step 4: Run tests and commit**

Run: `python -m unittest tests.test_interview_calibration_assertions -v`

Expected: all tests PASS.

```powershell
git add profile_agent/calibration/interview_assertions.py tests/test_interview_calibration_assertions.py
git commit -m "feat: assert dynamic interview paths"
```

### Task 5: Drive the Real Graph with Frozen Answers

**Files:**
- Create: `profile_agent/calibration/interview_runner.py`
- Test: `tests/test_interview_calibration_runner.py`

**Interfaces:**
- Consumes: case, run number, injectable pre-interview runner, graph builder, and answer selector.
- Produces: `run_interview_calibration_case(...) -> InterviewCalibrationRun`.

- [ ] **Step 1: Write a failing offline integration test**

Use a fake pre-interview result and `build_interview_graph` with fake question, answer-processing, and report services. Assert the driver resumes every interrupt, records selected rules, reaches `assessment_report`, and evaluates both path and report assertions.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_interview_calibration_runner -v`

Expected: FAIL because the runner does not exist.

- [ ] **Step 3: Implement the interrupt loop**

```python
def run_interview_calibration_case(
    case: InterviewCalibrationCase,
    *,
    run_number: int = 1,
    pre_interview_runner=pre_interview_graph.invoke,
    graph_builder=build_interview_graph,
    answer_selector=select_scripted_answer,
) -> InterviewCalibrationRun:
    initial_state = pre_interview_runner({
        "resume_text": case.resume_text,
        "jd_text": case.jd_text,
        "target_role": case.target_role,
    })
    graph = graph_builder()
    config = {"configurable": {"thread_id": f"calibration-{case.id}-{run_number}"}}
    result = graph.invoke(initial_state, config)
    usage_counts: dict[str, int] = {}
    selected_rule_ids: list[str] = []
    while payload := extract_interrupt_payload(result):
        answer, rule_id = answer_selector(
            payload=payload,
            plan=initial_state["interview_plan"],
            rules=case.answer_rules,
            usage_counts=usage_counts,
        )
        usage_counts[rule_id] = usage_counts.get(rule_id, 0) + 1
        selected_rule_ids.append(rule_id)
        result = graph.invoke(Command(resume=answer), config)
    return build_interview_calibration_run(case, run_number, initial_state, result, selected_rule_ids)
```

Set a hard defensive ceiling of `case.path_expectation.max_questions + 1` resumes and fail clearly if exceeded.

- [ ] **Step 4: Run integration tests and commit**

Run: `python -m unittest tests.test_interview_calibration_runner tests.test_interview_graph tests.test_interview_report_integration -v`

Expected: all tests PASS.

```powershell
git add profile_agent/calibration/interview_runner.py tests/test_interview_calibration_runner.py
git commit -m "feat: drive calibrated interview sessions"
```

### Task 6: Persist Path Artifacts and Add CLI

**Files:**
- Modify: `profile_agent/calibration/artifacts.py`
- Create: `run_interview_calibration.py`
- Test: `tests/test_interview_calibration_artifacts.py`
- Test: `tests/test_run_interview_calibration.py`

**Interfaces:**
- Produces: `write_interview_calibration_artifacts(...) -> Path` and CLI exit codes matching the report-calibration command.

- [ ] **Step 1: Write failing artifact and CLI tests**

Assert artifacts contain `initial_state.json`, `turns.json`, `evidences.json`, `report.json`, `selected_rules.json`, and `assertions.json`; patch the runner in CLI tests so no network call occurs.

- [ ] **Step 2: Run and verify failure**

Run: `python -m unittest tests.test_interview_calibration_artifacts tests.test_run_interview_calibration -v`

Expected: FAIL because the writer/CLI are absent.

- [ ] **Step 3: Implement safe serialization and CLI**

Reuse the phase-one JSON helper and `artifacts/calibration/<timestamp>/<case>/run-<NN>/` layout. CLI arguments are `--case`, `--runs`, and `--artifact-root`; return 0 only when all path and report assertions pass, 1 for assertion/provider failures, and 2 for invalid arguments. Never serialize environment variables.

- [ ] **Step 4: Run focused tests and commit**

Run: `python -m unittest tests.test_interview_calibration_artifacts tests.test_run_interview_calibration -v`

Expected: all tests PASS.

```powershell
git add profile_agent/calibration/artifacts.py run_interview_calibration.py tests/test_interview_calibration_artifacts.py tests/test_run_interview_calibration.py
git commit -m "feat: add interview path calibration command"
```

### Task 7: Run the Six Real Interview Calibrations

**Files:**
- Modify only when a reproducible defect is isolated: the responsible service and its focused test.

**Interfaces:**
- Consumes: configured MiMo API, six scripted cases, and completed phase-one calibration.
- Produces: six complete dynamic interview artifacts and a passing regression suite.

- [ ] **Step 1: Run C03 and C05 first**

Run: `python run_interview_calibration.py --case C03 --runs 1`

Expected: the path includes a migration probe and the report does not publish L4 transfer capability.

Run: `python run_interview_calibration.py --case C05 --runs 1`

Expected: the path validates safety/reliability and the critical error reaches the final report.

- [ ] **Step 2: Isolate failures by stage**

For each failure, use its assertion code to classify it as answer selection, Supervisor path, QuestionGenerator, AnswerProcessor Evidence, semantic report matching, or deterministic scoring. Add a failing focused test to the responsible module before changing production code. Do not alter frozen candidate answers to make the system pass unless the answer rule failed to represent the approved case definition.

- [ ] **Step 3: Run all six full paths once**

Run: `python run_interview_calibration.py --case ALL --runs 1`

Expected: all six sessions terminate, produce reports, and pass both path and report hard assertions.

- [ ] **Step 4: Run final verification**

Run: `python -m unittest discover -s tests -v`

Expected: all tests PASS offline.

Run: `python check_without_llm.py`

Expected: PASS.

Run: `python -m compileall profile_agent run_report_calibration.py run_interview_calibration.py`

Expected: compilation succeeds.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 5: Commit each isolated production fix separately**

Use a commit message naming the corrected boundary, for example:

```powershell
git commit -m "fix: preserve unverified interview dimensions"
```

## Completion Gate

Phase two is complete only when all six real scripted interviews terminate, C03 includes transfer validation, C05 exposes the safety boundary, C06 does not fabricate coverage, every final conclusion is Evidence-backed, and the complete offline regression suite passes.
