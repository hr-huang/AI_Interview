# Current AI Agent Question Corpus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 为角色 ai_agent_engineer 交付一个可审计的 v2 当前型题库：30 道原创情景题、公开近期证据、六维与模式配额、v1 兼容、primary/compatible 路由、确定性 embedding contract、检索评测和 candidate-safe 运行时边界。先完成全部零成本验证；真实 BGE-M3 建索引只能在最后的显式 STOP gate 后由用户再次批准。

**Architecture:** 在现有 RAG foundation（schema、loader、Qdrant store、retriever、generator、CLI）上增加 v2 corpus contract。题目正文和治理 sidecars 分离，source registry 保存可复核 URL、日期、人工摘要和 locator；纯函数生成六段 embedding text 及 manifest fingerprint；检索执行 primary exact -> compatible fallback -> no_match。内容采集、人工审查、intent 标注和评测各自有文件及验证器，运行时只投影 candidate-safe 字段。

**Tech Stack:** Python 3.11+, Pydantic v2, unittest, 现有 qdrant-client、现有 SiliconFlow embedding client、fake embeddings、fake/local Qdrant、JSON/JSONL、PowerShell。

## Global Constraints

- [ ] 固定角色为 ai_agent_engineer、role_version=2026-H2，corpus_as_of=2026-08-27，题量固定 30。
- [ ] 六维配额固定为 d01=6,d02=5,d03=6,d04=4,d05=6,d06=3。
- [ ] 允许的 primary mode 只有 foundation、project_deep_dive、scenario、system_design、coding、follow_up；配额固定为 foundation=4, project_deep_dive=5, scenario=8, system_design=4, coding=3, follow_up=6。旧输入 project 只兼容映射到 project_deep_dive。
- [ ] v1 的 question_mode 读取为 v2 primary_mode，缺失 compatible_modes 读取为空；v2 对旧接口投影 question_mode=primary_mode。旧 checkpoint、公开报告和结果状态 hit/no_match/unavailable/index_mismatch 继续可读。
- [ ] 路由只在同一角色、版本、dimension、active/as_of/trust 过滤后的集合中执行 primary exact -> compatible fallback -> no_match；不得跨维度或用未覆盖 cell 伪造覆盖率。
- [ ] embedding text 固定六行和顺序：question、business_constraint、skills、dimension_terms、primary_mode、compatible_modes。列表规范化后排序，字段值不得换行；不得包含 difficulty、rubric、source URL/title/ID、company tags、record ID、score、JD/resume/answer/PII 或内部 signals。
- [ ] manifest fingerprint 至少包含 provider、model、vector dimension、embedding text version、question-bank manifest hash、mode-policy version；不匹配只能返回 index_mismatch。
- [ ] 所有 sidecar strict forbid unknown fields；校验唯一主键、外键、日期、状态、trust、content hash、canonical URL、record count、每 URL 题目关联上限。active 题目只允许 medium/high trust。
- [ ] source type 仅为 public interview experience、official technical doc/engineering article、current enterprise JD。来源任务必须真实联网验证公开直接页面；不得绕过登录、验证码或付费墙，不复制长段原文，只保存 URL、日期、人工摘要和 locator。
- [ ] 每题至少一个近期公开面试信号和一个独立官方技术文档或当前企业 JD。官方 evergreen 在 180 天内重新验证，current JD 在 180 天内验证。30 题至少 18 题近 180 天、至少 27 题近 365 天；最多 3 题可使用 2025-01-01 之后 gap-only fallback，必须写 exception reason，并额外绑定近 180 天官方/JD。
- [ ] 至少 12 个独立 canonical URL，每个 URL 最多关联 3 题；角色包既有来源不得直接计作本题库证据，除非它们按本计划逐页验证并重新登记。
- [ ] 题目必须原创、情景化、可追问；review sidecar 记录审阅者/日期、decision、六维/模式/证据检查和修改意见。双 Luna（Luna-1、Luna-2）复核只能作为代理/模型复核记录，完成后 decision 仍为 `pending_human`，不得虚构用户人工审核、签名或批准；只有用户人工抽样并明确批准后，才可将 decision 改为 approved、题目 status 改为 active 并进入索引。expected_signals、critical_errors、follow_up_seeds 永不进入 embedding 或 candidate-safe prompt。
- [ ] 30 个 labeled intents 一题一条，包含 intent_id、role/version、dimension、requested mode、query、gold、acceptable、hard-negative、label notes。acceptable 只能同维 exact/compatible；hard-negative 覆盖错误维度/模式、过期、retired、重复、错误角色、低 trust，且不得进入 top 3。
- [ ] 评测门槛固定：gold Recall@3 >=0.90、MRR@3 >=0.90、每维 Recall@3 >=0.80、hard-negative hits 0、invalid/duplicates 0、retrieval trace 100%。fake/local 全通过之前不得调用真实 provider。
- [ ] 最后真实 BGE-M3 只允许一次 30 题 + 30 query 的约 60 向量批次，无自动循环、重试、调参或自动 --apply。没有 STOP gate 后用户的再次明确批准，绝不执行真实联网/付费 embedding、--apply 或生产 Qdrant 写入。
- [ ] `artifacts/question_corpus/` 当前未被 `.gitignore` 忽略，可保留并提交验证产物；本计划不修改 `.gitignore`。
- [ ] Task 6 的 source registry 允许 `lifecycle=draft` 且 `question_ids=[]` 作为未回填草稿；Task 7 必须回填并验证题目外键、关联上限和覆盖统计后，才可进入发布审计。
- [ ] 每个任务均先 RED 再 GREEN，使用可复制命令及预期结果；每个独立边界完成后小步提交。不得用修改数据绕过验证器。

