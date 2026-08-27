# RAG Task 7 实现报告：安全题库管理命令

## 状态

- 状态：完成。
- 范围：新增 `run_question_bank.py`、`tests/test_run_question_bank.py`，更新
  `README.md`，并保留本报告；未触碰工作树中与 Task 7 无关的既有脏改动。

## 实现摘要

- 提供 `validate`、`audit`、`rebuild`、`sync` 四个子命令；题库 JSON 继续作为唯一事实
  源，Qdrant 仅作为可重建索引。
- `validate` 与 `audit` 只加载/审计题库，绝不构造 embedding 或 Qdrant 客户端；`audit`
  输出过期、即将过期、needs-review、retired、来源问题、无效记录和 eligible 生命周期
  分类。
- `rebuild`/`sync` 默认只输出 dry-run 摘要，不需要 API key；仅显式 `--apply` 才会
  embedding 全部记录、建立 fingerprint 并调用 Qdrant `rebuild`/`sync`。
- 题库校验在任何 embedding/store 工厂前完成；生产默认拒绝 `test_only` synthetic 题库，
  只有显式注入 `test_dependency` 的离线测试才能使用它。
- `main(argv, ...)` 支持 loader、auditor、embedding/store/fingerprint factories、环境、
  日期和输出函数注入，测试使用 fake，无真实网络/API。
- 错误仅返回安全分类和异常类型，不回显题库文本、向量、Authorization header 或 key；
  `--format json` 提供机器摘要，默认 human 摘要；退出码 `0` 成功、`1` 操作失败、`2`
  参数/配置/题库校验失败。

## TDD 证据

先新增 `tests/test_run_question_bank.py` 并运行定向测试，按预期因
`run_question_bank` 不存在得到 `ModuleNotFoundError`（RED）。随后实现最小 CLI 契约并
逐轮修复审计参数别名、题库异常分类和 secret-safe 输出。

## 验证

- 定向：`python -m unittest tests.test_run_question_bank -v` — **10 passed**。
- 全后端：`python -m unittest discover -s tests` — **549 passed**。
- 编译：`python -m compileall -q profile_agent tests run_question_bank.py` — **通过**。
- 空白：`git diff --check` — **通过**（仅报告工作树既有文件的换行提示）。
- secret scan：`git grep -n -E "sk-[A-Za-z0-9]{20,}" -- . ":(exclude).env"` —
  **无匹配**。

## 风险与边界

- 当前仓库没有真实生产题库；现有六条题库仅是明确标记的 synthetic fixture，CLI 会在生产
  默认路径拒绝它。
- `--apply` 会为所有题目调用 SiliconFlow embedding，成本和服务可用性取决于外部 API；
  建议先 dry-run，测试与离线验证不执行真实调用。
- Qdrant 路径默认是 `data/qdrant-question-index`，可通过
  `QUESTION_RAG_INDEX_PATH` 或 `--index-path` 覆盖；下一步仍需真实题库评审与召回校准。

## 复审修复追加

- 修复 I-1：validate、audit 和 dry-run 在任何环境读取或 `load_dotenv` 之前返回；只有
  显式 `--apply` 且题库、参数、依赖形状和路径预检通过后才发现环境配置。dry-run 不会把
  `.env` key 载入当前进程。
- 修复 I-2：audit 使用保留根 role/schema/test-only 边界的容错 raw 路径，交给生命周期审计
  输出 missing/invalid source、invalid record，并追加 duplicate question id/content hash
  与 hash mismatch 诊断；诊断项不会被误计为 eligible。
- 修复 I-3：空题库在 validate、dry-run 和 apply 共用前置校验，统一 code 2，且不构造
  embedding/store。
- 修复 I-4/M-1：预检非空 model/index version、可用 index path、URL、依赖工厂和正维度；
  向量维度或 fingerprint 无效时在 store 之前失败。日期严格限定 `YYYY-MM-DD`，日期/窗口
  溢出按配置错误 code 2 返回。
- 第二轮 TDD 定向测试：**21 passed**（含 env-read/dotenv spy、容错 audit、空题库、路径、
  维度、fingerprint 和日期边界）。
