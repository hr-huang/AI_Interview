# Task 9：零成本 fake/loopback local calibration 与 policy hardening

## 目标与执行边界

本任务为 30 条 canonical question corpus 增加可重复、零成本的离线
calibration 路径，并为可选的 local Qdrant 路径增加显式 loopback policy。
执行期间不访问外部网络，不构造付费 embedding provider，不生成真实 BGE
向量，不使用生产 Qdrant，不执行 `--apply`，也不读取或输出 `.env` 密钥。

canonical corpus 的 `draft`/`needs_review`/`pending_human` 生命周期保持不变；
fake/local calibration 使用 `candidate_safe=True` 明确表示这是候选发布校验，
不会把候选题库提升为生产 active/approved 数据。

## RED → GREEN 记录

### RED

新增 `tests/test_question_corpus_zero_cost.py`，覆盖：

- deterministic fake embedding 的 hash 稳定性与 provider-free 属性；
- fake store 对 30 条 retrieval intents 的 top-3、trace、hard-negative gate；
- loopback URL allowlist；
- fake CLI 的 provider/HTTP/Qdrant fail-fast guard、报告 repeatability；
- 未配置 explicit loopback Qdrant 时的 honest unavailable 分支。

首次执行：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_zero_cost -v
```

按预期 RED：导入失败，`DeterministicFakeQuestionStore` 尚未实现。

### GREEN

实现并接入：

- `DeterministicFakeEmbedding`：仅由输入文本 SHA-256 派生固定归一化向量，
  identity 为 `provider/model=deterministic-fake`、`index_version=deterministic-fake-v1`；
- `DeterministicFakeQuestionStore`：内存 rebuild/sync/search/retrieve，复用
  fingerprint、日期、角色、维度、模式、trust、expiry、exclude 与 candidate-safe
  policy；
- `QdrantQuestionStore` 的 explicit URL 仅接受 `http/https` 的
  `127.0.0.1`、`localhost`、`::1`，外部 host 在 client 构造前拒绝；candidate-safe
  local 查询可筛选 `active`/`needs_review`，仍由 authoritative catalog 重建记录；
- `evaluate-local --store fake` 完全离线，输出完整 top3/trace/metrics、manifest
  fingerprint、preview comparison、重复报告 hash；
- `evaluate-local --store local` 只接受显式 loopback URL；未配置或不可用时写入
  unavailable，不伪造 local 通过结果；
- fake/local artifact 分离为 `evaluation_fake.json` 与
  `evaluation_local_qdrant.json`，不再写入 Task8 临时
  `evaluation_local.json`。

GREEN 目标测试：

```text
Ran 5 tests ... OK
```

## 验证结果

指定 corpus 组合测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_schema tests.test_question_corpus_governance tests.test_question_corpus_evaluation tests.test_question_corpus_zero_cost -v
```

结果：`Ran 73 tests ... OK`。

完整测试发现套件：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

结果：`Ran 714 tests ... OK`。

离线治理 audit：

```text
AUDIT-CORPUS status=valid as_of=2026-08-28 questions=30 errors=0 warnings=0 dry_run=true
```

fake calibration CLI：

```text
EVALUATE-LOCAL status=passed store=fake questions=30 passed=true dry_run=true
```

artifact 中的 fake gate 指标为：recall@3=1.0、MRR@3=1.0、各维度
recall@3=1.0、acceptable recall@3=1.0、hard-negative hits=0、invalid=0、
duplicate=0、trace coverage=1.0；两次报告 hash 相同。

local calibration CLI（当前环境未配置 explicit loopback Qdrant）：

```text
EVALUATE-LOCAL status=unavailable store=local questions=30 passed=false dry_run=true
```

`evaluation_local_qdrant.json` 明确记录 `local_qdrant.status=unavailable`、
`skipped=true` 及环境缺失原因；其中 embedding 仍明确标记为
`deterministic-fake` 且 `real_embedding=false`，没有伪造 local metrics。

静态/差异检查：

```powershell
.\.venv\Scripts\python.exe -m compileall -q profile_agent run_question_bank.py
git diff --check
```

两项均无输出、无错误。

## 产物与提交范围

Task9 提交包含：

- `profile_agent/services/question_corpus_evaluation.py`
- `profile_agent/services/question_retrieval_service.py`
- `profile_agent/knowledge/qdrant_question_store.py`
- `run_question_bank.py`
- `tests/test_question_corpus_zero_cost.py`
- `artifacts/question_corpus/evaluation_fake.json`
- `artifacts/question_corpus/evaluation_local_qdrant.json`
- 本报告

既有 Task8 临时产物 `manifest_preview.json`、`validation_report.json`、
`evaluation_local.json` 保留在工作树中但不加入本次提交。

## Concerns / follow-up

1. 当前环境没有显式 loopback Qdrant 服务，因此 local backend 仅完成 fail-closed
   unavailable 记录；只有在用户明确提供本机 loopback 服务时才会执行 local
   calibration。
2. deterministic fake 向量只用于契约、路由、生命周期与 repeatability 校验，
   不代表真实 embedding 的语义质量；生产索引仍需显式的 provider 与 `--apply`
   流程。