## Read Before Implementation

实现者在开始任何任务前完整阅读：

- docs/superpowers/specs/2026-08-27-current-ai-agent-question-corpus-design.md
- docs/superpowers/specs/2026-08-26-current-interview-question-rag-design.md
- docs/superpowers/plans/2026-08-26-current-interview-question-rag-implementation.md
- profile_agent/schemas/question_rag_schema.py
- profile_agent/services/question_bank_service.py
- profile_agent/services/question_retrieval_service.py
- profile_agent/knowledge/qdrant_question_store.py
- run_question_bank.py
- profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json
- tests/test_question_rag_schema.py
- tests/test_question_bank_service.py
- tests/test_question_retrieval_service.py
- tests/test_qdrant_question_store.py
- tests/test_run_question_bank.py
- tests/test_question_generator_service.py
- tests/test_question_rag_graph_integration.py

既有 RAG foundation 已覆盖 schema 基础、JSON loader/hash、SiliconFlow client、Qdrant staged index、retrieval ranking、generator/private trace、CLI validate/audit/rebuild/sync；本计划只增加 v2 corpus contract 和其验证，不重复设计已经存在的基础能力。

## Corpus Layout and Shared Interfaces

最终题库目录：

- profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/questions.json
- profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/QuestionBankManifest.json
- profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/QuestionSourceRegistry.json
- profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/review.json
- profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/dedupe.json
- profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/rights.json
- profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/locator.json
- profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/retrieval_intents.jsonl
- artifacts/question_corpus/

必须保持的接口：

| Symbol | Exact signature | Contract |
|---|---|---|
| six-section record formatter | def build_question_embedding_text(record: InterviewQuestionRecord) -> str | 固定六行，禁止内部和证据字段。 |
| six-section query formatter | def build_query_embedding_text(intent: QuestionRetrievalIntent, role_terms: Sequence[str]) -> str | 直接使用 query projection，不伪造题目记录。 |
| record hash | def compute_question_content_hash(record: InterviewQuestionRecord) -> str | canonical sorted JSON、UTF-8、SHA-256、版本化。 |
| manifest hash | def compute_question_bank_manifest_hash(records: Sequence[InterviewQuestionRecord], source_registry: QuestionSourceRegistry, policy: QuestionModePolicy) -> str | 固定排序题目、来源摘要、配额和模式 policy 后 SHA-256。 |
| route | def route_mode_candidates(intent: QuestionRetrievalIntent, candidates: Sequence[RetrievedQuestion], policy: QuestionModePolicy) -> Sequence[RetrievedQuestion] | primary 非空只返回 primary；否则按 policy 取 compatible；空集由调用者返回 no_match。 |
| corpus load | def load_question_corpus_snapshot(corpus_dir: Path, as_of: date) -> QuestionCorpusSnapshot | 一次加载正文、manifest、registry、review、dedupe、rights、locator。 |
| corpus validate | def validate_question_corpus(snapshot: QuestionCorpusSnapshot, role_pack: RolePack, as_of: date) -> Sequence[CorpusIssue] | 只读、确定、结构化报告，不联网。 |
| labeled intent conversion | def intent_to_runtime_intent(item: LabeledQuestionIntent) -> QuestionRetrievalIntent | 丢弃评测标签后形成现有 runtime intent。 |
| local evaluation | def evaluate_question_corpus(snapshot: QuestionCorpusSnapshot, intents: Sequence[LabeledQuestionIntent], store: QuestionStore) -> CorpusEvaluationReport | 产出每条 trace 和固定指标，不调用网络。 |

---

## Task 1: Governance schemas for records, manifest, source and all sidecars

**Files:**
- Modify profile_agent/schemas/question_rag_schema.py
- Add tests/test_question_corpus_schema.py

- [ ] **RED:** 在 tests/test_question_corpus_schema.py 为正文、QuestionBankManifest、QuestionSourceRegistry、review.json、dedupe.json、rights.json、locator.json 和 labeled intent 写最小合法 fixture；分别注入未知字段、重复主键、错误日期类型、空主键、非法 enum，运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_schema -v
  ~~~

  预期：新 schema 尚不存在，新增测试失败；既有 tests/test_question_rag_schema.py 不应被删除或跳过。

