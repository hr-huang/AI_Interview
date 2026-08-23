"""Frozen resume, JD, answers, and path expectations for dynamic calibration."""

from __future__ import annotations

from functools import lru_cache

from profile_agent.calibration.schemas import (
    InterviewCalibrationCase,
    InterviewPathExpectation,
    LevelRange,
    ScriptedAnswerRule,
)


_TARGET_ROLE = "AI Agent / AI 应用工程师"

_COMMON_JD = """
AI Agent / AI 应用工程师（2026）

负责把真实业务问题建模为可验证的 AI 应用任务，设计 Agent Workflow、状态、节点、
动态路由和 Tool Calling 边界；能够设计 Context、RAG、记忆和工具集成方案；能够使用
AI 协作完成工程交付，并用测试、日志和评测集验收。需要处理超时、重试、幂等、降级、
人工接管和高风险操作授权，同时能够解释成本、延迟、复杂度与持续演进之间的取舍。
候选人可以使用 AI 工具，不以纯手写代码速度作为核心评价标准。
""".strip()


def _rule(
    rule_id: str,
    match_any: list[str],
    answer: str,
    *,
    max_uses: int = 1,
) -> ScriptedAnswerRule:
    return ScriptedAnswerRule(
        id=rule_id,
        match_any=match_any,
        answer=answer,
        max_uses=max_uses,
    )


def _case(
    case_id: str,
    title: str,
    resume_text: str,
    answer_rules: list[ScriptedAnswerRule],
    path_expectation: InterviewPathExpectation,
) -> InterviewCalibrationCase:
    return InterviewCalibrationCase(
        id=case_id,
        title=title,
        resume_text=resume_text,
        jd_text=_COMMON_JD,
        target_role=_TARGET_ROLE,
        answer_rules=answer_rules,
        path_expectation=path_expectation,
    )


def _c01() -> InterviewCalibrationCase:
    return _case(
        "C01",
        "全维度具体且可验收",
        """
        候选人有三年 AI 应用工程经验，负责过客服 Agent 平台。简历声明其设计了状态机、
        RAG、工具权限、离线评测和故障恢复，并通过测试与回放日志持续优化成本和延迟。
        """,
        [
            _rule(
                "C01_agent",
                ["Agent", "Workflow", "状态", "节点", "编排", "路由"],
                "我把流程拆成显式状态、节点和工具边界：状态保存任务上下文与幂等键，节点负责检索、决策和执行，路由由状态机控制，异常进入重试或人工接管。",
                max_uses=2,
            ),
            _rule(
                "C01_business",
                ["业务", "任务", "目标", "需求", "成功标准"],
                "我先把模糊业务目标拆成输入、输出、约束和可测成功标准；语义判断交给模型，规则校验使用确定性代码，并用拒答率和人工升级率验收。",
                max_uses=2,
            ),
            _rule(
                "C01_rag",
                ["RAG", "Context", "上下文", "检索", "记忆"],
                "Context 只放本轮任务，RAG 负责带引用检索，长期记忆与运行状态分开；我会评测召回率、引用正确率、知识过期和上下文污染。",
                max_uses=2,
            ),
            _rule(
                "C01_delivery",
                ["AI 协作", "交付", "测试", "日志", "代码", "验收"],
                "我把需求写成可审查规格，AI 生成代码必须人工 review，并通过单元测试、集成测试、回放日志和离线实验验收，保留规格到测试结果的映射。",
                max_uses=2,
            ),
            _rule(
                "C01_reliability",
                ["可靠性", "失败", "恢复", "重试", "安全", "评测", "人工", "风险"],
                "我区分超时、可重试和不可重试错误，写操作带幂等键，不可重试错误走补偿或降级；高风险动作必须经过授权和人工确认。",
                max_uses=2,
            ),
            _rule(
                "C01_evolution",
                ["成本", "性能", "延迟", "复杂度", "演进", "取舍"],
                "我以单流程和小模型作为基线，比较成本、延迟、复杂度和扩展性，只有收益明确才引入多 Agent，并持续用失败样本复盘演进。",
                max_uses=2,
            ),
        ],
        InterviewPathExpectation(
            required_topics={
                "agent": ["状态", "节点", "Workflow", "编排"],
                "reliability": ["恢复", "重试", "安全", "人工"],
            },
            job_match_published=True,
            max_questions=10,
        ),
    )


