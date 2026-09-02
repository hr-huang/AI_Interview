# 工程验证与检索校准

本文档保存主 README 中不适合首页展示、但仍值得复核的测试与检索校准信息。

## 自动化验证

当前 CI 覆盖：

- Python 后端单元 / 集成 / API 测试；
- React 前端组件测试；
- TypeScript + Vite production build；
- Candidate `/start`、LangGraph checkpoint、Scenario fallback 等关键运行路径。

2026-09-02 主分支最近一次验证结果：

```text
Backend   883 passed · 6 warnings · 831 subtests passed
Frontend  12 test files · 43 tests passed
Build     TypeScript + Vite production build passed
```

本地复核：

```powershell
uv sync --frozen --dev
uv run pytest -q
pnpm --dir web install --frozen-lockfile
pnpm --dir web test
pnpm --dir web build
```

CI 配置见 [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)。

## Scenario RAG 校准

当前 `2026-H2` Scenario Bank 维护一组固定 reviewed retrieval cases，用来检查业务场景检索是否出现错配，而不是只凭主观观察判断“看起来相关”。

设计冻结时记录的校准快照：

| 指标 | 结果 | 含义 |
| --- | ---: | --- |
| Reviewed cases | 24 | 固定检索校准案例 |
| Top-1 acceptable | 24 / 24 | 最终采用模块位于可接受集合 |
| Top-3 recall | 24 / 24 | 可接受模块进入 Top-3 |
| Forbidden Top-1 | 0 | 禁止场景没有成为最终选择 |
| Fallback | 0 | 本次校准没有触发回退 |
| Top-3 forbidden diagnostics | 7 | 用于观察相邻场景干扰，不作为单独发布失败项 |

Scenario Bank 本身以审核后的 JSON 为事实源；Qdrant 是可重建的派生索引，不参与评分。

### 校验与索引维护

纯校验 / 预览：

```powershell
uv run python run_scenario_bank.py validate
uv run python run_scenario_bank.py rebuild-index
uv run python run_scenario_bank.py evaluate
```

只有显式增加 `--apply` 才会执行真实 Embedding、Qdrant 写入或检索校准，并可能产生模型调用费用。

## 动态追问回归案例

仓库中的 Graph 集成测试覆盖了一条 Evidence Gap 传递链：

```text
AnswerProcessor
missing_evidence_tags = ["版本", "引用"]

↓

InterviewRuntimeState
latest_gap_tags = ["版本", "引用"]

↓

Constraint Selector
selected = knowledge_policy_version_stale
not selected = knowledge_memory_delete
```

该测试用于证明上一轮回答形成的缺口会改变下一轮可使用的约束，而不是证明模型生成的某一句自然语言追问本身。

对应测试：[`tests/test_scenario_rag_graph_integration.py`](../../tests/test_scenario_rag_graph_integration.py)。

## 验证原则

- ScoreEngine 是唯一数值评分入口；
- Qdrant / Reranker 不参与评分；
- 未验证能力保持 `UNVERIFIED`，不因缺少证据自动得到负分；
- 测试和校准结果用于发现错误，不作为 README 第一屏的营销数字；
- Provider 相关真实运行产物默认作为本地 artifact 管理，固定回归基线才进入版本控制。