- [ ] **GREEN:** 在 question_rag_schema.py 增加 strict Pydantic models：QuestionModePolicy、QuestionCorpusQuotas、v2 InterviewQuestionRecord、QuestionBankManifest、QuestionSourceRegistryEntry、QuestionSourceRegistry、QuestionReviewRecord、QuestionReviewSidecar、QuestionDedupeRecord、QuestionDedupeSidecar、QuestionRightsRecord、QuestionRightsSidecar、QuestionLocatorRecord、QuestionLocatorSidecar、LabeledQuestionIntent、QuestionCorpusSnapshot。所有 model 显式 forbid unknown fields；QuestionCorpusSnapshot 明确含 records、manifest、source_registry、review、dedupe、rights、locator；保留旧 record、retrieval intent、result 和 trace 状态。

- [ ] **RED:** 增加 policy/quotas 失败用例：第七模式、project 作为输出模式、重复 compatible、compatible 含 primary、dimension quota 不是 6/5/6/4/6/3、mode quota 不是 4/5/8/4/3/6、manifest count 不是 30。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_schema -v
  ~~~

  预期：固定 policy 尚未实现，测试失败。

- [ ] **GREEN:** 固定 QuestionModePolicy 的六种 mode、compatible order 和 validate_mode_assignment；固定 QuestionCorpusQuotas 的题量、维度和 primary mode 配额。source model 含 source_id、source_type、canonical_url、title、published_at、verified_at、accessed_at、trust、lifecycle、question_ids、human_summary；review/rights/locator/dedupe model 的字段由设计 spec 原样落地。

- [ ] **RED:** 回归 schema 与静态编译：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_rag_schema tests.test_question_corpus_schema -v
  .\.venv\Scripts\python.exe -m compileall profile_agent/schemas/question_rag_schema.py
  ~~~

  预期：新增 schema tests 全部通过，旧 schema tests 全部通过。

- [ ] **GREEN:** 只提交 schema 和 schema tests，不在此任务写题库内容或联网采集：

  ~~~powershell
  git add profile_agent/schemas/question_rag_schema.py tests/test_question_corpus_schema.py
  git diff --cached --check
  git commit -m "feat: define question corpus governance schemas"
  ~~~

## Task 2: v1/v2 projection, mode policy and compatible-mode contract

**Files:**
- Modify profile_agent/services/question_bank_service.py
- Modify profile_agent/services/question_retrieval_service.py
- Modify profile_agent/graphs/interview.py
- Modify profile_agent/web/container.py
- Modify tests/test_question_bank_service.py
- Modify tests/test_question_retrieval_service.py
- Modify tests/test_question_rag_graph_integration.py

- [ ] **RED:** 添加 v1 JSON、v2 JSON、旧 checkpoint 和 public projection fixtures，断言旧 question_mode 能读取，v1 缺省 compatible 为空，v2 能投影旧字段，未知 role/version 与非法 mode 被拒绝。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_bank_service tests.test_question_retrieval_service tests.test_question_rag_graph_integration -v
  ~~~

  预期：当前 loader 没有 v2 projection 和 mode policy，新增测试失败。

- [ ] **GREEN:** 在 question_bank_service.py 实现 project_v1_record_to_v2(record)、project_v2_record_to_v1(record)、normalize_project_mode(value)；在 question_retrieval_service.py 固定 policy 数据和 mode assignment 校验，不在本任务改变 Qdrant 查询顺序。v1 content hash 继续可读，v2 hash 纳入 business constraint、dimension terms、primary 和 compatible。

- [ ] **RED:** 给 build_interview_graph 保持前五个位置参数、retrieval trace private field 和 public output 的兼容性写测试；运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_rag_graph_integration tests.test_rag_final_api_fix -v
  ~~~

  预期：若 keyword-only policy 注入破坏旧调用，测试应先暴露该回归。

- [ ] **GREEN:** 在 graph/container 只增加 keyword-only policy/manifest dependency；保留旧 checkpoint 的字段读取和 public projection 的 provenance stripping。任何 v1 record 进入 v2 retrieval 前显式 projection，禁止业务代码隐式猜字段。

- [ ] **RED:** 运行全兼容性回归：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_rag_schema tests.test_question_bank_service tests.test_question_retrieval_service tests.test_question_rag_graph_integration tests.test_rag_final_api_fix -v
  ~~~

  预期：新增与既有测试通过，并能证明旧状态名未改变。

- [ ] **GREEN:** 更新最小必要 fixture 和 docstring，提交：

  ~~~powershell
  git add profile_agent/services/question_bank_service.py profile_agent/services/question_retrieval_service.py profile_agent/graphs/interview.py profile_agent/web/container.py tests/test_question_bank_service.py tests/test_question_retrieval_service.py tests/test_question_rag_graph_integration.py
  git commit -m "feat: preserve v1 question mode compatibility"
  ~~~

## Task 3: Deterministic six-section embedding text and manifest fingerprint

**Files:**
- Modify profile_agent/services/question_bank_service.py
- Modify profile_agent/services/question_retrieval_service.py
- Modify profile_agent/knowledge/qdrant_question_store.py
- Modify profile_agent/schemas/question_rag_schema.py
- Add tests/fixtures/question_corpus_v2/embedding_contract.json
- Modify tests/test_question_bank_service.py
- Modify tests/test_question_retrieval_service.py
- Modify tests/test_qdrant_question_store.py

