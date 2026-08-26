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

## 复审修复追加

针对 `rag-task-5-review.md` 的 4 项 Important 与 3 项 Minor 已完成修复：

- 查询安全边界改为多层、顺序稳定的脱敏：覆盖环境变量式 API/access/private
  key、secret/token/password、Authorization/Proxy-Authorization、Bearer/JWT、AWS
  AKIA/ASIA、GitHub、Slack、`sk-`/`sk_` 等常见形态；脱敏标记本身不会递归匹配。
  所有 query section（包括 coverage gap、JD、resume、recent）都经过同一边界，且
  Supervisor 的自由 `reason` 仍不进入 query。
- 512 字符限制改成 section 独立预算（静态字段、objective、requirement、gap、JD、
  resume、recent），超长字段保留稳定的头尾代表，避免后置锚点被全局截断吞掉。
- Retriever 遍历全部 raw hits 后执行 role/dimension/mode/status/validity/excluded
  硬过滤，再对有效候选排序并按请求上限（最多 3）输出；前三条错误候选不会遮挡第 4
  条合法候选。
- 检索边界只接受带 `RetrievedQuestion`、finite 非空 score、非空 source/index
  provenance 的真实 store hit；裸 record、list envelope、`None`/NaN/inf/非数值分数
  和异常 provenance 都安全降级为 `unavailable`。选中题目的 score、source、index 与
  Task1 trace 强制一致。
- builder 检测重复 requirement ID，明确拒绝 `datetime` 作为 retrieval date；测试补齐
  六种 mode 映射、攻击字符串、第 4 条合法候选、无分数/NaN/inf、超长尾部锚点、裸
  store list、过滤矩阵和稳定性。

本轮 TDD 先运行新增边界测试得到预期失败（超长 section、攻击变体、raw-hit 顺序、
malformed hit、限额传递等），逐项实现后定向测试 **20/20 passed**；全后端
`unittest discover -s tests` **515/515 passed**。`compileall -q profile_agent tests`
和 `git diff --check` 均通过；未执行真实 Embedding/Qdrant 网络请求。

剩余风险仍为首版权重未经真实题库校准，以及上游 store 仍需遵守
`QuestionStoreSearchResult` envelope；本模块对不可信返回已拒绝裸 list/裸 record，并保留
明确的 unavailable 降级。
