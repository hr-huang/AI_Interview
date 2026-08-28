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

## 独立审查后的 hardening follow-up

审查指出原实现的 local repeatability 复用了第一次 evaluation 结果，fake
hard-negative 仅停留在标签/候选池层面，manifest comparison 复用了同一 snapshot，
以及 policy/unavailable 状态和 fake store 默认值仍需收紧。为此补充了五个 RED
测试：

- local loopback 使用同一已构建 index、第二个独立 provider/evaluator 实际执行
  30 次检索，并比较两份独立 report hash；
- fake isolated index 实际注入 210 个 synthetic candidates（30 题 × 7 类），
  每个 intent 的 candidate pool 必须覆盖其 7 个 hard-negative，filter 必须报告
  全部已过滤，且 evaluator 计算的 top-3 hits 必须为空；
- manifest preview 使用 fresh loader snapshot 比较，差异会以
  `manifest_preview_mismatch` 阻断；
- 外部 Qdrant host 在 client 构造前以 `local_url_policy_rejected` / `invalid`
  失败，和 loopback 服务不可用的 `local_qdrant_unavailable` / `unavailable`
  分离；
- `DeterministicFakeQuestionStore(candidate_safe=...)` 默认值改为 `False`，fake
  calibration/tests 显式传入 `True`。

补充测试首次按预期 RED：10 个测试中 5 个失败，分别暴露上述独立 evaluation、
候选索引、manifest、policy 和默认值缺口；修复后聚焦测试为 `10/10 OK`，相关
corpus 组合为 `78/78 OK`。fake artifact 现包含候选池及每个 intent 的
`indexed=210`、`filtered=210`、`eligible=[]` 审计证据，未改变 canonical 30
题或生产数据。

最终验证（在禁用 python-dotenv 自动注入的测试进程中，避免工作站环境影响既有
provider 测试）包括：

```text
question_corpus_zero_cost + question_corpus_evaluation: 25/25 OK
question_corpus_schema + governance + evaluation + zero_cost: 78/78 OK
full unittest discovery: 719/719 OK
audit-corpus: status=valid, questions=30, errors=0, warnings=0
evaluate-local --store fake: status=passed, questions=30, passed=true
evaluate-local --store local (未配置 loopback): status=unavailable, passed=false
compileall: OK
git diff --check: OK
```

follow-up 提交仍仅包含 Task9 指定代码、测试、fake/local 两个 calibration
artifact 与本报告；既有 `manifest_preview.json`、`validation_report.json`、
`evaluation_local.json` 不加入提交。

## Quality follow-up：local failure classification

进一步复审发现 local 分支的裸 `except Exception` 会把 evaluator、schema、
configuration 和程序错误伪装成 unavailable。新增连接失败与 evaluator 失败
测试，并改为安全分类：transport/connection/timeout 仅映射为
`status=unavailable`、`local_qdrant_unavailable`；`EvaluationValidationError`
映射为 `status=failed`、`local_evaluation_failed`；配置/类型/schema 错误映射为
`status=invalid`、`local_configuration_invalid`；其余内部错误映射为
`status=failed`、`internal_failure`。输出仅包含固定安全消息和 category，不回显
异常文本、响应正文或密钥。

本轮聚焦测试为 `12/12 OK`，相关 corpus 组合为 `80/80 OK`。全量测试先发现既有
测试副作用改写了 canonical `questions.json`（造成 content hash mismatch）；因
Task9 禁止污染 canonical corpus，已仅恢复该被测试改写的文件，随后在
`PYTHON_DOTENV_DISABLED=1` 隔离环境下完成 `721/721 OK`。三个既有临时 artifact
仍未加入提交。

## Candidate embedded evaluation CLI (follow-up)

Added `evaluate-candidate-embedded` to `run_question_bank.py`. The command is
dry-run by default and requires explicit `--apply-real-embedding` for the
fixed SiliconFlow `BAAI/bge-m3` configuration. It requires explicit bank,
registry, manifest, intents, isolated candidate index, and artifact paths;
rejects formal production Qdrant paths; validates exactly 30 `needs_review`
records and intents; batches record and query embedding once each; evaluates
through an isolated local Qdrant store with `candidate_safe=True`; runs four
reused-vector supervisor probes plus an exclusion/no-match gate; and writes a
sanitized artifact without credentials or provider response bodies.

Tests were added in `tests/test_question_corpus_candidate_embedded.py` for
path isolation and the default no-network/no-write dry-run. The focused test
runner could not execute in this checkout because the environment's `.venv`
does not contain `pytest`; Python `compileall` completed successfully. Real
embedding execution was intentionally not performed; it remains an explicit
operator action for the parent task.

### Dotenv review follow-up

RED reproduced the missing dotenv load when candidate apply had no injected
environment. GREEN now loads dotenv only after candidate preflight and only
when no environment was injected; dry-run remains entirely provider-free.
The new behavior test and the related zero-cost suite pass (18 tests).

### Trace-preserving candidate evaluation

The embedded candidate path now supplies a two-argument result provider around
the already-batched query vectors. It preserves Qdrant hit metadata and emits
the evaluator trace envelope without any per-intent embedding call. Added
shape coverage passes; candidate/zero-cost tests pass (19 tests).
The trace envelope now expands the first hit's question/source/score/index and
match-tier fields, satisfying the evaluator's strict trace contract.

### Review follow-up

Probe success now follows the local Qdrant result contract (`status="hit"`),
with a shape-oriented unittest assertion. The candidate CLI tests use only
the standard library `unittest` runner. Focused and related unittest suites
pass (54 tests); compileall and diffcheck pass. The full suite and real
embedding remain parent-operator work; no network call was made here.