- [ ] **RED:** 为同一 record 的 JSON key 重排、skills/terms 重排、空白变化、非 embedding metadata 变化写 table-driven tests；断言六行键序严格为 question、business_constraint、skills、dimension_terms、primary_mode、compatible_modes，字段无换行；注入 difficulty、source、company、ID、score、JD/resume/answer/PII、expected signals 并断言不泄漏。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_bank_service tests.test_question_retrieval_service -v
  ~~~

  预期：六段 formatter 尚未实现，测试失败。

- [ ] **GREEN:** 实现 QuestionEmbeddingProjection 和 build_question_embedding_text；列表采用 Unicode whitespace collapse、trim、casefold key sort 并保留 canonical token，空列表输出空值。实现 build_query_embedding_text 时直接把 retrieval intent 和 role terms 组装为 projection，不构造伪造 record。将完整 six-section fixture 写入 embedding_contract.json。

- [ ] **RED:** 为 hash 和 fingerprint 写测试：改变题干、business constraint、skill、dimension term、primary、compatible 会改变输入；改变非 embedding metadata 只改变 record content hash；改变 provider/model/vector dimension/text version/manifest hash/policy version 返回 index mismatch。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_bank_service tests.test_qdrant_question_store -v
  ~~~

  预期：旧 fingerprint 只有四字段，新增 mismatch tests 失败。

- [ ] **GREEN:** 实现 compute_question_content_hash、compute_question_bank_manifest_hash 的排序 JSON/UTF-8/SHA-256；将 IndexFingerprint 扩展为 provider、model、vector dimension、index version、embedding text version、question-bank manifest hash、mode-policy version。Qdrant mismatch 分支保持 index_mismatch，payload 仅存必要检索字段和内部 trace metadata。

- [ ] **RED:** 做 formatter、payload、fingerprint 的回归：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_bank_service tests.test_question_retrieval_service tests.test_qdrant_question_store -v
  git diff --check
  ~~~

  预期：实现与 fixture 逐字一致，旧 store staged alias/rollback tests 通过。

- [ ] **GREEN:** 提交 contract：

  ~~~powershell
  git add profile_agent/services/question_bank_service.py profile_agent/services/question_retrieval_service.py profile_agent/knowledge/qdrant_question_store.py profile_agent/schemas/question_rag_schema.py tests/fixtures/question_corpus_v2/embedding_contract.json tests/test_question_bank_service.py tests/test_question_retrieval_service.py tests/test_qdrant_question_store.py
  git commit -m "feat: make question embeddings deterministic"
  ~~~

## Task 4: Strict validators and read-only CLI audit

**Files:**
- Add profile_agent/services/question_corpus_governance.py
- Modify profile_agent/services/question_bank_service.py
- Modify run_question_bank.py
- Add tests/test_question_corpus_governance.py
- Modify tests/test_run_question_bank.py

- [ ] **RED:** 为 loader/validator 写最小合法 snapshot 和每类非法 snapshot：unknown field、duplicate PK、orphan question/source FK、wrong count/role/version、invalid mode/dimension、active low trust、bad hash/URL、source URL 超 3 题、missing review/rights/locator；运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_governance tests.test_run_question_bank -v
  ~~~

  预期：新 governance module 和 CLI action 不存在，测试失败。

- [ ] **GREEN:** 实现 load_question_corpus_snapshot(corpus_dir, as_of) 和 validate_question_corpus(snapshot, role_pack, as_of) -> Sequence[CorpusIssue]。CorpusIssue 固定 code、path、message、severity。validator 检查所有 sidecar strictness、PK/FK、record count、role/version、quota、content hash、canonical URL、state/trust、active eligibility、review decision、rights、dedupe 和 locator。

- [ ] **RED:** 为证据时间窗写 boundary tests：每题 interview + independent official/JD，近180/近365计数，最多3 fallback、2025-01-01 下界、gap-only、例外理由、额外近180 official/JD、evergreen/current JD 的 verified date、至少12独立 URL及 URL association cap。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_governance -v
  ~~~

  预期：规则未完全实现时，每个违反条件的 fixture 都失败。

- [ ] **GREEN:** 固化日期计算为 corpus_as_of 与 published/verified/accessed date 的 date arithmetic；source taxonomy 只接受三类公开来源，拒绝搜索结果页、登录、验证码、付费墙、不可达转载；active 只接受 medium/high trust。validator 只读，不访问网络，不重写数据。

- [ ] **RED:** 为 CLI 增加 read-only/dry-run tests，断言 audit-corpus、manifest 和 evaluate-local 不需要 embedding 环境变量、不触发 provider、不会调用 Qdrant 写操作；运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_run_question_bank -v
  ~~~

  预期：当前 CLI 缺少 corpus-dir/dry-run audit 路径，测试失败。

- [ ] **GREEN:** 在 run_question_bank.py 增加 --corpus-dir 默认路径、audit-corpus、manifest、evaluate-local actions 与 --dry-run；输出 artifacts/question_corpus/validation_report.json 和 manifest_preview.json。保留既有 validate/audit/rebuild/sync 行为；--apply 仍是显式动作且本任务不执行。提交：

  ~~~powershell
  git add profile_agent/services/question_corpus_governance.py profile_agent/services/question_bank_service.py run_question_bank.py tests/test_question_corpus_governance.py tests/test_run_question_bank.py
  git commit -m "feat: audit question corpus without writes"
  ~~~

