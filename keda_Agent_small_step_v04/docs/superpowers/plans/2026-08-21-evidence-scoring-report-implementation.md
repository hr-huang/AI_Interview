# Evidence-Driven Assessment Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Execution should use isolated Codex Luna Max worktrees as requested by the user; do not use collaboration sub-agents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将动态面试产生的 Evidence 确定性转换为可解释能力等级、雷达数据、岗位匹配度和结构化胜任力报告。

**Architecture:** LLM 只负责 Requirement-to-Dimension 语义绑定、Evidence-to-Rubric 结构化匹配和受约束的自然语言表达。Python 负责全部 ID 校验、等级、分数、覆盖率、置信度、Claim 聚合、岗位适配裁决和报告引用校验。

**Tech Stack:** Python 3.11+、Pydantic 2、现有 LLM wrapper、现有 InterviewPlan/Turn/Evidence/RuntimeState、unittest、JSON Role Pack。

## Global Constraints

- 报告是 InterviewGraph 结束后的独立阶段，不修改现有 PreInterviewGraph 或动态面试循环。
- 不向现有 Evidence 写入分数；新对象统一放入 profile_agent/schemas/report_schema.py。
- UNVERIFIED 没有展示分，不得作为 0 聚合，不得生成负向结论。
- LLM 不得直接输出 Requirement 分、能力分或岗位匹配度。
- 同一结构化评分输入必须得到完全一致的 Python 分数、等级和 Evidence 引用。
- 每个已评分雷达维度至少提供两条 ScoreReason。
- strength/risk/critical_error 原因必须引用合法 Evidence；unverified 原因可以无 Evidence。
- 岗位加权覆盖率低于 0.70 或任一 gating 维度未评估时，不发布岗位匹配分。
- gating 维度出现 L0 时保留 raw score，但 fit level 最高限制为“有条件匹配”。
- 第一版只实现 ai_application_engineering / 2026-H2 Role Pack。
- 测试使用 Fake LLM，不调用真实模型。
- 执行时遵循用户偏好：可使用 Codex Luna Max 工作树任务，不使用 collaboration 子 agent。

---

## File Map

### Create

- profile_agent/schemas/report_schema.py：Role Pack、Rubric、评分和报告契约。
- profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json：版本化岗位标准。
- profile_agent/services/role_profile_service.py：岗位包加载与校验。
- profile_agent/services/scoring_blueprint_service.py：Requirement 与岗位维度绑定。
- profile_agent/services/rubric_matcher_service.py：Evidence-to-Rubric 匹配与校验。
- profile_agent/services/score_engine_service.py：纯 Python 评分引擎。
- profile_agent/services/claim_verification_service.py：Claim 核验聚合。
- profile_agent/services/report_writer_service.py：报告文案生成、校验与降级。
- profile_agent/services/assessment_report_service.py：端到端编排。
- tests/test_report_schema.py
- tests/test_role_profile_service.py
- tests/test_scoring_blueprint_service.py
- tests/test_rubric_matcher_service.py
- tests/test_score_engine_service.py
- tests/test_claim_verification_service.py
- tests/test_report_writer_service.py
- tests/test_assessment_report_service.py
- tests/fixtures/report_golden_cases.py

### Modify

- README.md：增加报告阶段、Role Pack 版本和离线验证说明。

---

### Task 1: Freeze Report and Scoring Schemas

**Files:**
- Create: profile_agent/schemas/report_schema.py
- Test: tests/test_report_schema.py

**Interfaces:**
- Consumes: stable IDs from CompetencyModel, InterviewPlan, InterviewTurn, Evidence and ClaimRegistry.
- Produces: RoleCompetencyProfile, ScoringBlueprint, RubricMatchBatch, RequirementScore, RadarDimensionResult, JobMatchResult, ScoreSnapshot, ReportNarrativeDraft and AssessmentReport.

- [ ] **Step 1: Write failing schema tests**

Create tests for these exact invariants:

- `test_unverified_requirement_has_none_score`：构造 `level="UNVERIFIED", display_score=None`，断言模型可创建且分数为空。
- `test_unverified_requirement_rejects_zero_score`：将同一对象的 `display_score` 改为 `0`，断言 Pydantic 抛出 ValidationError。
- `test_verified_requirement_requires_score`：构造 `level="L2", display_score=None`，断言校验失败。
- `test_strength_risk_and_critical_reason_require_evidence`：分别构造三种原因且 Evidence ID 为空，逐项断言校验失败。
- `test_unverified_reason_can_have_no_evidence`：构造 `kind="unverified"` 且 Evidence ID 为空，断言可创建。
- `test_scored_radar_dimension_requires_two_reasons`：构造已评分维度且仅一条原因，断言校验失败；补足两条后通过。
- `test_role_dimension_weights_must_sum_to_one`：传入总权重 `0.90`，断言校验失败；传入 `1.00` 后通过。
- `test_role_dimension_ids_are_unique`：传入重复 `role_dim_01`，断言校验失败。
- `test_unpublished_job_match_has_no_score_or_fit_level`：构造 `published=False` 且带分数或等级，断言两种情况都失败。

