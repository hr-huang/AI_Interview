"""Frozen inputs for the six report-boundary calibration cases.

The cases in this module are deliberately model-free.  They provide stable
candidate answers and Evidence objects to the semantic report pipeline; the
pipeline remains responsible for matching and scoring those inputs.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from profile_agent.calibration.schemas import (
    LevelRange,
    ReportCalibrationCase,
    ReportCalibrationExpectation,
)
from profile_agent.schemas.claim_schema import ClaimRegistry
from profile_agent.schemas.interview_schema import (
    AssessmentTarget,
    EvidenceRequirement,
    InterviewPlan,
)
from profile_agent.schemas.report_schema import RoleCompetencyProfile
from profile_agent.schemas.runtime_schema import (
    Evidence,
    InterviewRuntimeState,
    InterviewTurn,
    RequirementProgress,
)
from profile_agent.services.role_profile_service import load_role_profile


_ROLE_FAMILY = "ai_application_engineering"
_ROLE_PROFILE_VERSION = "2026-H2"
_BASE_TIME = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)

_REQUIREMENTS = (
    ("req_01", "验证状态、节点、工具边界与动态路径设计", "system_design"),
    ("req_02", "验证业务问题到可测任务的建模能力", "scenario"),
    ("req_03", "验证 Context、RAG、记忆与工具集成设计", "system_design"),
    ("req_04", "验证使用 AI 协作交付并以测试日志验收的能力", "project_deep_dive"),
    ("req_05", "验证失败恢复、评测、安全边界与人工接管", "scenario"),
    ("req_06", "验证成本、性能、复杂度与持续演进取舍", "follow_up"),
)

_DIMENSION_BY_REQUIREMENT = {
    "req_01": "role_dim_01",
    "req_02": "role_dim_02",
    "req_03": "role_dim_03",
    "req_04": "role_dim_04",
    "req_05": "role_dim_05",
    "req_06": "role_dim_06",
}

_TARGET_TYPE_BY_REQUIREMENT = {
    "req_01": "system_design",
    "req_02": "problem_solving",
    "req_03": "system_design",
    "req_04": "experience_verification",
    "req_05": "debugging",
    "req_06": "system_design",
}


def _plan(
    transfer_requirement_ids: set[str] | None = None,
) -> InterviewPlan:
    """Build the shared six-requirement report-boundary plan."""

    transfer_requirement_ids = transfer_requirement_ids or set()
    targets = []
    for index, (requirement_id, description, question_mode) in enumerate(
        _REQUIREMENTS,
        start=1,
    ):
        targets.append(
            AssessmentTarget(
                id=f"target_{index:02d}",
                objective=description,
                target_type=_TARGET_TYPE_BY_REQUIREMENT[requirement_id],
                competency_ids=[],
                evidence_requirements=[
                    EvidenceRequirement(
                        id=requirement_id,
                        description=description,
                        planned_role_dimension_id=(
                            _DIMENSION_BY_REQUIREMENT[requirement_id]
                        ),
                        requires_transfer_validation=(
                            requirement_id in transfer_requirement_ids
                        ),
                    )
                ],
                related_claim_ids=[],
                priority="high",
                must_cover=True,
                time_budget_minutes=5,
                preferred_modes=[question_mode],
            )
        )

    return InterviewPlan(
        duration_minutes=30,
        max_questions=10,
        closing_buffer_minutes=2,
        targets=targets,
    )


def _terminal_runtime(
    plan: InterviewPlan,
    turns: list[InterviewTurn] | tuple[InterviewTurn, ...] = (),
    evidences: list[Evidence] | tuple[Evidence, ...] = (),
) -> InterviewRuntimeState:
    """Build a terminal runtime snapshot without inventing extra evidence."""

    supporting_by_requirement: dict[str, list[str]] = {}
    contradicting_by_requirement: dict[str, list[str]] = {}
    for evidence in evidences:
        destination = (
            contradicting_by_requirement
            if evidence.polarity == "contradicting"
            else supporting_by_requirement
        )
        for requirement_id in evidence.requirement_ids:
            destination.setdefault(requirement_id, []).append(evidence.id)

    visited_target_ids: list[str] = []
    for turn in turns:
        if turn.target_id not in visited_target_ids:
            visited_target_ids.append(turn.target_id)

    requirement_progress: dict[str, RequirementProgress] = {}
    for target in plan.targets:
        for requirement in target.evidence_requirements:
            requirement_id = requirement.id
            supporting_ids = supporting_by_requirement.get(requirement_id, [])
            contradicting_ids = contradicting_by_requirement.get(requirement_id, [])
            if contradicting_ids:
                status = "contradictory"
            elif supporting_ids:
                status = "sufficient"
            else:
                status = "skipped"
            requirement_progress[requirement_id] = RequirementProgress(
                requirement_id=requirement_id,
                status=status,
                attempt_count=sum(
                    turn.primary_requirement_id == requirement_id for turn in turns
                ),
                supporting_evidence_ids=list(supporting_ids),
                contradicting_evidence_ids=list(contradicting_ids),
            )

    return InterviewRuntimeState(
        question_count=len(turns),
        started_at=_BASE_TIME,
        current_target_id=None,
        requirement_progress=requirement_progress,
        visited_target_ids=visited_target_ids,
        stop_requested=True,
        stop_reason="冻结报告边界案例已完成，运行状态已终止",
    )


def _turn(
    case_id: str,
    sequence_number: int,
    *,
    requirement_id: str,
    question_mode: str,
    question: str,
    answer: str,
) -> InterviewTurn:
    target_number = int(requirement_id.rsplit("_", 1)[1])
    asked_at = _BASE_TIME + timedelta(minutes=sequence_number - 1)
    return InterviewTurn(
        id=f"turn_{case_id}_{sequence_number:03d}",
        sequence_number=sequence_number,
        target_id=f"target_{target_number:02d}",
        primary_requirement_id=requirement_id,
        question_mode=question_mode,
        question=question,
        answer=answer,
        asked_at=asked_at,
        answered_at=asked_at + timedelta(seconds=30),
    )


def _evidence(
    case_id: str,
    sequence_number: int,
    turn: InterviewTurn,
    *,
    observation: str,
    polarity: str = "supporting",
    strength: str = "strong",
) -> Evidence:
    answer = turn.answer or ""
    if len(answer) < 2:
        raise ValueError(
            "冻结演示 Evidence.source_excerpt 需要至少两个字符的回答"
        )
    # The browser transcript remains the only place for a complete answer.
    # Freeze a short, exact answer substring for the evidence drawer so the
    # demo exercises the same public evidence contract as a real assessment.
    parts = re.split(r"(?<=[。！？；;.!?])", answer)
    quote = next(
        (
            part
            for part in parts
            if part and part != answer and len(part) < len(answer)
        ),
        "",
    )
    if not quote:
        quote = answer[: min(len(answer) - 1, 32)]
    if not quote or quote == answer or quote not in answer:
        raise ValueError(
            "冻结演示 Evidence.source_excerpt 必须是回答中的严格短子串"
        )
    return Evidence(
        id=f"ev_{case_id}_{sequence_number:03d}",
        turn_id=turn.id,
        requirement_ids=[turn.primary_requirement_id],
        related_claim_ids=[],
        polarity=polarity,
        strength=strength,
        observation=observation,
        source_excerpt=quote,
    )


def _case(
    profile: RoleCompetencyProfile,
    *,
    case_id: str,
    title: str,
    description: str,
    turns: list[InterviewTurn],
    evidences: list[Evidence],
    expectation: ReportCalibrationExpectation,
    transfer_requirement_ids: set[str] | None = None,
) -> ReportCalibrationCase:
    turn_by_id = {turn.id: turn for turn in turns}
    for evidence in evidences:
        turn = turn_by_id.get(evidence.turn_id)
        if (
            turn is None
            or not evidence.source_excerpt
            or evidence.source_excerpt not in (turn.answer or "")
            or evidence.source_excerpt == (turn.answer or "")
        ):
            raise ValueError(
                f"{case_id} 的 Evidence.source_excerpt 未引用对应固定回答: "
                f"{evidence.id}"
            )

    plan = _plan(transfer_requirement_ids)
    return ReportCalibrationCase(
        id=case_id,
        title=title,
        description=description,
        target_role=profile.display_name,
        plan=plan,
        runtime_state=_terminal_runtime(plan, turns, evidences),
        turns=turns,
        evidences=evidences,
        claim_registry=ClaimRegistry(),
        expectation=expectation,
    )


def _criterion_ids(
    profile: RoleCompetencyProfile,
    dimension_id: str,
    collection_name: str,
) -> list[str]:
    dimension = next(
        (item for item in profile.dimensions if item.id == dimension_id),
        None,
    )
    if dimension is None:
        raise ValueError(f"Role Profile 缺少维度: {dimension_id}")
    return [item.id for item in getattr(dimension, collection_name)]


def _rubric_hits(
    profile: RoleCompetencyProfile,
    requirement_ids: list[str] | tuple[str, ...],
    collection_name: str,
) -> dict[str, list[str]]:
    return {
        requirement_id: _criterion_ids(
            profile,
            _DIMENSION_BY_REQUIREMENT[requirement_id],
            collection_name,
        )
        for requirement_id in requirement_ids
    }


def _level_range(min_level: str, max_level: str) -> LevelRange:
    return LevelRange(min_level=min_level, max_level=max_level)


def _build_c01(profile: RoleCompetencyProfile) -> ReportCalibrationCase:
    turns = [
        _turn(
            "C01",
            1,
            requirement_id="req_01",
            question_mode="system_design",
            question="请说明 Agent 流程中的状态、节点、工具和动态路径边界。",
            answer=(
                "我把流程拆成显式状态、节点和工具边界：状态只保存订单上下文与幂等键，"
                "节点分别负责检索、决策和执行，工具声明参数与权限；路由由状态机控制，"
                "异常进入重试或人工接管。"
            ),
        ),
        _turn(
            "C01",
            2,
            requirement_id="req_02",
            question_mode="scenario",
            question="请把一个模糊业务目标转成可测任务。",
            answer=(
                "我先把业务目标拆成输入、输出、约束和可测成功标准；LLM 只处理需要语义判断的步骤，"
                "规则校验用确定性代码，失败边界用验收集、拒答率和人工升级率定义。"
            ),
        ),
        _turn(
            "C01",
            3,
            requirement_id="req_03",
            question_mode="system_design",
            question="请说明 Context、RAG、记忆和工具如何协同。",
            answer=(
                "Context 只放本轮任务，RAG 负责带引用检索，长期记忆和运行状态分开；"
                "我会评测召回率、引用正确率、预算、污染和过期知识，工具调用前校验参数、权限和结果。"
            ),
        ),
        _turn(
            "C01",
            4,
            requirement_id="req_04",
            question_mode="project_deep_dive",
            question="请说明如何使用 AI 协作交付并验收。",
            answer=(
                "我把需求写成可审查规格，AI 生成的代码必须经过人工 review；"
                "用单元测试、集成测试、回放日志和离线实验验收，并保留规格到测试日志的映射。"
            ),
        ),
        _turn(
            "C01",
            5,
            requirement_id="req_05",
            question_mode="scenario",
            question="请说明失败恢复、评测和安全边界。",
            answer=(
                "我按超时、可重试和不可重试分类，写操作带幂等键，不能重试的错误走补偿或降级；"
                "模型输出和工具调用用离线评测集验证，高风险动作必须经过人工确认。"
            ),
        ),
        _turn(
            "C01",
            6,
            requirement_id="req_06",
            question_mode="follow_up",
            question="请说明成本、性能、复杂度和持续演进的取舍。",
            answer=(
                "我比较成本、延迟、复杂度和扩展性，先用单流程和小模型做基线，"
                "只有收益明确才引入多 Agent；持续按失败样本和新约束复盘迭代，"
                "不会只按热点选型。"
            ),
        ),
    ]
    evidences = [
        _evidence(
            "C01",
            index,
            turn,
            observation=f"冻结观察：回答具体说明了 {requirement_id} 的可验证工程边界。",
        )
        for index, (turn, (requirement_id, _, _)) in enumerate(
            zip(turns, _REQUIREMENTS),
            start=1,
        )
    ]
    requirement_ids = [item[0] for item in _REQUIREMENTS]
    return _case(
        profile,
        case_id="C01",
        title="全维度具体且可验收",
        description=(
            "候选人明确说明状态、节点、工具边界、任务建模、RAG 评测、测试日志、"
            "恢复安全和成本延迟取舍。"
        ),
        turns=turns,
        evidences=evidences,
        expectation=ReportCalibrationExpectation(
            required_rubric_hits=_rubric_hits(
                profile,
                requirement_ids,
                "minimum_criteria",
            ),
            requirement_level_ranges={
                requirement_id: _level_range("L3", "L3")
                for requirement_id in requirement_ids
            },
            job_match_published=True,
        ),
    )


def _build_c02(profile: RoleCompetencyProfile) -> ReportCalibrationCase:
    answers = {
        "req_01": "Agent、Workflow。",
        "req_02": "Agent、benchmark。",
        "req_03": "RAG、Memory。",
        "req_04": "Workflow、benchmark。",
        "req_05": "Agent、RAG、Workflow、Memory、benchmark。",
    }
    turns = [
        _turn(
            "C02",
            index,
            requirement_id=requirement_id,
            question_mode=question_mode,
            question="请给出具体实现、验证方式和取舍。",
            answer=answers[requirement_id],
        )
        for index, (requirement_id, _, question_mode) in enumerate(
            _REQUIREMENTS[:5],
            start=1,
        )
    ]
    evidences = [
        _evidence(
            "C02",
            index,
            turn,
            strength="weak",
            observation="冻结观察：回答只有术语关键词，没有具体实现或验证事实。",
        )
        for index, turn in enumerate(turns, start=1)
    ]
    requirement_ids = [item[0] for item in _REQUIREMENTS[:5]]
    return _case(
        profile,
        case_id="C02",
        title="关键词堆叠但缺少事实",
        description="回答只提到 Agent、RAG、Workflow、Memory 和 benchmark，没有具体实现或验证。",
        turns=turns,
        evidences=evidences,
        expectation=ReportCalibrationExpectation(
            forbidden_rubric_hits=_rubric_hits(
                profile,
                requirement_ids,
                "excellence_signals",
            ),
            expected_unverified_requirements=requirement_ids,
            job_match_published=False,
        ),
    )


def _build_c03(profile: RoleCompetencyProfile) -> ReportCalibrationCase:
    turns = [
        _turn(
            "C03",
            1,
            requirement_id="req_01",
            question_mode="project_deep_dive",
            question="请说明你在已有项目中如何设计 Agent Workflow。",
            answer=(
                "在已有订单流程中，我把状态、节点和工具边界拆开，路由由状态机控制，"
                "失败通过重试和人工介入恢复；我比较过单 Agent 和 Workflow，最终选 Workflow。"
            ),
        ),
        _turn(
            "C03",
            2,
            requirement_id="req_01",
            question_mode="scenario",
            question="迁移到新的受监管场景时你会如何调整？",
            answer=(
                "迁移到受监管的理赔场景时，我会原样复制现有流程，不新增合规边界、"
                "权限校验或重新评测。"
            ),
        ),
    ]
    evidences = [
        _evidence(
            "C03",
            1,
            turns[0],
            observation="冻结观察：已有项目中的 Workflow 解释具体且覆盖状态边界。",
        ),
        _evidence(
            "C03",
            2,
            turns[1],
            polarity="contradicting",
            strength="medium",
            observation="冻结观察：迁移到受监管新场景时主张原样复制且不重新验证。",
        ),
    ]
    return _case(
        profile,
        case_id="C03",
        title="已有深度但迁移不可直接复用",
        description="已有 Workflow 解释很强，但迁移到受监管新场景时主张不加验证地复制。",
        turns=turns,
        evidences=evidences,
        transfer_requirement_ids={"req_01"},
        expectation=ReportCalibrationExpectation(
            required_rubric_hits=_rubric_hits(
                profile,
                ["req_01"],
                "minimum_criteria",
            ),
            forbidden_rubric_hits={
                "req_01": _criterion_ids(
                    profile,
                    "role_dim_01",
                    "excellence_signals",
                )[-1:],
            },
            requirement_level_ranges={
                "req_01": _level_range("L2", "L3"),
            },
            required_question_modes={
                "req_01": ["project_deep_dive", "scenario"],
            },
            required_limiting_evidence_ids={
                "req_01": ["ev_C03_002"],
            },
        ),
    )


def _build_c04(profile: RoleCompetencyProfile) -> ReportCalibrationCase:
    turns = [
        _turn(
            "C04",
            1,
            requirement_id="req_01",
            question_mode="system_design",
            question="请说明状态所有权和并行节点的 join 语义。",
            answer=(
                "共享状态的 owner 放在编排器，节点只读写自己声明的字段；join 以 correlation id 聚合，"
                "超时、重复和部分失败都有明确状态，人工接管可以从检查点恢复。相比让所有节点任意写"
                "共享状态，这会多一些字段映射代码，但能避免并发覆盖，也更容易重放和定位失败。"
            ),
        ),
        _turn(
            "C04",
            2,
            requirement_id="req_05",
            question_mode="scenario",
            question="请说明失败恢复、评测和人工接管。",
            answer=(
                "失败按超时、可重试和不可重试分类，保存 checkpoint 后恢复；写入使用幂等键，"
                "风险动作进入人工确认，并用回放集分别评测模型输出和工具调用。只重试可恢复错误是"
                "为了避免放大业务错误；高风险动作宁可增加一些延迟，也不让模型绕过人工授权。"
            ),
        ),
    ]
    evidences = [
        _evidence(
            "C04",
            1,
            turns[0],
            observation="冻结观察：回答覆盖状态 owner、join 语义、失败路径和人工接管。",
        ),
        _evidence(
            "C04",
            2,
            turns[1],
            observation="冻结观察：回答覆盖恢复分类、幂等、评测和人工确认。",
        ),
    ]
    return _case(
        profile,
        case_id="C04",
        title="核心可靠性充分但不穷举编排变体",
        description="回答覆盖状态所有权、join、失败恢复和人工接管，但不要求列举可选编排方案。",
        turns=turns,
        evidences=evidences,
        expectation=ReportCalibrationExpectation(
            required_rubric_hits=_rubric_hits(
                profile,
                ["req_01", "req_05"],
                "minimum_criteria",
            ),
            requirement_level_ranges={
                "req_01": _level_range("L3", "L3"),
                "req_05": _level_range("L3", "L3"),
            },
        ),
    )


def _build_c05(profile: RoleCompetencyProfile) -> ReportCalibrationCase:
    turn = _turn(
        "C05",
        1,
        requirement_id="req_05",
        question_mode="scenario",
        question="高风险外部写入是否可以完全自动化？",
        answer=(
            "为追求自动化，我允许模型在没有授权、审批或人工确认时直接调用高风险外部写入工具；"
            "失败也不需要二次校验。"
        ),
    )
    evidence = _evidence(
        "C05",
        1,
        turn,
        polarity="contradicting",
        strength="strong",
        observation="冻结观察：候选人明确允许未经授权和人工确认的高风险外部写入。",
    )
    return _case(
        profile,
        case_id="C05",
        title="高风险操作缺少授权边界",
        description="候选人明确允许模型未经授权或人工确认直接执行高风险外部写入。",
        turns=[turn],
        evidences=[evidence],
        expectation=ReportCalibrationExpectation(
            required_rubric_hits=_rubric_hits(
                profile,
                ["req_05"],
                "critical_errors",
            ),
            requirement_level_ranges={
                "req_05": _level_range("L0", "L1"),
            },
        ),
    )


def _build_c06(profile: RoleCompetencyProfile) -> ReportCalibrationCase:
    turns = [
        _turn(
            "C06",
            1,
            requirement_id="req_01",
            question_mode="system_design",
            question="请说明你的 Agent 状态和工具边界。",
            answer=(
                "我会用显式状态机区分上下文、节点和工具，工具参数与权限先校验，"
                "失败路径保留重试和人工接管。"
            ),
        ),
        _turn(
            "C06",
            2,
            requirement_id="req_02",
            question_mode="scenario",
            question="请把业务问题转成可测任务。",
            answer=(
                "我先定义输入、输出、约束和成功标准，把需要语义判断的部分交给模型，"
                "把规则校验和失败边界写成确定性验收条件。"
            ),
        ),
    ]
    evidences = [
        _evidence(
            "C06",
            1,
            turns[0],
            observation="冻结观察：只有 req_01 获得了状态、节点和工具边界证据。",
        ),
        _evidence(
            "C06",
            2,
            turns[1],
            observation="冻结观察：只有 req_02 获得了可测任务建模证据。",
        ),
    ]
    return _case(
        profile,
        case_id="C06",
        title="仅部分维度有证据",
        description="候选人只回答 req_01 和 req_02，其余维度没有 Evidence。",
        turns=turns,
        evidences=evidences,
        expectation=ReportCalibrationExpectation(
            required_rubric_hits=_rubric_hits(
                profile,
                ["req_01", "req_02"],
                "minimum_criteria",
            ),
            requirement_level_ranges={
                "req_01": _level_range("L2", "L3"),
                "req_02": _level_range("L2", "L3"),
            },
            expected_unverified_requirements=[
                "req_03",
                "req_04",
                "req_05",
                "req_06",
            ],
            expected_unverified_dimensions=[
                "role_dim_03",
                "role_dim_04",
                "role_dim_05",
                "role_dim_06",
            ],
            job_match_published=False,
        ),
    )


def build_public_student_showcase_case() -> ReportCalibrationCase:
    """Build the student-scoped public demo without altering calibration cases."""

    profile = load_role_profile(_ROLE_FAMILY, _ROLE_PROFILE_VERSION)
    turns = [
        _turn(
            "DEMO_STUDENT",
            1,
            requirement_id="req_01",
            question_mode="system_design",
            question=(
                "你在校招助手项目中为什么选择显式 Workflow，而不是让一个 Agent "
                "自行完成全部步骤？请结合状态和工具边界说明。"
            ),
            answer=(
                "课程团队最初使用单 Agent，但调试时无法确认失败发生在哪一步。我把流程改为简历解析、"
                "岗位检索、证据匹配和人工确认四个节点，状态只保存候选人 ID、来源版本、当前步骤和"
                "工具结果引用；涉及写入或低置信度结论时转人工。职责独立但共享状态，所以没有继续拆成"
                "多 Agent。改造后可以逐节点回放，也减少了重复调用。"
            ),
        ),
        _turn(
            "DEMO_STUDENT",
            2,
            requirement_id="req_02",
            question_mode="scenario",
            question=(
                "如果业务只说“让岗位推荐更准确”，你会如何把它变成可验收任务？"
            ),
            answer=(
                "我会先确认候选人、岗位范围和不能使用的敏感字段，再把输出定义为带理由的岗位列表。"
                "离线使用 Top-K 命中率和理由引用正确率，在线观察点击率与人工驳回率；规则过滤用"
                "确定性代码，模型只负责需要语义判断的部分。"
            ),
        ),
        _turn(
            "DEMO_STUDENT",
            3,
            requirement_id="req_03",
            question_mode="project_deep_dive",
            question=(
                "项目中的 Context、RAG 和工具调用是怎样连接的？你如何避免旧岗位信息影响回答？"
            ),
            answer=(
                "我把当前任务和实时状态放进 Context，岗位材料按发布日期过滤后检索并保留引用；"
                "工具调用前检查参数格式和权限。项目做过过期文档测试，但还没有完整比较不同记忆策略"
                "或建立独立的工具调用评测集。"
            ),
        ),
        _turn(
            "DEMO_STUDENT",
            4,
            requirement_id="req_04",
            question_mode="project_deep_dive",
            question=(
                "你使用 AI 编程工具完成了哪些工作？如何证明交付物不是“能运行就算完成”？"
            ),
            answer=(
                "我先写输入输出契约和验收样例，再让 AI 生成解析与接口代码；我逐段审查依赖和异常路径，"
                "补充单元测试、接口测试和固定样本回放。一次 AI 把空文件当成功结果，我用失败用例发现后"
                "修正，并把该样本加入回归集。"
            ),
        ),
        _turn(
            "DEMO_STUDENT",
            5,
            requirement_id="req_05",
            question_mode="scenario",
            question=(
                "如果岗位推荐工具超时后返回了不确定结果，你会怎样恢复，并避免系统作出高风险决定？"
            ),
            answer=(
                "读取请求可以按错误类型限次重试，写操作使用幂等键；仍失败时降级为只展示已有证据。"
                "模型不能直接淘汰候选人，高风险结论必须人工确认，日志保留请求、模型版本、工具结果和"
                "审批记录。我会用超时注入、重复写入率和人工接管成功率验收。"
            ),
        ),
    ]
    evidences = [
        _evidence(
            "DEMO_STUDENT",
            index,
            turn,
            observation=(
                "公开演示观察：该回答来自应届候选人的课程、竞赛或实习项目，"
                "并提供了可回溯的工程事实。"
            ),
        )
        for index, turn in enumerate(turns, start=1)
    ]
    req_01_hits = [
        *_criterion_ids(profile, "role_dim_01", "minimum_criteria"),
        *_criterion_ids(profile, "role_dim_01", "excellence_signals"),
    ]
    req_03_minimum = _criterion_ids(
        profile,
        "role_dim_03",
        "minimum_criteria",
    )
    return _case(
        profile,
        case_id="DEMO_STUDENT",
        title="应届候选人完整演示",
        description=(
            "一名应届候选人使用课程、竞赛与实习项目回答五轮动态问题；"
            "系统区分已证明能力、部分证据和待复试验证项。"
        ),
        turns=turns,
        evidences=evidences,
        expectation=ReportCalibrationExpectation(
            required_rubric_hits={
                "req_01": req_01_hits,
                "req_02": _criterion_ids(
                    profile,
                    "role_dim_02",
                    "minimum_criteria",
                ),
                "req_03": req_03_minimum[:1],
                "req_04": _criterion_ids(
                    profile,
                    "role_dim_04",
                    "minimum_criteria",
                ),
                "req_05": _criterion_ids(
                    profile,
                    "role_dim_05",
                    "minimum_criteria",
                ),
            },
            requirement_level_ranges={
                "req_01": _level_range("L3", "L3"),
                "req_02": _level_range("L3", "L3"),
                "req_03": _level_range("L1", "L1"),
                "req_04": _level_range("L3", "L3"),
                "req_05": _level_range("L3", "L3"),
            },
            expected_unverified_requirements=["req_06"],
            expected_unverified_dimensions=["role_dim_06"],
            job_match_published=True,
        ),
    )


def load_report_calibration_cases() -> tuple[ReportCalibrationCase, ...]:
    """Return the six deterministic report-boundary inputs in canonical order."""

    profile = load_role_profile(_ROLE_FAMILY, _ROLE_PROFILE_VERSION)
    return (
        _build_c01(profile),
        _build_c02(profile),
        _build_c03(profile),
        _build_c04(profile),
        _build_c05(profile),
        _build_c06(profile),
    )


def get_report_calibration_case(case_id: str) -> ReportCalibrationCase:
    """Return one frozen case by ID, raising ``KeyError`` for unknown IDs."""

    for case in load_report_calibration_cases():
        if case.id == case_id:
            return case
    raise KeyError(f"未知报告校准案例: {case_id}")


__all__ = [
    "build_public_student_showcase_case",
    "get_report_calibration_case",
    "load_report_calibration_cases",
]