## Task 5: Exact-primary, compatible-fallback retrieval and honest no_match

**Files:**
- Modify profile_agent/services/question_retrieval_service.py
- Modify profile_agent/knowledge/qdrant_question_store.py
- Modify profile_agent/services/question_generator_service.py
- Modify profile_agent/graphs/interview.py
- Modify profile_agent/web/container.py
- Modify tests/test_question_retrieval_service.py
- Modify tests/test_qdrant_question_store.py
- Modify tests/test_question_generator_service.py
- Modify tests/test_question_rag_graph_integration.py

- [ ] **RED:** 写四组 retrieval tests：primary 有候选时 compatible 不得参与；primary 为空但 compatible 有候选时 trace 标为 fallback；两者为空时 selected 为空且 status 为 no_match；跨 dimension/role、过期、retired、低 trust、excluded ID 的候选均被过滤。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_retrieval_service tests.test_qdrant_question_store -v
  ~~~

  预期：当前只做 exact question_mode，新增 tier tests 失败。

- [ ] **GREEN:** 实现 ModeMatchTier 与 route_mode_candidates；retriever 使用一次安全 candidate fetch 或两次明确查询，但只有 primary 结果为空才进入 compatible；compatible order 固定来自 QuestionModePolicy，不能用分数跨 tier。store payload/filter 绑定 role、role_version、dimension、active、as_of、trust、fingerprint；trace 记录 tier、question_id/source_id/score/index_version，public output 不暴露这些内部字段。

- [ ] **RED:** 写 generator prompt safety tests：candidate 只含 question、business_constraint、skills 和必要 dimension/mode context，不含 source URL/title/ID、score、rubric、company tags、JD/resume/answer/PII、expected signals、critical errors、follow-up seeds；运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_generator_service tests.test_question_rag_graph_integration -v
  ~~~

  预期：旧 prompt expectations 与新 candidate-safe contract 冲突，测试失败。

- [ ] **GREEN:** 更新 candidate projection 和 tests；保留 InterviewTurn.retrieval_trace private/exclude 行为、既有 generator fallback 和 public projection。旧 index 不具备 v2 fingerprint 时返回 index_mismatch，不能当作 compatible corpus。

- [ ] **RED:** 做全 retrieval/graph 回归：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_retrieval_service tests.test_qdrant_question_store tests.test_question_generator_service tests.test_question_rag_graph_integration tests.test_rag_final_api_fix -v
  ~~~

  预期：所有 exact/compatible/no_match、trace 和 prompt safety 测试通过。

- [ ] **GREEN:** 提交：

  ~~~powershell
  git add profile_agent/services/question_retrieval_service.py profile_agent/knowledge/qdrant_question_store.py profile_agent/services/question_generator_service.py profile_agent/graphs/interview.py profile_agent/web/container.py tests/test_question_retrieval_service.py tests/test_qdrant_question_store.py tests/test_question_generator_service.py tests/test_question_rag_graph_integration.py
  git commit -m "feat: enforce exact compatible question routing"
  ~~~

## Task 6: Real public-source discovery and source registry

**Files:**
- Add artifacts/question_corpus/source_discovery_notes.md
- Add profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/QuestionSourceRegistry.json
- Add tests/fixtures/question_corpus_v2/source_registry_minimal.json
- Modify tests/test_question_corpus_governance.py

- [ ] **RED:** 先用真实联网浏览公开直接页面，建立 discovery ledger 的失败清单测试：搜索结果页、登录页、验证码、付费墙、不可访问转载、没有发布日期或无法人工定位的页面不得进入 registry。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_governance -v
  ~~~

  预期：registry 尚未存在，source acceptance tests 失败。

- [ ] **GREEN:** 实际验证并登记至少 12 个独立 canonical URL，来源只来自三类允许 source type；Task 6 的 registry 草稿每条保存 source_id、canonical_url、title、source_type、published_at、verified_at、accessed_at、trust、`lifecycle=draft`、`question_ids=[]`、人工摘要和可复核 locator。draft 的空 question_ids 只表示尚未完成 Task 7 题目关联，不计入覆盖率，也不能进入 active 或索引。对公开面试经历保存事实摘要而不是大段原文；官方文档/JD 保存与题目能力相关的人工摘要。绕过登录、验证码或付费墙的页面不得使用。

- [ ] **RED:** 用固定 fixture 测 registry canonicalization：去 tracking query、保留 scheme/host/path/必要 query；duplicate canonical URL、source type、日期、question_ids 超 cap、低 trust active、role/version 不一致必须失败。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_governance -v
  ~~~

  预期：真实 registry 尚未满足严格引用检查时测试失败。

- [ ] **GREEN:** 完成 source_discovery_notes.md，每个 URL 写验证日期、页面可达性、来源分类、人工摘要和拟覆盖能力；在 registry 中保留稳定 URL，不保存 cookies、tokens、全文复制或个人敏感信息。角色包四条基础来源只作为背景，只有单独验证且具 question_ids 时才计入本 registry。

