# Report Transcript and Radar Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose every interview turn in both frozen demos and real reports, add a readable transcript UI, and make the radar discoverable, accessible, animated, and collision-free at all supported widths.

**Architecture:** Extend the existing server-side report projection with a typed `interview_transcript` built from the already-persisted `InterviewTurn` and `Evidence` collections. Render that projection through one shared React component used by demo and real reports. Keep score evidence separate from the complete transcript, and fix radar behavior within the existing report component and stylesheet.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, React 19, TypeScript, Vitest, Testing Library, SVG, CSS media queries and keyframes.

## Global Constraints

- Do not modify Supervisor decisions, dynamic routing, question generation, answer analysis, or scoring formulas.
- Demo reports use frozen turns; real reports use persisted candidate turns through the same `ReportViewModel` field and React component.
- Do not call a paid model or change API keys, dependencies, or environment configuration.
- At widths `<= 1100px`, radar and match boundary stack vertically; radar labels are at least 14px on mobile and 15px on desktop.
- Halo animation runs in a staggered sequence for exactly two iterations and is disabled under `prefers-reduced-motion: reduce`.
- Missing evidence means `none`, not a zero score; invalid provenance continues to fail closed.

---

### Task 1: Project every interview turn into the report API

**Files:**
- Modify: `profile_agent/web/report_view.py`
- Test: `tests/test_report_view.py`
- Test: `tests/test_demo_api.py`

**Interfaces:**
- Consumes: `InterviewPlan`, `list[InterviewTurn]`, and `list[Evidence]` already passed to `build_report_view(...)`.
- Produces: `InterviewTranscriptTurnView` and `ReportViewModel.interview_transcript: list[InterviewTranscriptTurnView]`.

- [ ] **Step 1: Write failing projection tests**

Add assertions that a C01 report returns six transcript rows in sequence, preserves the complete question and answer, resolves the requirement label from the plan, and derives `supporting`. Add a turn with no evidence and assert `evidence_ids == []` and `evidence_status == "none"`.

```python
view = build_report_view(run.report, case.plan, case.turns, case.evidences, profile, demo=True)
assert len(view.interview_transcript) == 6
first = view.interview_transcript[0]
assert first.sequence_number == 1
assert first.question == case.turns[0].question
assert first.answer == case.turns[0].answer
assert first.requirement_id == "req_01"
assert first.requirement_label
assert first.evidence_status == "supporting"
```

- [ ] **Step 2: Run tests and confirm the missing field failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_view tests.test_demo_api
```

Expected: failure because `ReportViewModel` has no `interview_transcript`.

- [ ] **Step 3: Add the typed transcript projection**

Define a Pydantic view model with these exact fields:

```python
class InterviewTranscriptTurnView(BaseModel):
    turn_id: str
    sequence_number: int
    question: str
    answer: str | None
    question_mode: str
    requirement_id: str
    requirement_label: str
    asked_at: datetime
    answered_at: datetime | None
    evidence_ids: list[str]
    evidence_status: Literal["supporting", "limiting", "mixed", "none"]
```

Build rows sorted by `sequence_number`. Use all evidences whose `turn_id` matches. Map only supporting polarities to `supporting`, only contradicting polarities to `limiting`, both to `mixed`, and no evidence to `none`. Resolve `requirement_label` from the matching plan requirement description. Reuse the existing history validation before building the projection.

- [ ] **Step 4: Add API coverage for both demo variants**

Assert `/api/demo/assessment` returns six transcript rows and `/api/demo/assessment/boundary` returns its complete frozen transcript. Patch LLM boundaries exactly as the existing zero-API test does, proving neither endpoint calls a model.

- [ ] **Step 5: Run backend tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_view tests.test_demo_api tests.test_report_calibration_cases
```

Expected: all tests pass.

### Task 2: Render one complete transcript component for demo and real reports

**Files:**
- Create: `web/src/features/report/InterviewTranscript.tsx`
- Create: `web/src/features/report/InterviewTranscript.test.tsx`
- Modify: `web/src/api/types.ts`
- Modify: `web/src/features/report/ReportPage.tsx`
- Modify: `web/src/features/report/report.css`

**Interfaces:**
- Consumes: `ReportViewModel.interview_transcript` from Task 1.
- Produces: `InterviewTranscript({ turns, onEvidenceSelect })`, where `onEvidenceSelect(evidenceId)` opens the existing evidence drawer.

- [ ] **Step 1: Add the TypeScript type and a failing component test**

Define `InterviewTranscriptTurnView` with the same names and nullability as the Python model, then add `interview_transcript: InterviewTranscriptTurnView[]` to `ReportViewModel`.

Test two turns: one supporting turn and one `none` turn. Verify the collapsed section title is visible, both full answers appear after expansion, the empty evidence copy reads `未形成评分证据`, and clicking a real evidence calls the callback with its ID.

```tsx
await user.click(screen.getByText('展开查看完整面试记录'))
expect(screen.getByText('候选人的完整回答')).toBeVisible()
expect(screen.getByText('未形成评分证据')).toBeVisible()
await user.click(screen.getByRole('button', { name: /查看证据 ev_001/ }))
expect(onEvidenceSelect).toHaveBeenCalledWith('ev_001')
```

- [ ] **Step 2: Run the focused test and confirm failure**

Run:

```powershell
npm test -- --run src/features/report/InterviewTranscript.test.tsx
```

Expected: failure because the component does not exist.

- [ ] **Step 3: Implement the transcript component**