Representative assertion:

~~~python
score = RequirementScore(
    requirement_id="req_01",
    dimension_id="role_dim_01",
    level="UNVERIFIED",
    display_score=None,
    coverage=0.0,
    confidence="low",
)
self.assertIsNone(score.display_score)
~~~

- [ ] **Step 2: Run RED**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_schema -v
~~~

Expected: FAIL because report_schema does not exist.

- [ ] **Step 3: Implement the schema module**

Define these literals exactly:

~~~python
ScoreLevel = Literal["UNVERIFIED", "L0", "L1", "L2", "L3", "L4"]
ConfidenceLevel = Literal["low", "medium", "high"]
QualityLevel = Literal["unverified", "weak", "medium", "strong"]
FitLevel = Literal[
    "高度匹配", "较高匹配", "有条件匹配", "当前匹配度较低", "存在明显岗位风险"
]
~~~

Define these models and fields:

~~~text
RubricCriterion
  id: str
  text: str
  score_adjustment: int [-5, 5], default 0

CompetencyDimensionRubric
  id, name, weight, is_gating
  minimum_criteria: non-empty list[RubricCriterion]
  excellence_signals, critical_errors, accepted_alternatives

RoleCompetencyProfile
  role_family, display_name, version
  valid_from: date
  knowledge_as_of: date
  dimensions: non-empty list[CompetencyDimensionRubric]
  source_refs: non-empty list[str]

RequirementBindingDraft
  requirement_id, primary_dimension_id, rubric_id

ScoringBlueprintDraft
  bindings: list[RequirementBindingDraft]

RequirementScoringBinding
  requirement_id, primary_dimension_id
  weight_within_dimension: float (0, 1]
  rubric_id

ScoringBlueprint
  role_family, role_profile_version
  bindings: list[RequirementScoringBinding]

RubricQuality
  correctness, specificity, reasoning
  tradeoff_awareness, transferability

RubricMatch
  evidence_id, requirement_id
  matched_minimum_criteria
  matched_excellence_signals
  matched_critical_errors
  accepted_alternative_ids
  quality: RubricQuality

RubricMatchBatch
  matches: list[RubricMatch]

ScoreReason
  reason_type: strength | risk | unverified | critical_error
  text, evidence_ids, rubric_signal_ids

RequirementScore
  requirement_id, dimension_id, level, display_score
  coverage, confidence
  supporting_evidence_ids, contradicting_evidence_ids
  matched_signal_ids, unresolved_critical_error_ids
  score_reasons

RadarDimensionResult
  dimension_id, name, score, level, coverage, confidence
  score_reasons, requirement_breakdown

JobMatchResult
  raw_score, published, fit_level
  coverage, confidence, limiting_reasons

ClaimVerification
  claim_id
  status: supported | partially_supported | insufficient | contradictory | unverified
  supporting_evidence_ids, contradicting_evidence_ids, explanation

ScoreSnapshot
  role_family, role_profile_version, scoring_engine_version
  radar_dimensions, job_match, claim_verifications

NarrativeItem
  text, dimension_ids, evidence_ids

DevelopmentAction
  dimension_id, current_gap, actions, acceptance_criteria

ReportNarrativeDraft
  executive_summary, strengths, risks, unverified_areas
  fit_contexts, development_actions

InterviewPathStep
  turn_id, question_mode, requirement_id, outcome, evidence_ids

AssessmentReport
  target_role, score_snapshot, narrative
  interview_path, assessment_limitations
~~~

Use Pydantic model validators for weight sum, unique IDs, score presence and evidence-required ScoreReason rules.

- [ ] **Step 4: Run focused and full tests**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_schema -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

Expected: all tests pass.

- [ ] **Step 5: Commit**

~~~powershell
git add profile_agent/schemas/report_schema.py tests/test_report_schema.py
git commit -m "feat: add assessment report schemas"
~~~

---

### Task 2: Add the 2026-H2 AI Application Role Pack