- [ ] **RED:** 做 source freshness/count audit，预期若不足 18 题近180、27 题近365、12 URL 或 association cap 超限则返回确定 error codes：

  ~~~powershell
  .\.venv\Scripts\python.exe run_question_bank.py audit-corpus --corpus-dir profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2 --dry-run
  ~~~

  预期：在 Task 7 题目关联完成前命令报告缺题/缺关联，不允许假绿。

- [ ] **GREEN:** 只在每个来源可直接复核、分类和日期都写入后提交：

  ~~~powershell
  git add artifacts/question_corpus/source_discovery_notes.md profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/QuestionSourceRegistry.json tests/fixtures/question_corpus_v2/source_registry_minimal.json tests/test_question_corpus_governance.py
  git commit -m "docs: register verified question corpus sources"
  ~~~

## Task 7: Produce 30 original scenario questions and governance sidecars

**Files:**
- Add profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/questions.json
- Add profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/QuestionBankManifest.json
- Add profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/review.json
- Add profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/dedupe.json
- Add profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/rights.json
- Add profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/locator.json
- Modify profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/QuestionSourceRegistry.json
- Add tests/fixtures/question_corpus_v2/question_matrix.json
- Modify tests/test_question_corpus_governance.py

- [ ] **RED:** 先添加完整配额矩阵测试；矩阵必须逐项包含以下 30 个 question_id、dimension_id、primary_mode，compatible_modes 必须由 Task 1 的 policy 校验生成：

  | Dimension | Question IDs and primary modes |
  |---|---|
  | d01 | q001 foundation; q002 project_deep_dive; q003 scenario; q004 system_design; q005 follow_up; q006 scenario |
  | d02 | q007 foundation; q008 scenario; q009 project_deep_dive; q010 scenario; q011 follow_up |
  | d03 | q012 foundation; q013 scenario; q014 system_design; q015 coding; q016 follow_up; q017 project_deep_dive |
  | d04 | q018 project_deep_dive; q019 scenario; q020 coding; q021 follow_up |
  | d05 | q022 foundation; q023 scenario; q024 system_design; q025 scenario; q026 follow_up; q027 project_deep_dive |
  | d06 | q028 coding; q029 system_design; q030 follow_up |

  q010 将 d02 的 primary mode 从 coding 调整为 scenario，q028 将 d06 的 primary mode 从 scenario 调整为 coding；两题交换保持 foundation=4、project_deep_dive=5、scenario=8、system_design=4、coding=3、follow_up=6 的总配额不变。

  运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_governance -v
  ~~~

  预期：questions.json 和 sidecars 尚未存在，测试失败。

- [ ] **GREEN:** 创作 30 道原创、具体业务约束驱动、可追问的情景题。每条正文记录必须有 question_id、question_text、role、role_version、dimension_id、skills、business_constraint、dimension_terms、primary_mode、compatible_modes、difficulty、expected_signals、critical_errors、follow_up_seeds、status、version、content_hash 和 source_ids；source URL/title 等治理字段只通过 registry/sidecar 关联。正文必须覆盖角色包六维，兼容 mode 只能来自 frozen policy。双 Luna 复核可记录为代理/模型审阅者，但不得冒充人工签署：两次 Luna 复核完成后 review decision 仍为 `pending_human`，题目保持非 active 状态。

- [ ] **RED:** 为 30 题写 review/dedupe/rights/locator FK tests：每题一条 review；双 Luna 复核记录存在时 decision 仍必须为 `pending_human`，测试拒绝虚构的人工 approved/active，只有用户人工抽样/批准 gate 通过后才允许改为 approved/active；每题 dedupe 记录必须记录 canonical text hash/near-duplicate decision；rights 必须记录原创声明、来源摘要边界和许可检查；locator 必须能指向 source_id 的页面 section/heading/date；所有 source_ids 必须在 registry。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_governance -v
  ~~~

  预期：sidecar 字段缺失或关联未完成时失败。

- [ ] **GREEN:** 完成代理/模型复核记录和 sidecars；不得声称已完成人工审核，不得填写虚构人工姓名、签名或批准日期。Luna-1 与 Luna-2 的复核只记录发现和修改意见，完成后 review decision 仍为 `pending_human`，题目保持非 active。明确交付用户人工抽样/批准 gate：用户逐题或按约定样本抽查并明确批准后，才可将对应 review decision 改为 approved、题目 status 改为 active 并允许建索引；在此之前只能进行 candidate-safe 的 fake/local 评测。Task 7 同时将每个 source registry 草稿的 question_ids 回填为真实题目 ID，验证 source/question FK、每 URL 不超过 3 题及覆盖统计，确保至少 18/30 近180、27/30 近365，fallback 不超过3且每例写 exception_reason、gap-only、2025-01-01 后和额外近180 official/JD。用 loader 重算 content_hash 与 manifest hash，禁止手写伪 hash。

- [ ] **RED:** 做完整 corpus audit：

  ~~~powershell
  .\.venv\Scripts\python.exe run_question_bank.py audit-corpus --corpus-dir profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2 --dry-run
  ~~~

  预期：30 条、六维/模式配额、证据、URL cap、review/dedupe/rights/locator 全部未通过的项目逐条列出；即使结构与证据审计 zero errors，`pending_human` 仍是发布 gate，用户人工抽样/批准前不把题目改为 active、不生成可用索引。

