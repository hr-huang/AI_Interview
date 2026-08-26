# RAG Task 5 实现报告：确定性 Retrieval Intent 与 Ranking

## 状态

- 状态：完成。
- 范围：仅新增 `profile_agent/services/question_retrieval_service.py`、
  `tests/test_question_retrieval_service.py` 与本报告；未触碰工作树中既有脏改动。

## 实现摘要

- `build_question_retrieval_intent` 严格解析 Supervisor 的 `target_id` 与
  `primary_requirement_id`，使用 requirement 的 `planned_role_dimension_id`，并按
  `QuestionMode` 得到确定性 difficulty。
- 查询由目标 objective、requirement、coverage gap、有限 JD 锚点、一个简短 resume
  项目锚点和最近两条已回答 turn 组成，固定上限 512 字符；排除项排序、去重，常见
  邮箱/手机号/URL/API token 等在进入 query 前脱敏，不复制完整简历或 JD。
- `QuestionRetriever` 只向 embedding client 发送一个确定性 query，向 store 请求最多
  3 条结果；对 role/dimension/mode/active/valid_until/excluded IDs 做二次硬过滤。
- 排序使用向量相似度、trust、freshness、coverage、mode，以及 duplicate/asked penalty
  的有界加权总分；总分相同则按向量、trust、freshness、question_id、source_id 稳定
  tie-break。`last_rank_trace`/结果上的非序列化 `rank_trace` 记录每个候选的组件与总分，
  最终仍构造 Task1 的严格 `QuestionRetrievalResult` 命中合同。
- 空候选、`no_match`、`unavailable`、`index_mismatch`、embedding/store 异常均诚实
  降级；不把失败伪装成命中，也不输出异常文本。

## TDD 证据

先新增定向测试并运行，按预期得到：

```text
ModuleNotFoundError: No module named 'profile_agent.services.question_retrieval_service'
```

实现后定向测试：

```text
Ran 7 tests ... OK
```

覆盖目标/需求解析、边界字段、确定性与长度、PII/完整上下文边界、排序解释、稳定
tie-break、最多三条、已问排除和所有安全降级状态。

## 验证

- 定向：`python -m unittest tests.test_question_retrieval_service -v` — **7 passed**。
- 全后端：`python -m unittest discover -s tests` — **502 passed**。
- 编译：`python -m compileall -q profile_agent tests` — **通过**。
- 空白：`git diff --check` — **通过**（仅报告工作树既有文件的换行提示，不属于本 Task）。

## 风险与边界

- 评分权重是首版确定性基线，尚未用真实题库标注校准；Task5 不引入网络、真实题库或
  reranker。
- `planned_role_dimension_id` 缺失时明确报错，避免猜测维度造成错误召回；生产 Planner
  需继续输出 Role Pack 中的真实维度 ID。
- 评分明细挂在 retriever/结果的内部诊断属性，不扩展 Task1 的序列化 schema；后续图层
  接入时应保持这些字段不进入候选人可见报告。