**Files:**
- Create: profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json
- Create: profile_agent/services/role_profile_service.py
- Test: tests/test_role_profile_service.py

**Interfaces:**
- Consumes: RoleCompetencyProfile.
- Produces: load_role_profile(role_family: str, version: str) -> RoleCompetencyProfile.

- [ ] **Step 1: Write failing content and loader tests**

- `test_loads_ai_application_2026_h2_profile`：用固定 role/version 加载，断言返回 `ai_application_engineering` 和 `2026-H2`。
- `test_profile_has_six_frozen_dimensions`：断言 ID 按 `role_dim_01` 至 `role_dim_06` 排列，中文名称与下方列表一致。
- `test_weights_equal_25_15_15_15_20_10_percent`：断言权重序列严格等于 `[0.25, 0.15, 0.15, 0.15, 0.20, 0.10]`。
- `test_each_dimension_has_two_minimum_two_excellence_one_error`：逐维度断言三类规则数量分别为 `2/2/1`。
- `test_gating_dimensions_are_01_02_05`：断言 gating ID 集合严格等于 `{role_dim_01, role_dim_02, role_dim_05}`。
- `test_unknown_role_or_version_is_rejected`：分别传入未知 role 和未知 version，断言抛出 ValueError 且错误包含输入值。

Expected dimension order:

~~~python
[
    "AI应用与Agent编排",
    "业务理解与任务建模",
    "Context、RAG与工具集成",
    "AI原生工程交付",
    "可靠性、评测与安全",
    "系统思维与持续进化",
]
~~~

- [ ] **Step 2: Run RED**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_role_profile_service -v
~~~

- [ ] **Step 3: Create the JSON Role Pack**

Use metadata:

~~~json
{
  "role_family": "ai_application_engineering",
  "display_name": "AI Agent / AI应用工程师",
  "version": "2026-H2",
  "valid_from": "2026-07-01",
  "knowledge_as_of": "2026-08-21",
  "source_refs": [
    "WEF-Future-of-Jobs-2025",
    "Microsoft-Work-Trend-Index-2025",
    "LinkedIn-Labor-Market-Report-2026",
    "curated-enterprise-jd-sample-2026-h2"
  ]
}
~~~

Use this exact rubric table:

| ID | Name | Weight | Gating | Minimum criteria | Excellence signals | Critical error |
|---|---|---:|---|---|---|---|
| role_dim_01 | AI应用与Agent编排 | .25 | yes | d01_min_01 拆分节点/状态/工具边界；d01_min_02 解释状态流转/路由/人工介入 | d01_exc_01 比较单Agent/Workflow/多Agent(+2)；d01_exc_02 新场景迁移优化(+3) | d01_err_01 无差别拆成多Agent(-5) |
| role_dim_02 | 业务理解与任务建模 | .15 | yes | d02_min_01 澄清目标/输入/输出/约束；d02_min_02 定义成功标准和失败边界 | d02_exc_01 识别不适合LLM的步骤(+2)；d02_exc_02 模糊需求转可交付规格(+3) | d02_err_01 无验收标准直接选模型(-5) |
| role_dim_03 | Context、RAG与工具集成 | .15 | no | d03_min_01 区分上下文/检索/状态/记忆；d03_min_02 校验工具参数/权限/结果 | d03_exc_01 设计检索评测与引用(+2)；d03_exc_02 处理预算/污染/过期知识(+3) | d03_err_01 未校验检索结果进入高风险工具(-5) |
| role_dim_04 | AI原生工程交付 | .15 | no | d04_min_01 清晰规格并审查AI结果；d04_min_02 用测试/日志/实验验证 | d04_exc_01 发现依赖/边界/安全问题(+2)；d04_exc_02 建立规格到验收流程(+3) | d04_err_01 未经理解测试直接交付(-5) |
| role_dim_05 | 可靠性、评测与安全 | .20 | yes | d05_min_01 超时/重试/降级/恢复；d05_min_02 模型输出和工具调用评测边界 | d05_exc_01 可重试分类/幂等/补偿(+2)；d05_exc_02 效果/成本/延迟/安全评测(+3) | d05_err_01 模型直接触发高风险操作(-5) |
| role_dim_06 | 系统思维与持续进化 | .10 | no | d06_min_01 比较成本/性能/复杂度/扩展性；d06_min_02 根据失败和新约束调整 | d06_exc_01 区分工程原则与热点(+2)；d06_exc_02 持续评测复盘演进(+3) | d06_err_01 只按热度选型且无取舍(-5) |