- [ ] **GREEN:** audit 显示 zero errors、warnings 仅为明确允许的 freshness 信息后，提交数据与 fixture：

  ~~~powershell
  git add profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/questions.json profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/QuestionBankManifest.json profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/QuestionSourceRegistry.json profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/review.json profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/dedupe.json profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/rights.json profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/locator.json tests/fixtures/question_corpus_v2/question_matrix.json tests/test_question_corpus_governance.py
  git commit -m "content: author and review current agent questions"
  ~~~

## Task 8: Label 30 intents and implement the evaluation harness

**Files:**
- Add profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/retrieval_intents.jsonl
- Add profile_agent/services/question_corpus_evaluation.py
- Add tests/test_question_corpus_evaluation.py
- Modify run_question_bank.py

- [ ] **RED:** 添加 labeled-intent schema/evaluation tests，要求 q001 到 q030 各有一个 intent_id；每行必须有 role/version、dimension、requested_mode、query_text、gold、acceptable、hard_negative、label_notes；gold 只能对应自身题目，acceptable 同维且 exact/compatible，hard-negative 要覆盖错误维度、错误模式、expired、retired、duplicate、wrong role、low trust。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_evaluation -v
  ~~~

  预期：intents 文件与 evaluation module 不存在，测试失败。

- [ ] **GREEN:** 编写 30 条自然的岗位/业务查询，gold 一题一条；hard negatives 明确分类并配置为 absent from top 3 的断言；实现 intent_to_runtime_intent，转换时只填现有 QuestionRetrievalIntent 需要的 runtime 字段，不把标签/来源/内部信号送入 embedding。

- [ ] **RED:** 为 fake store 评测写测试：固定向量和候选顺序下计算 Recall@3、MRR@3、dimension Recall@3、hard-negative hits、invalid/duplicate count、trace coverage；缺 gold、duplicate、无 trace 或错误 tier 必须失败。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_evaluation -v
  ~~~

  预期：指标实现不存在，测试失败。

- [ ] **GREEN:** 在 question_corpus_evaluation.py 实现 CorpusEvaluationReport、每 intent result、evaluate_question_corpus 和 JSON serializer；指标使用固定的小数规则并保留每条 top3、gold rank、match tier、trace。gold Recall@3 >= 0.90、MRR@3 >= 0.90、每维 >= 0.80、hard-negative hits 0、invalid/duplicates 0、trace 100% 作为 pass predicate；失败应返回非零 CLI exit code。

- [ ] **RED:** 运行 CLI label/evaluate-local tests，断言只读、不访问网络、不要求 provider env：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_evaluation tests.test_run_question_bank -v
  ~~~

  预期：CLI 尚未接入 evaluation harness，测试失败。

- [ ] **GREEN:** 将 evaluate-local 接入 run_question_bank.py，读取 corpus snapshot 与 JSONL，允许 --store fake 或 loopback local Qdrant test double，输出 artifacts/question_corpus/evaluation_local.json；提交：

  ~~~powershell
  git add profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/retrieval_intents.jsonl profile_agent/services/question_corpus_evaluation.py tests/test_question_corpus_evaluation.py run_question_bank.py
  git commit -m "feat: add labeled question corpus evaluation"
  ~~~

## Task 9: Zero-cost fake/local calibration and policy hardening

**Files:**
- Modify profile_agent/services/question_corpus_evaluation.py
- Modify profile_agent/services/question_retrieval_service.py
- Modify profile_agent/knowledge/qdrant_question_store.py
- Modify run_question_bank.py
- Add tests/test_question_corpus_zero_cost.py
- Add artifacts/question_corpus/evaluation_fake.json
- Add artifacts/question_corpus/evaluation_local_qdrant.json

- [ ] **RED:** 写 zero-cost tests，monkeypatch SiliconFlow/HTTP/provider 构造函数为 fail-fast；运行 fake store 30 queries，另在可用时连接 127.0.0.1 local Qdrant，不允许外部 host；断言任何网络/付费调用均被捕获。运行：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_zero_cost -v
  ~~~

  预期：当前 CLI/retriever 仍可能实例化 provider，fail-fast tests 失败。

- [ ] **GREEN:** 增加 deterministic fake embedding（输入 hash 到固定向量）和 fake/local Qdrant adapter；让 evaluate-local --store fake 完全离线，local Qdrant 只允许 loopback 明确配置，不自动启动服务。运行 fake corpus evaluation，保存完整 top3、trace、指标和 manifest fingerprint；若 local Qdrant 不可用，记录环境缺失但 fake gate 仍必须通过，不得伪造 local 结果。

- [ ] **RED:** 执行全部 zero-cost commands，预期在数据、schema、evidence、routing、metrics 任一不满足时退出非零：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_corpus_schema tests.test_question_corpus_governance tests.test_question_corpus_evaluation tests.test_question_corpus_zero_cost -v
  .\.venv\Scripts\python.exe run_question_bank.py audit-corpus --corpus-dir profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2 --dry-run
  .\.venv\Scripts\python.exe run_question_bank.py evaluate-local --corpus-dir profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2 --store fake --dry-run
  ~~~

  预期：所有 fake/local 指标达到门槛，audit zero errors，CLI 不读 embedding key、不执行 --apply。

