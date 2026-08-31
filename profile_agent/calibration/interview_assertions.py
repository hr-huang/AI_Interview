"""Pure hard-boundary assertions for dynamic interview calibration."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from profile_agent.calibration.report_assertions import evaluate_report_invariants
from profile_agent.calibration.schemas import (
    CalibrationAssertion,
    InterviewCalibrationCase,
)
from profile_agent.schemas.interview_schema import InterviewPlan
from profile_agent.schemas.report_schema import AssessmentReport
from profile_agent.schemas.runtime_schema import (
    Evidence,
    InterviewRuntimeState,
    InterviewTurn,
)


_LEVEL_ORDER = {
    level: index for index, level in enumerate(("L0", "L1", "L2", "L3", "L4"))
}


def _assertion(code: str, passed: bool, message: str) -> CalibrationAssertion:
    return CalibrationAssertion(code=code, passed=passed, message=message)


def _requirement_text_by_id(plan: InterviewPlan) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for target in plan.targets:
        for requirement in target.evidence_requirements:
            descriptions[requirement.id] = requirement.description
    return descriptions


def _topic_turn_count(
    topic_terms: list[str],
    turns: list[InterviewTurn],
    requirement_text: Mapping[str, str],
) -> int:
    folded_terms = [term.casefold() for term in topic_terms]
    count = 0
    for turn in turns:
        haystack = (
            f"{turn.question}\n{requirement_text.get(turn.primary_requirement_id, '')}"
        ).casefold()
        if any(term in haystack for term in folded_terms):
            count += 1
    return count


def _evidence_provenance_valid(
    plan: InterviewPlan,
    turns: list[InterviewTurn],
    evidences: list[Evidence],
) -> tuple[bool, str]:
    turns_by_id = {turn.id: turn for turn in turns}
    if len(turns_by_id) != len(turns):
        return False, "InterviewTurn ID 存在重复。"

    plan_target_ids = {target.id for target in plan.targets}
    plan_requirement_ids = {
        item.id
        for target in plan.targets
        for item in target.evidence_requirements
    }
    evidence_ids = [evidence.id for evidence in evidences]
    if len(evidence_ids) != len(set(evidence_ids)):
        return False, "Evidence ID 存在重复。"

    for evidence in evidences:
        turn = turns_by_id.get(evidence.turn_id)
        if turn is None:
            return False, f"{evidence.id} 引用了未知 Turn。"
        if turn.target_id not in plan_target_ids:
            return False, f"{evidence.id} 对应 Turn 引用了计划外 Target。"
        if not set(evidence.requirement_ids).issubset(plan_requirement_ids):
            return False, f"{evidence.id} 引用了计划外 Requirement。"
        answer = turn.answer or ""
        if not evidence.source_excerpt or evidence.source_excerpt not in answer:
            return False, f"{evidence.id} 的 source_excerpt 无法回溯到回答。"
    return True, "所有 Evidence 都能回溯到对应问题、回答和 Requirement。"


def _scripted_usage_valid(
    case: InterviewCalibrationCase,
    selected_rule_ids: list[str],
    turn_count: int,
) -> tuple[bool, str]:
    limits = {rule.id: rule.max_uses for rule in case.answer_rules}
    counts = Counter(selected_rule_ids)
    unknown = sorted(set(counts) - set(limits))
    exhausted = sorted(
        rule_id
        for rule_id, count in counts.items()
        if rule_id in limits and count > limits[rule_id]
    )
    count_matches = len(selected_rule_ids) == turn_count
    passed = not unknown and not exhausted and count_matches
    if passed:
        return True, "每个已回答 Turn 都对应一个未超限的脚本规则。"
    details = []
    if unknown:
        details.append("unknown=" + ",".join(unknown))
    if exhausted:
        details.append("exhausted=" + ",".join(exhausted))
    if not count_matches:
        details.append(
            f"selected={len(selected_rule_ids)}, turns={turn_count}"
        )
    return False, "脚本规则使用边界失败: " + "; ".join(details)


def evaluate_interview_path(
    case: InterviewCalibrationCase,
    final_state: Mapping[str, object],
    selected_rule_ids: list[str],
) -> list[CalibrationAssertion]:
    """Evaluate path, provenance, and final report without model calls."""

    plan = InterviewPlan.model_validate(final_state.get("interview_plan"))
    runtime = InterviewRuntimeState.model_validate(final_state.get("runtime_state"))
    turns = [
        InterviewTurn.model_validate(item)
        for item in final_state.get("interview_turns", [])
    ]
    evidences = [
        Evidence.model_validate(item) for item in final_state.get("evidences", [])
    ]
    report_value = final_state.get("assessment_report")
    report = AssessmentReport.model_validate(report_value)

    assertions = [
        _assertion(
            "terminal_state",
            runtime.stop_requested and report_value is not None,
            "面试已终止并生成 AssessmentReport。"
            if runtime.stop_requested and report_value is not None
            else "面试未终止或缺少 AssessmentReport。",
        )
    ]

    within_question_limit = (
        len(turns) <= case.path_expectation.max_questions
        and runtime.question_count <= case.path_expectation.max_questions
    )
    assertions.append(
        _assertion(
            "question_limit",
            within_question_limit,
            f"问题数 {len(turns)} 未超过上限 {case.path_expectation.max_questions}。"
            if within_question_limit
            else f"问题数或 Runtime 计数超过上限 {case.path_expectation.max_questions}。",
        )
    )

    unanswered = [
        turn.id
        for turn in turns
        if not (turn.answer or "").strip() or turn.answered_at is None
    ]
    assertions.append(
        _assertion(
            "answered_turns",
            not unanswered,
            "所有问题都有非空回答和 answered_at。"
            if not unanswered
            else "存在未回答 Turn: " + ", ".join(unanswered),
        )
    )

    requirement_text = _requirement_text_by_id(plan)
    for topic, terms in case.path_expectation.required_topics.items():
        covered = _topic_turn_count(terms, turns, requirement_text) > 0
        assertions.append(
            _assertion(
                f"required_topic:{topic}",
                covered,
                f"路径已覆盖主题 {topic}。"
                if covered
                else f"路径未覆盖主题 {topic}: {', '.join(terms)}。",
            )
        )

    for topic in case.path_expectation.forbidden_repeated_topics:
        count = _topic_turn_count([topic], turns, requirement_text)
        passed = count <= 1
        assertions.append(
            _assertion(
                f"repeated_topic:{topic}",
                passed,
                f"主题 {topic} 出现 {count} 次，未重复追问。"
                if passed
                else f"主题 {topic} 出现 {count} 次，存在重复追问。",
            )
        )

    provenance_passed, provenance_message = _evidence_provenance_valid(
        plan,
        turns,
        evidences,
    )
    assertions.append(
        _assertion(
            "evidence_provenance",
            provenance_passed,
            provenance_message,
        )
    )

    usage_passed, usage_message = _scripted_usage_valid(
        case,
        selected_rule_ids,
        len(turns),
    )
    assertions.append(
        _assertion("scripted_rule_usage", usage_passed, usage_message)
    )

    radar_by_id = {
        radar.dimension_id: radar for radar in report.score_snapshot.radar_dimensions
    }
    for dimension_id, level_range in case.path_expectation.radar_level_ranges.items():
        radar = radar_by_id.get(dimension_id)
        actual_level = radar.level if radar is not None else None
        passed = (
            actual_level in _LEVEL_ORDER
            and _LEVEL_ORDER[level_range.min_level]
            <= _LEVEL_ORDER[actual_level]
            <= _LEVEL_ORDER[level_range.max_level]
        )
        assertions.append(
            _assertion(
                f"radar_level:{dimension_id}",
                passed,
                f"{dimension_id} 等级 {actual_level} 符合范围。"
                if passed
                else f"{dimension_id} 等级 {actual_level or 'missing'} 不符合范围。",
            )
        )

    for dimension_id in case.path_expectation.expected_unverified_dimensions:
        radar = radar_by_id.get(dimension_id)
        passed = radar is not None and radar.level == "UNVERIFIED"
        assertions.append(
            _assertion(
                f"unverified_dimension:{dimension_id}",
                passed,
                f"{dimension_id} 保持 UNVERIFIED。"
                if passed
                else f"{dimension_id} 未保持 UNVERIFIED。",
            )
        )

    assessments = report.score_snapshot.requirement_assessments
    for dimension_id in case.path_expectation.required_critical_dimensions:
        critical_ids = {
            error_id
            for assessment in assessments
            if assessment.dimension_id == dimension_id
            for error_id in assessment.unresolved_critical_error_ids
        }
        assertions.append(
            _assertion(
                f"critical_dimension:{dimension_id}",
                bool(critical_ids),
                f"{dimension_id} 包含严重错误: {', '.join(sorted(critical_ids))}。"
                if critical_ids
                else f"{dimension_id} 未识别到要求的严重错误。",
            )
        )

    expected_publication = case.path_expectation.job_match_published
    actual_publication = report.score_snapshot.job_match.published
    publication_passed = (
        expected_publication is None
        or actual_publication == expected_publication
    )
    assertions.append(
        _assertion(
            "job_match_publication",
            publication_passed,
            "岗位匹配发布状态不受此案例约束。"
            if expected_publication is None
            else f"岗位匹配发布状态为 {actual_publication}，符合期望。"
            if publication_passed
            else f"岗位匹配发布状态期望 {expected_publication}，实际 {actual_publication}。",
        )
    )

    assertions.extend(evaluate_report_invariants(evidences, report))
    return assertions


__all__ = ["evaluate_interview_path"]