Each JSON dimension must include accepted_alternatives as an empty list in v1.

- [ ] **Step 4: Implement loader**

~~~python
_ROLE_PACKS = {
    ("ai_application_engineering", "2026-H2"):
        "ai_application_engineer_2026_h2.json",
}

def load_role_profile(role_family: str, version: str) -> RoleCompetencyProfile:
    filename = _ROLE_PACKS.get((role_family, version))
    if filename is None:
        raise ValueError(f"不存在的 Role Pack: {role_family}/{version}")
    path = Path(__file__).resolve().parents[1] / "knowledge" / "role_packs" / filename
    return RoleCompetencyProfile.model_validate_json(path.read_text("utf-8"))
~~~

- [ ] **Step 5: Run tests and commit**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_role_profile_service -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git add profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json profile_agent/services/role_profile_service.py tests/test_role_profile_service.py
git commit -m "feat: add versioned AI application role pack"
~~~

---

### Task 3: Build the Scoring Blueprint

**Files:**
- Create: profile_agent/services/scoring_blueprint_service.py
- Test: tests/test_scoring_blueprint_service.py

**Interfaces:**
- Consumes: InterviewPlan, RoleCompetencyProfile, injectable llm_client.
- Produces: build_scoring_blueprint(plan, role_profile, llm_client=llm) -> ScoringBlueprint.

- [ ] **Step 1: Write RED tests**

- `test_calls_structured_llm_once`：Fake LLM 记录调用次数，断言一次调用且响应模型为 `ScoringBlueprintDraft`。
- `test_binds_every_plan_requirement_exactly_once`：输入三个 Requirement，断言输出 ID 集合完全相等且计数均为一。
- `test_two_requirements_in_same_dimension_each_get_half_weight`：两个 Requirement 同属一个维度，断言各自归一化权重为 `0.5`。
- `test_missing_binding_is_rejected`：Fake LLM 漏掉一个 Requirement，断言服务抛出 BlueprintValidationError。
- `test_duplicate_binding_is_rejected`：Fake LLM 重复绑定同一 Requirement，断言服务拒绝。
- `test_unknown_requirement_or_dimension_is_rejected`：分别注入未知 Requirement ID 与未知维度 ID，断言服务拒绝并指出 ID。
- `test_input_objects_are_not_mutated`：调用前后比较 `model_dump()`，断言 Plan 与 Role Profile 完全一致。

- [ ] **Step 2: Run RED**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_scoring_blueprint_service -v
~~~

- [ ] **Step 3: Implement semantic binding and deterministic weights**

The LLM returns ScoringBlueprintDraft only. Prompt includes Target objective/type, every Requirement description, and Role Dimension ID/name/rubric text. It states: bind every Requirement once to its most relevant primary dimension; do not score.

Validation and normalization:

~~~python
plan_ids = {
    req.id for target in plan.targets for req in target.evidence_requirements
}
bound_ids = [x.requirement_id for x in draft.bindings]
if len(bound_ids) != len(set(bound_ids)):
    raise ValueError("Requirement binding 重复")
if set(bound_ids) != plan_ids:
    raise ValueError("Requirement binding 必须与 Plan 完全一致")
if any(x.primary_dimension_id not in dimension_ids for x in draft.bindings):
    raise ValueError("不存在的 Role Dimension")

counts = Counter(x.primary_dimension_id for x in draft.bindings)
bindings = [
    RequirementScoringBinding(
        **x.model_dump(),
        weight_within_dimension=1.0 / counts[x.primary_dimension_id],
    )
    for x in draft.bindings
]
~~~

For v1 require rubric_id == primary_dimension_id.

- [ ] **Step 4: Run tests and commit**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_scoring_blueprint_service -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git add profile_agent/services/scoring_blueprint_service.py tests/test_scoring_blueprint_service.py
git commit -m "feat: bind interview requirements to role dimensions"
~~~

---

### Task 4: Match Evidence to Rubric

**Files:**
- Create: profile_agent/services/rubric_matcher_service.py
- Test: tests/test_rubric_matcher_service.py

**Interfaces:**
- Consumes: InterviewPlan, ScoringBlueprint, RoleCompetencyProfile, list[InterviewTurn], list[Evidence], injectable LLM.
- Produces: `match_evidence_to_rubric(plan, blueprint, role_profile, turns, evidence, llm_client) -> RubricMatchBatch`.

- [ ] **Step 1: Write failing tests**