- [ ] **GREEN:** 固化 provider guard、local host allowlist、manifest preview 比较、repeatability check（同输入两次报告 hash 相同）；保存两个 artifact，注明 fake/local、模型为 deterministic fake、没有真实 BGE 向量。提交：

  ~~~powershell
  git add profile_agent/services/question_corpus_evaluation.py profile_agent/services/question_retrieval_service.py profile_agent/knowledge/qdrant_question_store.py run_question_bank.py tests/test_question_corpus_zero_cost.py artifacts/question_corpus/evaluation_fake.json artifacts/question_corpus/evaluation_local_qdrant.json
  git commit -m "test: calibrate question corpus with zero cost"
  ~~~

## Task 10: Full verification and explicit paid/network STOP gate

**Files:**
- Modify docs/superpowers/plans/2026-08-27-current-ai-agent-question-corpus-implementation.md only if verification discovers a plan inconsistency
- No product or corpus data file may be changed by this task unless a failing test identifies a concrete contract defect

- [ ] **RED:** 在执行任何真实 embedding 前运行全仓相关验证：

  ~~~powershell
  .\.venv\Scripts\python.exe -m unittest tests.test_question_rag_schema tests.test_question_bank_service tests.test_question_retrieval_service tests.test_qdrant_question_store tests.test_run_question_bank tests.test_question_generator_service tests.test_question_rag_graph_integration tests.test_question_corpus_schema tests.test_question_corpus_governance tests.test_question_corpus_evaluation tests.test_question_corpus_zero_cost -v
  .\.venv\Scripts\python.exe -m compileall profile_agent
  git diff --check
  ~~~

  预期：所有测试通过；manifest hash、record hash、six-section fixture、30 intents、30 records、six-dimension/mode quota、source freshness、URL cap、retrieval metrics、prompt safety 和 trace coverage 均有证据。

- [ ] **GREEN:** 运行只读审计与 fake evaluation 两次，比较 artifact hash；检查 runtime container 在没有 embedding key、网络和 Qdrant 写权限时仍能加载 manifest 或安全返回 unavailable/index_mismatch；用 Select-String 扫描计划和新增代码中的 placeholder/待补/模糊引用词，不得有命中。把 verification report 保存到 artifacts/question_corpus/verification_report.json，不保存秘密、全文来源或付费 provider 响应。

- [ ] **RED:** 输出并人工确认以下 STOP checklist，任何一项未满足都不得继续：

  ~~~text
  STOP: zero-cost corpus, fake/local evaluation, source audit, and compatibility tests passed.
  Dual Luna review is recorded but remains pending_human; no user manual sample/approval has been fabricated.
  No question may become active until the user completes the manual sampling/approval gate.
  No real BGE-M3 request, no paid embedding, no --apply, and no production Qdrant write has run.
  The next action, if desired, requires fresh explicit user approval after this STOP.
  ~~~

  预期：命令只打印 STOP，不创建真实 index，不访问外部 embedding 服务。

- [ ] **GREEN:** 在 STOP 后立即结束本计划的自动执行；不要预先执行 --apply、联网付费 embedding、重试/调参或生产 Qdrant 写入。用户必须先完成人工抽样/批准 gate，批准的题目才可从 `pending_human`/非 active 状态变为 approved/active；这一步独立于付费索引授权。只有用户在看到 STOP 后再次明确批准真实 embedding，才另开受授权任务，先复述一次约 60 向量 BGE-M3 单批范围和 provider/model/dimension/fingerprint，再由用户选择是否运行。提交最终 verification artifact：

  ~~~powershell
  git add artifacts/question_corpus/verification_report.json
  git commit -m "test: verify question corpus and stop before paid indexing"
  ~~~

  此 commit 是本计划的终点；若没有用户后续批准，仓库应停留在 fake/local 已验证、无真实 BGE index 的状态。

## Final Self-Review Before Handoff

- [ ] 逐条对照 docs/superpowers/specs/2026-08-27-current-ai-agent-question-corpus-design.md：30 题、六维配额、六种模式及 primary 配额、compatible policy、18/30 近180、27/30 近365、最多3 fallback、至少12 URL、URL 最多3题、三类来源、原创情景题、人工 review、30 intents、gold/acceptable/hard-negative、指标门槛、candidate-safe prompt、v1 兼容、manifest fingerprint、zero-cost gate 全部有明确任务和验证命令。
- [ ] 检查每一任务都有准确文件路径、接口名、RED 命令及预期失败、GREEN 命令及预期通过、独立 commit；任务总数恰为 10。
- [ ] 用 Select-String 检查本计划没有省略号、占位标记或模糊路径；检查 fenced commands 可复制且路径与现有布局一致。
- [ ] 检查 git status --short 只有本计划文件；运行 git diff --cached --check 后使用 commit message docs: plan current ai agent question corpus 提交本计划。