- 第二轮全后端回归：`python -m unittest discover -s tests` — **560 passed**。

## 第二轮复审修复追加

- 将 apply 的 provider、model、dimension、index version、index path 和 base URL 收敛到
  `build_question_index_config()`；默认值与 runtime 当前合同一致（`siliconflow`、
  `BAAI/bge-m3`、`1024`、`questions-v1`），并按 `QUESTION_RAG_*` 优先、legacy alias 回退解析。
  dry-run 继续使用静态默认值，不读取环境、不加载 dotenv。
- tolerant audit 保留 raw 记录诊断路径，严格保留 root `role_version`；记录版本漂移会进入
  `invalid_record` 并从 eligible 排除。畸形 URL 先转换为受控 invalid-source 标记，缺失 URL
  仍分类为 missing-source，不会让单条记录中止 audit。
- embedding client 在付费 embed 前预检声明的 provider/model/dimension；返回向量维度必须与
  canonical config 相同；fingerprint 的 provider/model/dimension/index version 也必须逐项与
  client/config 一致，任何漂移均在 store 构造或 mutation 前以 code 2 失败。
- audit 的 question/source ID 统一稳定短 hash（缺失记录仅保留受控 ordinal），未知 status
  统一为 `invalid`；human/JSON 和参数错误均不回显题目文本、来源 URL、原始 ID/status 或输入
  片段。日期格式、极值和 auditor 的 ValueError/OverflowError 统一返回 validation code 2。
- README 的环境变量示例改为纯注释变量名，明确 unset 与 blank 的区别、apply 成本和 key 不得
  入库；追加测试覆盖默认维度、client identity、fingerprint 漂移、role version、畸形 URL、
  参数错误和 secret-safe 输出。

## 复审修复验证

- 定向：`\.venv\Scripts\python.exe -m unittest tests.test_run_question_bank -v` — **32/32 通过**。
- 全后端：`\.venv\Scripts\python.exe -m unittest discover -s tests` — **571/571 通过**。
- 编译：`\.venv\Scripts\python.exe -m compileall -q profile_agent tests run_question_bank.py` —
  **通过**。
- `git diff --check -- README.md run_question_bank.py tests/test_run_question_bank.py
  rag-task-7-report.md` — **通过**；tracked secret scan（`sk-*`）— **无匹配**；未读取或打印
  任何真实 key。

## 运行时 canonical embedding 修复追加

- 将 provider、model、dimension、index version 与 base URL 的默认值和环境优先级收敛到
  `profile_agent.services.siliconflow_embedding_service.resolve_embedding_config()`；
  `QUESTION_RAG_EMBEDDING_MODEL` 优先，`SILICONFLOW_EMBEDDING_MODEL` 仅作 legacy 回退，
  最终默认 `BAAI/bge-m3`。CLI builder、`SiliconFlowEmbeddingClient.from_env()` 与 runtime
  container 都调用同一解析器，避免 reader/writer/client 身份漂移。
- apply 的默认 embedding factory 将 canonical provider/dimension 传给 client；client 的实际
  model/provider/dimension、返回向量和 manifest fingerprint 逐项校验。provider 返回维度不符
  时在 store 构造前失败；新增测试覆盖 precedence、legacy/default、client identity、runtime
  fingerprint 和真实响应维度校验。README 补充新模型变量及 legacy 兼容说明。

## 本轮验证

- Task7 + embedding service：`\.venv\Scripts\python.exe -m unittest tests.test_run_question_bank tests.test_siliconflow_embedding_service -v` — **51 passed**。
- 全后端：`\.venv\Scripts\python.exe -m unittest discover -s tests` — **576 passed**。
- 编译：`\.venv\Scripts\python.exe -m compileall -q profile_agent tests run_question_bank.py` —
  **通过**。
- `git diff --check`（Task7 相关文件）— **通过**；tracked secret scan（`sk-*`）— **无匹配**。

## 本轮风险

- 运行时仍需由部署环境一致设置 canonical 环境变量；legacy 模型变量只在新变量未设置时
  生效。`--apply` 仍可能产生 embedding 费用并依赖外部服务，验证和 dry-run 不会构造付费
  client。