- `test_one_structured_call_returns_validated_matches`：Fake LLM 返回一条合法 match，断言只调用一次且输出引用原 Evidence/Requirement。
- `test_unknown_evidence_id_is_rejected`：返回 `ev_missing`，断言 RubricMatchValidationError。
- `test_unknown_requirement_id_is_rejected`：返回 `req_missing`，断言 RubricMatchValidationError。
- `test_evidence_can_only_match_its_requirement_ids`：Evidence 只支持 `req_01` 却返回 `req_02`，断言拒绝。
- `test_unknown_criterion_signal_error_or_alternative_is_rejected`：对四类引用分别注入未知 ID，断言每类都被拒绝。
- `test_duplicate_evidence_requirement_pair_is_rejected`：返回相同 `(evidence_id, requirement_id)` 两次，断言拒绝。
- `test_omission_cannot_be_returned_as_critical_error`：仅凭未提及某点返回 critical error，断言拒绝。
- `test_unmatched_evidence_is_allowed_and_does_not_score`：Evidence 不出现在 match 中，断言批次仍合法且后续不计分。
- `test_inputs_are_not_mutated`：比较调用前后所有输入 `model_dump()`，断言完全一致。

- [ ] **Step 2: Run RED**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_rubric_matcher_service -v
~~~

- [ ] **Step 3: Implement one structured call and fail-closed validation**

Prompt includes the full Role Pack rubric IDs/text, Blueprint, Plan descriptions, Turn questions/answers and Evidence facts. System constraints:

~~~text
Do not output scores or levels.
Use supplied IDs only.
A critical error requires explicit candidate evidence.
Missing content is unverified, not a critical error.
Use accepted alternative IDs for different but valid reasoning.
~~~

Before returning, validate all IDs, validate that each matched requirement belongs to Evidence.requirement_ids, and reject duplicate (evidence_id, requirement_id) pairs. Build allowed rubric IDs from the dimension bound to that Requirement.

- [ ] **Step 4: Run tests and commit**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_rubric_matcher_service -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git add profile_agent/services/rubric_matcher_service.py tests/test_rubric_matcher_service.py
git commit -m "feat: match interview evidence to role rubric"
~~~

---

### Task 5: Implement the Deterministic Score Engine

**Files:**
- Create: profile_agent/services/score_engine_service.py
- Create: tests/fixtures/report_golden_cases.py
- Test: tests/test_score_engine_service.py

**Interfaces:**
- Consumes: RoleCompetencyProfile, ScoringBlueprint, RubricMatchBatch, list[Evidence], list[InterviewTurn].
- Produces: `calculate_score_snapshot(role_profile, blueprint, match_batch, evidence, turns) -> ScoreSnapshot` with no LLM call.

- [ ] **Step 1: Create explicit golden fixtures**

Provide builders for exactly these cases:

~~~python
make_deep_but_non_exhaustive_case()   # minimum met + deep quality => L3
make_keyword_only_case()              # many terms, application minimum unmet => L1
make_unverified_case()                # no valid match => UNVERIFIED
make_critical_safety_error_case()     # explicit unsafe tool action => L0
make_conflicting_transfer_case()      # project support + migration contradiction
~~~

- [ ] **Step 2: Write failing tests**

- `test_unverified_has_none_score_and_does_not_enter_average`：混合一个 L2 与一个 UNVERIFIED Requirement，断言后者分数为空且维度均值只用 L2。
- `test_deep_non_exhaustive_answer_reaches_l3`：使用 deep fixture，断言 minimum 全满足、四项质量中至少三项 strong、最终 L3。
- `test_keyword_complete_but_non_applicable_stays_l1`：使用 keyword fixture，断言 application minimum 未满足且最终 L1。
- `test_l4_requires_independent_transfer_evidence`：先仅给原问题 Evidence，断言最高 L3；再加入不同题型的迁移成功 Evidence，断言 L4。
- `test_adjustment_is_capped_to_plus_or_minus_five`：分别提供累计 `+8` 与 `-9` 调整，断言实际调整为 `+5/-5`。
- `test_evidence_order_does_not_change_snapshot`：将同一 Evidence 列表反转，断言两次 `ScoreSnapshot.model_dump()` 相等。
- `test_duplicate_content_does_not_raise_confidence`：复制相同内容和题型的 Evidence，断言 confidence 不上升。
- `test_independent_question_modes_can_raise_confidence`：加入来自不同 question mode 的强 Evidence，断言由 medium 提升至 high（其他 high 条件均已满足）。
- `test_dimension_coverage_uses_binding_weights`：验证权重 `0.6/0.4` 中仅前者已评估，断言 coverage 为 `0.6`。
- `test_job_match_unpublished_below_seventy_percent`：构造覆盖率 `0.69`，断言 published=False 且 score/fit_level 为空。
- `test_job_match_unpublished_if_gating_dimension_unverified`：覆盖率超过阈值但一个 gating 未评估，断言不发布。
- `test_gating_l0_caps_fit_level_but_preserves_raw_score`：构造 raw score 90 且 gating 为 L0，断言 raw score 仍为 90、fit level 为“有条件匹配”。
- `test_every_scored_dimension_has_two_reasons`：遍历已评分维度，断言原因数至少二且正反/限制信息不被吞掉。

