# Planner Deterministic Guardrails Implementation Plan

**Goal:** Make gating-dimension coverage, project-claim transfer validation, and question-capacity reservation enforceable by Python rather than relying on prompt wording.

**Architecture:** Keep the LLM responsible for semantic planning, but require every Evidence Requirement to declare its planned Role Pack dimension and whether it is a transfer probe. Validate these declarations before finalizing the plan. Carry the metadata into runtime so the deterministic Supervisor can prioritize and ask transfer requirements as scenario questions. Keep the existing question-capacity validator as the single budget boundary.

**Tech Stack:** Python 3.12, Pydantic, LangGraph state/services, unittest/pytest.

---

### Task 1: Add explicit planning-intent contracts

**Files:**
- Modify: `profile_agent/schemas/interview_schema.py`
- Test: `tests/test_interview_planner_guards.py`

1. Write failing schema/finalization tests proving planned dimension and transfer metadata survive Draft -> InterviewPlan.
2. Add `planned_role_dimension_id` and `requires_transfer_validation` to Evidence Requirement Draft/final models.
3. Copy both fields in `finalize_interview_plan`.
4. Run the focused tests.

### Task 2: Enforce Role Pack and transfer guardrails

**Files:**
- Modify: `profile_agent/services/interview_planner_service.py`
- Modify: `profile_agent/nodes/interview_planner.py`
- Test: `tests/test_interview_planner_guards.py`

1. Write failing tests for unknown Role Dimension IDs, missing gating dimensions, and a high/must-cover project-claim target without a transfer requirement/scenario mode.
2. Load the frozen AI Application Engineer Role Pack for planning and include it in Planner context.
3. Add deterministic validators and run them inside the existing one-retry business-validation loop.
4. Retain `validate_question_capacity` as the deterministic requirement-count limit and add a combined passing case.
5. Run focused Planner and pre-interview tests.

### Task 3: Make Supervisor honor transfer intent

**Files:**
- Modify: `profile_agent/services/supervisor_service.py`
- Test: `tests/test_supervisor_service.py`

1. Write failing tests showing a transfer requirement is prioritized within the same priority tier and uses `scenario`, even when its target also links a project claim.
2. Carry the transfer flag into `SupervisorRequirementContext`, sorting and mode selection.
3. Run focused Supervisor tests.

### Task 4: Offline regression and handoff

**Files:**
- Modify only if fixtures require explicit compatibility updates.

1. Run all offline tests without invoking any external model API.
2. Run strict warning/error checks already used by the project if available.
3. Review the diff for secrets and unrelated changes.
4. Commit the guardrail change as one isolated commit and report behavior, tests, and remaining semantic-model risk.
