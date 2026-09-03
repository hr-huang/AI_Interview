from __future__ import annotations

from profile_agent.services.question_generator_service import (
    _scenario_opening_question,
    _scenario_safe_follow_up,
)
from profile_agent.schemas.scenario_rag_schema import LockedScenarioContext


def test_scenario_opening_asks_for_one_key_decision_and_reason() -> None:
    context = LockedScenarioContext(
        scenario_id="enterprise_knowledge_assistant",
        module_id="knowledge_rag_memory",
        primary_dimension_id="role_dim_03",
        retrieval_unit_id="enterprise_knowledge_assistant::knowledge_rag_memory",
        business_goal="企业希望用知识助手回答最新制度问题",
        candidate_brief="你正在设计一个企业制度知识助手。",
        candidate_focus="知识版本与引用边界",
        retrieval_status="hit",
    )

    question = _scenario_opening_question(context).text

    assert "你正在设计一个企业制度知识助手" in question
    assert "知识版本与引用边界" in question
    assert "关键设计决策" in question
    assert "为什么" in question
    assert "你会怎么设计" not in question
    assert sum(question.count(mark) for mark in ("？", "?")) == 1


def test_safe_follow_up_is_concrete_reproducible_validation_not_generic_prompt() -> None:
    question = _scenario_safe_follow_up(
        "能够说明 RAG 版本更新、引用追溯和失效知识处理"
    ).text

    assert "沿着你刚才的回答" in question
    assert "可复现" in question
    assert "如何验证" in question
    assert "用什么结果判断" in question
    assert "请进一步说明" not in question
    assert sum(question.count(mark) for mark in ("？", "?")) == 1