Render a native `details` element, closed by default. Sort turns by `sequence_number`. Each row displays the human-readable requirement label, question mode, full question, full answer or `未提交回答`, and a localized evidence badge. Render evidence buttons only when evidence IDs exist. Do not repeat score reasons in this component.

- [ ] **Step 4: Insert the transcript before score reasons**

Render `InterviewTranscript` after the radar/match layout and before `评分原因与证据`. Connect evidence clicks to a helper that finds the matching `ReasonView` source and opens the existing drawer. If an evidence is not attached to a score reason, show the transcript without manufacturing a drawer reason.

- [ ] **Step 5: Style for readable editorial hierarchy**

Use at least 16px for question and answer text, 14px for secondary metadata, and 44px minimum interactive controls. Keep the section collapsed by default and avoid internal technical IDs as primary labels.

- [ ] **Step 6: Run component and report tests**

Run:

```powershell
npm test -- --run src/features/report/InterviewTranscript.test.tsx src/features/report/ReportPage.test.tsx
```

Expected: all focused tests pass.

### Task 3: Add accessible radar selection and the two-cycle dynamic halo

**Files:**
- Modify: `web/src/features/report/RadarChart.tsx`
- Modify: `web/src/features/report/RadarChart.test.tsx`
- Modify: `web/src/features/report/report.css`

**Interfaces:**
- Consumes: existing `RadarDimensionView[]` and `onDimensionSelect` callback.
- Produces: mouse and keyboard-selectable SVG axes plus synchronized row selection using the existing callback.

- [ ] **Step 1: Write failing interaction tests**

Verify an axis has `role="button"`, `tabIndex={0}`, and an accessible dimension label. Fire `Enter` and `Space` and assert `onDimensionSelect` is called. Verify each axis exposes a deterministic CSS custom property or class used for its animation delay.

```tsx
const axis = screen.getByRole('button', { name: /AI原生工程交付/ })
fireEvent.keyDown(axis, { key: 'Enter' })
expect(onDimensionSelect).toHaveBeenCalledWith(expect.objectContaining({ dimension_id: 'role_dim_04' }))
```

- [ ] **Step 2: Run the focused radar test and confirm failure**

Run:

```powershell
npm test -- --run src/features/report/RadarChart.test.tsx
```

Expected: failure because SVG axes are not keyboard controls and have no halo delay marker.

- [ ] **Step 3: Restore accessible SVG interaction**

Give each axis group `role="button"`, `tabIndex={0}`, its existing accessible label, and a key handler for `Enter` and `Space`. Keep the table-row button as an additional clear affordance. Increase visible point radius to 8 while retaining at least a 22-radius invisible hit circle.

- [ ] **Step 4: Implement the dynamic halo**

Add a halo circle per axis with `animation-delay: calc(var(--radar-index) * 140ms)`. Use a finite animation such as:

```css
.radar-axis-halo {
  transform-box: fill-box;
  transform-origin: center;
  animation: radar-halo 1.35s ease-out 2;
  animation-delay: calc(var(--radar-index) * 140ms);
}

@media (prefers-reduced-motion: reduce) {
  .radar-axis-halo { animation: none; }
}
```

The keyframes expand and fade the halo without flashing. Hover, focus-visible, and selected states use a stable outline rather than restarting an infinite animation.

- [ ] **Step 5: Increase radar label sizes and preserve synchronized emphasis**

Set SVG labels to 15px on desktop and 14px at `<= 780px`. Hover/focus/selection must emphasize the point, axis label, axis line, and matching table row. The persistent click hint remains visible.

- [ ] **Step 6: Run radar and report tests**

Run:

```powershell
npm test -- --run src/features/report/RadarChart.test.tsx src/features/report/ReportPage.test.tsx
```

Expected: all focused tests pass.

### Task 4: Fix responsive collisions and complete regression verification

**Files:**
- Modify: `web/src/features/report/report.css`
- Test: `web/src/features/report/ReportPage.test.tsx`

**Interfaces:**
- Consumes: the report layout and radar component from Tasks 2 and 3.
- Produces: collision-free layouts at all acceptance widths with no horizontal overflow.

- [ ] **Step 1: Add the outer layout breakpoint**

At `max-width: 1100px`, set `.report-layout-primary` to one column and ensure both `.report-radar-panel` and `.report-match-panel` have `min-width: 0`. Do not wait until the existing 780px mobile breakpoint.

- [ ] **Step 2: Run the full automated suites and build**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_view tests.test_demo_api tests.test_report_calibration_cases
npm test -- --run
npm run build
git diff --check
```

Expected: backend tests pass, all Vitest files pass, Vite production build succeeds, and `git diff --check` reports no whitespace errors.

- [ ] **Step 3: Browser-verify both demo variants and the real shared view**

Use Playwright with the installed Edge executable. At widths 390, 840, 960, 1100, and 1440, assert `document.body.scrollWidth <= window.innerWidth`, radar and match rectangles do not intersect, and radar labels remain inside the content viewport. Expand the complete transcript, verify all six C01 questions and answers, click a radar axis with mouse and keyboard, verify the evidence drawer, switch to C03, and confirm its transcript and `1 / 6` evidence coverage.

- [ ] **Step 4: Verify reduced-motion behavior**

Create a Playwright context with `reducedMotion: 'reduce'` and assert the computed `animation-name` for `.radar-axis-halo` is `none`.

- [ ] **Step 5: Review and commit only Task 4 changes**

Confirm every changed line supports this spec, no environment file or API key changed, and no unrelated user modification was overwritten. Commit only Task 4 files, then report modified files, tests, browser evidence, and remaining limitations to the primary agent. The primary agent still performs final acceptance and does not push the branch.