- [ ] **Step 3: Run RED**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_score_engine_service -v
~~~

- [ ] **Step 4: Implement exact level rules**

~~~python
_LEVEL_BASE = {"L0": 20, "L1": 40, "L2": 65, "L3": 82, "L4": 95}

def determine_level(matches, dimension, evidence_by_id, turn_by_id):
    if not matches:
        return "UNVERIFIED"
    if has_explicit_unresolved_critical_error(matches, evidence_by_id):
        return "L0"
    if not minimum_sufficiency_met(matches, dimension):
        return "L1"
    if meets_l3_quality(matches) and has_independent_transfer_success(
        matches, evidence_by_id, turn_by_id
    ):
        return "L4"
    if meets_l3_quality(matches):
        return "L3"
    return "L2"
~~~

L3 requires strong correctness plus at least two strong qualities among specificity, reasoning and tradeoff_awareness. L4 additionally requires supporting evidence from a different question mode with strong transferability. Omission never triggers L0.

- [ ] **Step 5: Implement score, coverage, confidence and job match**

Rules:

~~~text
Requirement score
= level base + unique signal adjustments, clipped to ±5 around base.

Dimension score
= Σ verified requirement score × binding weight
  / Σ verified binding weight.

Dimension coverage
= Σ verified binding weight / Σ all binding weight.

High confidence
= coverage >= .80
  + at least two independent question modes
  + no unresolved conflict
  + no core Requirement supported only by weak Evidence.

Low confidence
= coverage < .60 or only one weak evidence source.
Otherwise medium.

Job score publishes
= weighted coverage >= .70
  and every gating dimension verified.

Published raw score
= normalized role-weighted mean of verified dimension scores.

Gating L0
= preserve raw score, cap fit level at 有条件匹配, add limiting reason.
~~~

Fit bands: 85–100 高度匹配; 70–84 较高匹配; 55–69 有条件匹配; 40–54 当前匹配度较低; 0–39 存在明显岗位风险.

- [ ] **Step 6: Run tests and commit**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_score_engine_service -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git add profile_agent/services/score_engine_service.py tests/fixtures/report_golden_cases.py tests/test_score_engine_service.py
git commit -m "feat: add deterministic evidence score engine"
~~~

---

### Task 6: Aggregate Claim Verification

**Files:**
- Create: profile_agent/services/claim_verification_service.py
- Test: tests/test_claim_verification_service.py

**Interfaces:**
- Consumes: ClaimRegistry, list[Evidence].
- Produces: `aggregate_claim_verifications(claim_registry, evidence) -> list[ClaimVerification]`.

- [ ] **Step 1: Write failing tests**

- `test_strong_support_without_conflict_is_supported`：同一 Claim 有 strong 支持且无冲突，断言 `SUPPORTED`。
- `test_medium_support_is_partially_supported`：只有 medium 支持，断言 `PARTIALLY_SUPPORTED`。
- `test_any_explicit_contradiction_is_contradictory`：同时存在 strong 支持与显式矛盾，断言优先为 `CONTRADICTORY` 并保留两类 Evidence ID。
- `test_weak_support_only_is_insufficient`：仅 weak 支持，断言 `INSUFFICIENT`。
- `test_no_related_evidence_is_unverified`：无相关 Evidence，断言 `UNVERIFIED` 且引用列表为空。
- `test_unknown_claim_id_is_rejected`：Evidence 指向 registry 外 Claim，断言 ClaimVerificationError。
- `test_registry_order_is_preserved`：registry 顺序为 `claim_02, claim_01`，断言输出顺序一致。

- [ ] **Step 2: Run RED**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_claim_verification_service -v
~~~

- [ ] **Step 3: Implement deterministic precedence**

~~~text
no evidence                     -> unverified
any contradicting evidence      -> contradictory
any strong supporting evidence  -> supported
any medium supporting evidence  -> partially_supported
weak supporting evidence only   -> insufficient
~~~