def _c02() -> InterviewCalibrationCase:
    return _case(
        "C02",
        "关键词堆叠但缺少事实",
        "候选人简历只列出 Agent、RAG、Workflow、Memory 和 benchmark，没有项目职责、实现细节或验证结果。",
        [
            _rule(
                "C02_keywords",
                [
                    "Agent", "Workflow", "状态", "节点", "业务", "任务", "RAG",
                    "Context", "记忆", "工具", "交付", "测试", "日志", "可靠性",
                    "恢复", "安全", "评测", "成本", "性能", "取舍", "具体", "验证",
                ],
                "Agent、RAG、Workflow、Memory、benchmark。",
                max_uses=10,
            )
        ],
        InterviewPathExpectation(
            required_topics={
                "depth_probe": ["具体", "验证", "依据", "取舍", "怎么做"],
            },
            job_match_published=False,
            max_questions=10,
        ),
    )


def _c03() -> InterviewCalibrationCase:
    return _case(
        "C03",
        "已有项目强但迁移能力弱",
        "候选人负责过订单 Agent Workflow，能够说明状态和节点边界，但没有受监管行业或跨场景迁移经历。",
        [
            _rule(
                "C03_transfer",
                ["迁移", "新场景", "适配", "泛化", "陌生", "受监管"],
                "迁移到受监管的新场景时，我会原样复制现有流程，不新增合规边界、权限校验或重新评测。",
                max_uses=10,
            ),
            _rule(
                "C03_project",
                [
                    "Workflow",
                    "工作流",
                    "动态路由",
                    "状态",
                    "节点",
                    "编排",
                    "项目",
                    "订单",
                ],
                "在已有订单项目中，状态固定保存 order_id、current_step、tool_result、retry_count 和幂等键，解析、校验、查库存、写单分别由独立节点负责。信息缺失时路由回澄清，工具超时最多重试两次，低置信度或写操作转人工；写工具调用前还会校验 order_id 的访问权限。我比较过单 Agent 和 Workflow，因为分支多且写操作必须可回放，所以选择 Workflow，并用任务成功率、重复写入率和人工升级率验收。",
                max_uses=10,
            ),
            _rule(
                "C03_unverified",
                [
                    "RAG",
                    "Context",
                    "记忆",
                    "交付",
                    "测试",
                    "成本",
                    "性能",
                    "安全",
                    "业务目标",
                    "AI任务",
                    "业务问题",
                    "量化",
                    "验收",
                    "监控",
                    "评测",
                    "*",
                ],
                "我没有这方面的真实实践，无法提供可验证的实现或结果。",
                max_uses=10,
            ),
        ],
        InterviewPathExpectation(
            required_topics={
                "transfer": ["迁移", "新场景", "适配", "泛化", "受监管"],
            },
            radar_level_ranges={
                "role_dim_01": LevelRange(min_level="L2", max_level="L3"),
            },
            max_questions=10,
        ),
    )


def _c04() -> InterviewCalibrationCase:
    return _case(
        "C04",
        "关键点充分但不要求穷举",
        "候选人做过并行 Agent Workflow，重点负责共享状态、节点汇合、失败恢复和人工接管，没有穷举所有可选框架。",
        [
            _rule(
                "C04_agent_boundary",
                ["状态", "节点", "汇合", "并行", "join", "Workflow", "编排"],
                "共享状态的 owner 放在编排器，节点只读写自己声明的字段；join 用 correlation id 聚合，超时、重复和部分失败都有明确状态。这样会增加字段映射，但能避免并发覆盖并支持重放。",
                max_uses=3,
            ),
            _rule(
                "C04_recovery_boundary",
                ["失败", "恢复", "重试", "幂等", "人工", "可靠性", "安全", "评测"],
                "失败按超时、可重试和不可重试分类，保存 checkpoint 后恢复，写入使用幂等键；风险动作进入人工确认。只重试可恢复错误是为了避免放大业务错误，即使会增加延迟。",
                max_uses=3,
            ),
            _rule(
                "C04_unverified",
                ["RAG", "Context", "记忆", "成本", "性能", "业务建模"],
                "我没有足够的独立实践，无法补充可验证细节。",
                max_uses=4,
            ),
        ],
        InterviewPathExpectation(
            required_topics={
                "boundary": ["状态", "汇合", "恢复", "人工", "幂等"],
            },
            radar_level_ranges={
                "role_dim_01": LevelRange(min_level="L3", max_level="L3"),
                "role_dim_05": LevelRange(min_level="L3", max_level="L3"),
            },
            max_questions=10,
        ),
    )


