# Task 11 题库内容修订报告

## 范围

对 `ai_agent_engineer_2026_h2` 的 11 道 `needs_fix` 题（q008、q015、q019、q020、q022、q023、q024、q025、q028、q029、q030）做内容与治理 sidecar 同步修订；保持 30 题、中文、2026-H2 时效窗口，全部仍为 `needs_review` / `pending_human`，未激活。未联网、未调用外部 API，也未修改 Task9 Python 文件或 artifacts。

## 修订摘要

- q008：把 RAG 事故因果改为“合同生效日晚于索引覆盖时间→新条款未召回”，补充修复、回滚和防复发验收。
- q015：补齐 `call_tool` 接口、输入/输出 schema、3 个副作用/超时/业务拒绝 fixture、测试与回归命令，并保留一项交付前 AI 生成代码审查。
- q019：收窄为初级岗可回答的循环检测、停止条件和告警链路，标注 stretch；修正到 role_dim_05。
- q020：补齐 `evaluate_retrieval` 接口、四个固定 fixture、单元测试/回归测试和一项交付前 AI 代码审查，降为 intermediate。
- q022：改为脱敏字段、审计字段级证据、验收和告警/人工路径；更换为支持 role_dim_05 的来源组合。
- q023/q024：限定为初级岗 stretch 的最小事故/诊断闭环，明确 RTO、陈旧标记、单一漂移信号、反馈和回滚，不要求完整跨区/推荐架构。
- q025：明确三类缺证据问题（无命中、引用不可定位、来源过期），修正到 role_dim_04，并同步检索意图维度。
- q028：补齐确定性的 `merge_cost_events` 接口/schema、3 个固定 fixture、去重/冲突裁决、单元测试/回归测试及一项 AI 审查，降为 intermediate。
- q029/q030：分别收窄为证据时间线/陈旧阈值和哈希/灰度字段/人工批准的最小治理闭环，去除完整容量规划等过载要求。
- 同步 `QuestionSourceRegistry.json`、`review.json`、`dedupe.json`、`locator.json`、`rights.json`、`QuestionBankManifest.json` 与 `retrieval_intents.jsonl`；来源按三题一组配平，题目/语义/sidecar hashes 已重算。locator/rights 恢复为每题两个来源关系且无重复 FK。

## 验证

- `python -m unittest tests.test_question_corpus_schema tests.test_question_corpus_governance tests.test_run_question_bank tests.test_question_corpus_evaluation -q`：107 tests，OK。
- `python run_question_bank.py audit --bank .../questions.json --format json`：`status=ok`；30 records，0 invalid source、0 missing source、0 content hash mismatch、0 duplicate hash，30 条均 `needs_review`，无 eligible/active 题。
- `python run_question_bank.py audit-corpus --corpus-dir ... --dry-run --format json`：`status=valid`；30 questions，0 errors，0 warnings。
- fake local evaluation（离线 deterministic fake）：`passed=true`，30/30，Recall@3=1.0、MRR@3=1.0、trace coverage=1.0；local loopback 未配置时按预期 fail-closed。

未声称人工批准；后续仍需人工抽样与真实检索链路验证。