Validate every Evidence.related_claim_id before aggregation. Use deterministic Chinese explanations with evidence counts.

- [ ] **Step 4: Run tests and commit**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_claim_verification_service -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git add profile_agent/services/claim_verification_service.py tests/test_claim_verification_service.py
git commit -m "feat: aggregate claim verification evidence"
~~~

---

### Task 7: Generate and Validate Grounded Report Narrative

**Files:**
- Create: profile_agent/services/report_writer_service.py
- Test: tests/test_report_writer_service.py

**Interfaces:**
- Consumes: ScoreSnapshot, list[Evidence], RoleCompetencyProfile, injectable LLM.
- Produces: `write_report_narrative(score_snapshot, evidence, role_profile, llm_client)` and `fallback_report_narrative(score_snapshot, evidence, role_profile)`.

- [ ] **Step 1: Write failing grounding tests**

- `test_writer_calls_structured_llm_once`：Fake LLM 返回合法 NarrativeDraft，断言一次结构化调用。
- `test_strength_requires_supporting_evidence`：strength 引用 risk/contradiction Evidence，断言 GroundingValidationError。
- `test_risk_requires_contradicting_evidence`：risk 只引用正向 Evidence，断言 GroundingValidationError。
- `test_unverified_area_maps_to_unverified_reason`：未评估维度输出观察不足，断言其 reason kind 为 unverified 且无负向措辞。
- `test_unknown_dimension_or_evidence_is_rejected`：分别注入未知维度和 Evidence ID，断言拒绝。
- `test_narrative_schema_has_no_score_fields`：检查 NarrativeDraft JSON schema，断言不存在 score/level/fit_level 字段。
- `test_hire_and_reject_language_is_rejected`：逐个注入四个禁用短语，断言内容校验失败。
- `test_development_action_requires_known_dimension`：发展建议引用 `role_dim_missing`，断言失败。
- `test_fallback_uses_score_reasons_without_llm`：让 writer 抛异常，调用 fallback，断言文本与引用均来自确定性 ScoreReason。

Prohibited phrases: 建议录用, 建议淘汰, 必须录用, 不予录用.

- [ ] **Step 2: Run RED**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_writer_service -v
~~~

- [ ] **Step 3: Implement writer, validator and fallback**

~~~python
def write_report_narrative(
    score_snapshot: ScoreSnapshot,
    evidences: list[Evidence],
    role_profile: RoleCompetencyProfile,
    llm_client=llm,
) -> ReportNarrativeDraft:
    draft = ReportNarrativeDraft.model_validate(
        llm_client.structured(messages, ReportNarrativeDraft)
    )
    validate_report_narrative(draft, score_snapshot, evidences, role_profile)
    return draft
~~~

Validator rules:

~~~text
strength evidence IDs must be supporting.
risk evidence IDs must be contradicting.
unverified items must map to unverified ScoreReason.
all dimension and evidence IDs must exist.
development actions must use known dimensions.
all narrative strings must reject prohibited hiring language.
~~~

Fallback builds deterministic items from ScoreReason and never calls LLM.

- [ ] **Step 4: Run tests and commit**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_report_writer_service -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git add profile_agent/services/report_writer_service.py tests/test_report_writer_service.py
git commit -m "feat: generate evidence-grounded report narrative"
~~~

---

### Task 8: Orchestrate the End-to-End Report Stage

**Files:**
- Create: profile_agent/services/assessment_report_service.py
- Test: tests/test_assessment_report_service.py
- Modify: README.md

**Interfaces:**
- Consumes: completed plan/runtime/turn/evidence/claim state and injectable semantic services.
- Produces: `generate_assessment_report(plan, runtime_state, turns, evidence, claim_registry, role_family, role_version, semantic_services) -> AssessmentReport`.

- [ ] **Step 1: Write failing offline integration tests**

- `test_complete_pipeline_produces_explainable_report`：以固定 Plan/Runtime/Fake semantic services 跑全链路，断言六维雷达、匹配结果、Claim、路径、优势风险和建议齐全。
- `test_unverified_dimension_is_unscored_and_gray_ready`：保留一个无 Evidence 维度，断言 level=UNVERIFIED、score=None，并有可供前端置灰的状态。
- `test_each_scored_radar_dimension_has_two_reasons`：遍历输出，断言每个非 UNVERIFIED 维度至少两个合法原因。
- `test_interview_path_uses_turn_order_and_evidence_links`：输入乱序 Evidence 和有序 Turn，断言路径按 Turn 顺序且每步引用属于该 Turn。
- `test_writer_failure_uses_fallback_without_losing_scores`：writer 抛 RuntimeError，断言报告仍生成且前后 `ScoreSnapshot` 完全一致。
- `test_unfinished_runtime_is_rejected`：传入 `stop_requested=False`，断言 AssessmentReportStateError。
- `test_same_structured_inputs_produce_identical_score_snapshot`：连续运行两次，断言 ScoreSnapshot 序列化结果完全相同。