def _c05() -> InterviewCalibrationCase:
    return _case(
        "C05",
        "高风险操作缺少授权边界",
        "候选人参与过自动执行 Agent，主张最大化自动化，但简历没有说明权限、审批、人工确认或失败后的二次校验。",
        [
            _rule(
                "C05_unsafe",
                ["高风险", "授权", "审批", "人工确认", "安全", "外部写入", "权限", "风险"],
                "为追求自动化，我允许模型在没有授权、审批或人工确认时直接调用高风险外部写入工具；失败也不需要二次校验。",
                max_uses=3,
            ),
            _rule(
                "C05_unverified",
                ["Agent", "Workflow", "状态", "RAG", "Context", "业务", "交付", "成本", "性能", "*"],
                "我没有相关实践，无法提供可验证的具体实现。",
                max_uses=10,
            ),
        ],
        InterviewPathExpectation(
            required_topics={
                "safety": ["高风险", "授权", "审批", "人工确认", "权限"],
            },
            required_critical_dimensions=["role_dim_05"],
            job_match_published=False,
            max_questions=10,
        ),
    )


def _c06() -> InterviewCalibrationCase:
    return _case(
        "C06",
        "仅两个维度有真实证据",
        "候选人只做过基础 Agent 状态设计和业务任务拆解，没有 RAG、AI 原生交付、可靠性评测或系统演进实践。",
        [
            _rule(
                "C06_agent",
                ["Agent", "Workflow", "状态", "节点", "工具", "编排", "路由"],
                "我用显式状态机区分上下文、节点和工具，节点只写声明字段，工具调用前校验参数。",
                max_uses=2,
            ),
            _rule(
                "C06_business",
                ["业务", "任务", "目标", "需求", "成功标准"],
                "我先定义输入、输出、约束和成功标准，把语义判断交给模型，把规则校验写成确定性验收条件。",
                max_uses=2,
            ),
            _rule(
                "C06_rag_unverified",
                ["RAG", "Context", "检索", "记忆"],
                "我没有相关实践，无法提供可验证细节。",
                max_uses=2,
            ),
            _rule(
                "C06_delivery_unverified",
                ["AI 协作", "交付", "测试", "日志", "代码", "验收"],
                "我没有相关实践，无法提供可验证细节。",
                max_uses=2,
            ),
            _rule(
                "C06_reliability_unverified",
                ["可靠性", "失败", "恢复", "重试", "安全", "评测", "人工", "风险"],
                "我没有相关实践，无法提供可验证细节。",
                max_uses=2,
            ),
            _rule(
                "C06_evolution_unverified",
                ["成本", "性能", "延迟", "复杂度", "演进", "取舍"],
                "我没有相关实践，无法提供可验证细节。",
                max_uses=2,
            ),
        ],
        InterviewPathExpectation(
            forbidden_repeated_topics=["未验证"],
            radar_level_ranges={
                "role_dim_01": LevelRange(min_level="L2", max_level="L3"),
                "role_dim_02": LevelRange(min_level="L2", max_level="L3"),
            },
            expected_unverified_dimensions=[
                "role_dim_03",
                "role_dim_04",
                "role_dim_05",
                "role_dim_06",
            ],
            job_match_published=False,
            max_questions=10,
        ),
    )


@lru_cache(maxsize=1)
def load_interview_calibration_cases() -> tuple[InterviewCalibrationCase, ...]:
    """Return all dynamic interview cases in their frozen order."""

    return (_c01(), _c02(), _c03(), _c04(), _c05(), _c06())


def get_interview_calibration_case(case_id: str) -> InterviewCalibrationCase:
    """Return one dynamic interview case by stable ID."""

    for case in load_interview_calibration_cases():
        if case.id == case_id:
            return case
    raise KeyError(f"未知动态面试校准案例: {case_id}")


__all__ = [
    "get_interview_calibration_case",
    "load_interview_calibration_cases",
]