Use Fake blueprint/matcher/writer functions. No real LLM.

- [ ] **Step 2: Run RED**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_assessment_report_service -v
~~~

- [ ] **Step 3: Implement thin orchestration**

~~~python
def generate_assessment_report(
    *,
    target_role: str,
    plan: InterviewPlan,
    runtime_state: InterviewRuntimeState,
    turns: list[InterviewTurn],
    evidences: list[Evidence],
    claim_registry: ClaimRegistry,
    role_family: str = "ai_application_engineering",
    role_profile_version: str = "2026-H2",
    blueprint_builder=build_scoring_blueprint,
    rubric_matcher=match_evidence_to_rubric,
    narrative_writer=write_report_narrative,
) -> AssessmentReport:
    if not runtime_state.stop_requested:
        raise ValueError("面试尚未结束，不能生成最终报告")
    profile = load_role_profile(role_family, role_profile_version)
    blueprint = blueprint_builder(plan, profile)
    matches = rubric_matcher(plan, blueprint, profile, turns, evidences)
    snapshot = calculate_score_snapshot(
        profile, blueprint, matches, evidences, turns
    )
    snapshot = snapshot.model_copy(update={
        "claim_verifications":
            aggregate_claim_verifications(claim_registry, evidences)
    })
    try:
        narrative = narrative_writer(snapshot, evidences, profile)
    except Exception:
        narrative = fallback_report_narrative(snapshot, evidences, profile)
    return AssessmentReport(
        target_role=target_role,
        score_snapshot=snapshot,
        narrative=narrative,
        interview_path=build_interview_path(turns, evidences),
        assessment_limitations=build_limitations(snapshot),
    )
~~~

Do not catch scoring, Role Pack, ID or validation failures. Only narrative generation may fall back.

- [ ] **Step 4: Update README**

Document:

~~~text
InterviewGraph ends with immutable Turn/Evidence history.
AssessmentReportService performs versioned rubric matching,
deterministic scoring, Claim aggregation and grounded report writing.
Radar visualization consumes AssessmentReport and never creates scores.
~~~

Add focused and full offline commands.

- [ ] **Step 5: Run final verification**

~~~powershell
$env:LANGGRAPH_STRICT_MSGPACK='true'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
$testCode = $LASTEXITCODE
Remove-Item Env:LANGGRAPH_STRICT_MSGPACK
.\.venv\Scripts\python.exe check_without_llm.py
$selfCheckCode = $LASTEXITCODE
.\.venv\Scripts\python.exe -m compileall -q profile_agent tests run_pre_interview.py run_interview_demo.py
$compileCode = $LASTEXITCODE
git diff --check
$diffCode = $LASTEXITCODE
if (($testCode -ne 0) -or ($selfCheckCode -ne 0) -or ($compileCode -ne 0) -or ($diffCode -ne 0)) { exit 1 }
~~~

Expected: all tests, offline check, compileall and diff check exit 0.

- [ ] **Step 6: Commit**

~~~powershell
git add profile_agent/services/assessment_report_service.py tests/test_assessment_report_service.py README.md
git commit -m "feat: orchestrate evidence-driven assessment reports"
~~~

---

## End-to-End Acceptance Scenario

~~~text
load AI Agent 2026-H2 Role Pack
→ bind every Plan Requirement exactly once
→ vague first answer produces weak Evidence
→ follow-up produces strong Evidence and an L3 result
→ separate migration scenario exposes a reliability limitation
→ ScoreEngine retains positive and limiting Evidence
→ unverified dimensions remain unscored
→ job score publishes only after gating/coverage thresholds
→ every scored radar dimension exposes at least two reasons
→ every strength/risk references Evidence
→ report contains no hire/reject decision
→ repeated structured input yields identical ScoreSnapshot
~~~

## Explicitly Deferred

- Radar chart visual design and Web report page; this plan produces stable chart data only.
- Runtime web search for role standards; v1 uses curated versioned JSON.
- AI algorithm and AI-native backend Role Packs.
- Online coding assessment, repository upload and production persistence.
- Multi-candidate ranking, employer dashboard and direct hiring decisions.
