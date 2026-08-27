# 当前 AI Agent 面试题语料库设计（2026-H2）

> 状态：设计规格。本文冻结下一阶段的语料范围、证据和检索契约，不声称已经完成 30 道题的采集、审核或建库。
>
> 日期：2026-08-27

## 1. 决策摘要

本规格只服务一个岗位：**AI Agent 应用工程师（校招/初级）**，岗位版本为 2026-H2。目标语料库为 30 道经人工审核的、候选人可直接作答的原创情景题。每道题必须同时具备：

1. 至少一条 2025—2026 年公开面试经验信号；
2. 至少一条官方技术文档或当前企业 JD 的交叉验证；
3. 可定位、可复核、无版权和隐私越界的离线来源记录。

来源只用于离线研究、审核和追溯。运行时不联网搜题，不把来源原文、答案、个人信息或付费内容送入模型；运行时只做轻量、候选人安全的情景个性化。

30 道题同时受六维配额和 primary mode 配额约束。每道题恰好属于一个维度，有一个 primary_mode，以及零个或多个受控 compatible_modes。检索严格按“精确 primary mode → compatible mode → no_match”逐级尝试，任何情况下都不跨维度兜底。

现有 RAG 基础设施仍是本设计的底座：版本化 JSON 是权威源，Qdrant 是可重建的本地索引，Supervisor 仍只决定本轮考察目标和题型。现有 QuestionMode、QuestionRetrievalResult、私有 InterviewTurn.retrieval_trace 和候选人侧出题降级路径尽量保持兼容。

## 2. 与现有实现的关系和当前缺口

### 2.1 已存在的边界

前一阶段的 RAG 设计和实现计划已经确定：

- profile_agent/knowledge/role_packs/ai_application_engineer_2026_h2.json 是岗位能力维度的权威角色包，包含 role_dim_01 至 role_dim_06；
- profile_agent/schemas/question_rag_schema.py 定义 InterviewQuestionRecord、QuestionRetrievalIntent、RetrievedQuestion、QuestionRetrievalTrace 和 QuestionRetrievalResult；
- profile_agent/services/question_bank_service.py 负责 JSON 读取、字段校验、内容 hash、重复检查和生命周期审计；
- profile_agent/services/question_retrieval_service.py 从 AskAction、目标/证据要求、JD、简历、近期回答和已问 ID 构造确定性 intent，并在最多三个候选中重排；
- profile_agent/knowledge/qdrant_question_store.py 只持有 interview_questions 可重建索引和索引 manifest，不是题库的唯一副本；
- 当前运行链是 supervisor → retrieve_question → generate_question → wait_for_answer，检索失败不阻断整场面试，候选人侧不应看到题库评分信号或内部来源元数据。

### 2.2 本阶段必须正面处理的缺口

以下缺口不能被规格隐含掉：

1. 当前 embedding text 仍主要是 question_text_only，没有把业务约束、skills、维度词和 mode 关系纳入确定性索引文本；
2. 当前 difficulty 会进入 intent/排序，但 Qdrant 硬过滤没有真正按 difficulty 排除记录；本规格不把这个现状误写成已完成的硬门禁；
3. 六个维度乘六个 primary mode 形成 36 个概念格位，而 30 道题不可能全部覆盖。语料必须显式接受缺格，并通过 compatible_modes 和真实的 no_match 处理缺口，不能复制题目或伪造覆盖率。

本规格不擅自引入跨维度检索，也不因为缺少格位而改变 Supervisor 的目标维度。

## 3. 语料范围和配额

### 3.1 固定岗位范围

| 字段 | 固定值 |
| --- | --- |
| role | ai_agent_engineer |
| 展示岗位 | AI Agent 应用工程师（校招/初级） |
| role_version | 2026-H2 |
| 题目总数 | 30 |
| 发布时状态 | 仅 active 题可进入检索 |
| 允许的 active trust | medium、high |

角色包中的维度名称、权重、最低标准和关键错误是题目标签与审核的上游事实。语料库不能新增第七维，也不能把另一岗位的题目混入本版本。

### 3.2 六维配额

题目必须精确命中下表数量，合计 30。配额是发布门禁，不是软目标。

| dimension_id | 角色包维度 | 题数 |
| --- | --- | ---: |
| role_dim_01 | Agent 架构与任务编排 | 6 |
| role_dim_02 | 业务理解与任务建模 | 5 |
| role_dim_03 | Context、RAG、Memory 与工具工程 | 6 |
| role_dim_04 | AI 协作开发与生产交付 | 4 |
| role_dim_05 | 评测、可观测性与安全治理 | 6 |
| role_dim_06 | 成本、性能与持续优化 | 3 |
| **合计** |  | **30** |

### 3.3 primary mode 配额

每道题恰好有一个 primary mode，配额如下，合计 30。表中的“project”是产品语言；现有运行时 QuestionMode 的实际枚举值是 project_deep_dive，二者一一映射，不创建新的运行时枚举。

| 产品标签 | 序列化值 | primary mode 题数 |
| --- | --- | ---: |
| foundation | foundation | 4 |
| project | project_deep_dive | 5 |
| scenario | scenario | 8 |
| system_design | system_design | 4 |
| coding | coding | 3 |
| follow_up | follow_up | 6 |
| **合计** |  | **30** |

primary mode 配额与维度配额是两个独立边际约束；不要求也不假装覆盖 36 个维度×mode 交叉格。coding 题只能落在允许 coding 的维度策略中，不能为填配额将业务建模题改造成无关的代码题。

## 4. 题目语义契约

### 4.1 题目应表达什么

每道题是一条单一、可作答的评估任务，必须让候选人能够基于课程项目、竞赛、开源、实习、可复现实验或真实工作回答，不得要求候选人虚构生产经历。题面应包含一个可辨识的业务目标或工程约束，并保留一个主要回答目标：事实、取舍、失败边界、验证方式或实现思路至少命中其中一项。

题目的原创改写不是把原面经换几个同义词，而是从来源信号抽象工程能力后重新编排情景、约束和追问边界。来源中的原问题、完整答案、姓名、联系方式、账号、公司内部标识和其他个人信息均不进入题面。

### 4.2 规范化记录

下一版题库记录的逻辑字段如下：

| 类别 | 字段 | 规则 |
| --- | --- | --- |
| 身份 | question_id、role、role_version、version | 稳定 ID；只允许本岗位和本版本 |
| 候选人可见语义 | question_text、business_constraint | 原创、可直接作答；不含答案、PII、来源 URL |
| 能力标签 | dimension_id、dimension_terms、skills | 维度来自角色包；词表可审计、顺序规范化 |
| 题型 | primary_mode、compatible_modes | 一个 primary；兼容列表受维度策略约束且不得重复 primary |
| 难度 | difficulty | foundation、intermediate、advanced；用于标注/排序和一致性校验 |
| 内部评分 | expected_signals、critical_errors、follow_up_seeds | 仅供评估和后续追问；不得进入 embedding 或候选人提示 |
| 辅助标签 | company_tags | 只保留泛化后的行业/场景标签，不保留雇主内部机密 |
| 旧契约来源字段 | source_id、source_url、source_title、source_type | 保留以兼容现有 InterviewQuestionRecord；完整多源关系以 sidecar 为准 |
| 生命周期 | published_at、verified_at、valid_until、trust_level、status | active 资格须经来源、权益和时间检查 |
| 身份 hash | content_hash | 由规范化的语义字段确定性计算，作为去重门禁 |

business_constraint 是候选人安全的约束摘要，例如数据新鲜度、权限边界、失败恢复或延迟预算；它不能是隐藏答案。dimension_terms 是从角色包维度名、最低标准和允许词表生成的稳定词集合，不从候选人简历或来源原文直接拷贝。

### 4.3 来源证据不等于题面内容

每道题的来源证据、审核意见和定位信息必须留在离线 sidecar。题库记录中的兼容 source_* 字段只是现有运行时的窄投影，不能代替“近期面经信号 + 官方文档/JD”两类证据的完整关联，也不能促使运行时把 URL 发送给模型。

## 5. 证据、来源和原创性

### 5.1 每题的双重交叉验证门槛

每道题在进入 active 前必须关联至少两类证据：

1. **近期公开面经信号**：来源发布日期或可确认的公开发布时间必须满足 `2025-01-01 <= published_at <= corpus_as_of`；首版 `corpus_as_of=2026-08-27`，不得接受未来日期。内容只记录“考察了什么能力/约束/故障/取舍”的人工摘要，不复制原问题或答案；
2. **技术交叉验证**：至少一条官方技术文档，或一份当前企业 JD。它用来验证题目中的工程事实、能力边界或岗位相关性，不是用来补齐答案。

两条证据应分别登记 source_id。同一 URL 不能靠重复登记同时制造独立性；若一个来源同时提供两类信息，仍需再找到另一条独立 URL 才能满足本门槛。

### 5.2 允许的代表性来源类别

下面只是可优先发现和交叉验证的代表性类别，不是已经完成的来源清单，也不宣称已经采集了 30 道题：

- 官方技术资料：OpenAI、Anthropic、MCP、A2A、Google、Microsoft、AWS、OWASP、NIST 等官方文档或工程文章；
- 当前企业 JD：百度、OPPO 等企业的公开招聘页面；
- 公开面经信号：牛客等平台在 2025—2026 年公开可访问的近期面经页面。

实际发布时，每个 URL 都必须在 QuestionSourceRegistry 中有规范化记录、抓取/查看日期、信任等级和权益结论。搜索结果页、无法定位的转载、需要绕过登录/验证码/付费墙的内容不能作为发布证据。

### 5.3 URL 和来源分布门槛

- 全库至少 12 个独立 canonical URL；独立性按规范化后的 scheme + host + path + 非追踪 query 计数，fragment 和常见追踪参数不制造新 URL；
- 同一 canonical URL 最多支撑 3 道题，按全库所有题的引用总数计，不按维度拆分规避；
- source_id、canonical URL、题目关联关系必须互相一致；来源被撤回或无法复核时，相关题不能继续保持 active；
- active 题的所有关键来源 trust 只能是 medium 或 high。low 可暂存供人工复核，但不得入索引或被检索返回。

### 5.4 版权、隐私和付费内容

离线研究只保存公开页面的必要元数据、人工摘要和定位信息，不保存网页全文、长摘录、原面经答案或付费内容。题目、业务约束、skills 和 dimension terms 必须由审核者原创改写；只保留能够证明“为何选择该能力”的短语义摘要和 hash，不保留可还原原文的连续片段。

发布前必须通过 PII 扫描和人工复核，移除姓名、电话、邮箱、社交账号、简历编号、内部链接、客户数据和可识别的个人经历细节。不能仅依靠正则扫描代替人工判断。任何版权权益不清、来源需要付费访问、或改写仍与原文高度近似的记录都退回，不进入 active。

## 6. primary/compatible mode 策略

### 6.1 受控维度策略

策略是可版本化的 allowlist，不由模型临时决定。每个维度保存 allowed_primary_modes、allowed_compatible_modes 和 preferred_order。compatible_modes 必须是该维度策略的子集，去重后按固定顺序序列化；不允许把一个维度的 mode 当作另一个维度的替代。

本版本固定的最小策略如下：

| 维度 | 允许的 primary/compatible mode | preferred order（从高到低） |
| --- | --- | --- |
| role_dim_01 架构与编排 | foundation、project_deep_dive、scenario、system_design、follow_up | system_design、scenario、project_deep_dive、foundation、follow_up |
| role_dim_02 业务理解与建模 | foundation、project_deep_dive、scenario、system_design、follow_up | scenario、project_deep_dive、system_design、foundation、follow_up |
| role_dim_03 Context/RAG/Memory/工具 | 六种 mode 全部允许 | scenario、system_design、coding、foundation、project_deep_dive、follow_up |
| role_dim_04 协作开发与交付 | 六种 mode 全部允许 | project_deep_dive、coding、scenario、system_design、foundation、follow_up |
| role_dim_05 评测/可观测性/安全 | 六种 mode 全部允许 | scenario、system_design、coding、foundation、project_deep_dive、follow_up |
| role_dim_06 成本/性能/优化 | 六种 mode 全部允许 | scenario、system_design、coding、project_deep_dive、foundation、follow_up |

“六种 mode 全部允许”仍受题目单一 primary、全库配额和证据适配性约束；它不是要求每个维度覆盖所有 mode。若未来改变策略，必须升级 mode_policy_version、重跑 30 个 intent 评测并重建索引。

### 6.2 检索降级顺序

给定 Supervisor 的目标维度和请求 mode，检索器必须执行以下确定性流程：

1. 只在相同 role、相同 role_version、相同 dimension_id、status=active、valid_until >= today 且未被排除的题中查找；
2. 先保留 primary_mode == requested_mode 的 exact 集合；
3. exact 集合为空时，才保留 requested_mode ∈ compatible_modes 的 compatible 集合；
4. 两个集合都为空时返回 status=no_match，交由现有无 RAG 出题路径继续，不制造伪命中；
5. exact 与 compatible 不混排；如果 exact 有结果，compatible 题不能抢到 exact 之前；
6. 任何阶段都不改变 dimension_id，也不把相邻维度的高相似题作为兜底。

同一题的兼容关系是有向声明：题目 A 声明可用于 foundation，不代表 foundation 题自动可用于 A 的 primary mode。follow_up 只有在该题的业务约束能支持近期回答缺口时才可声明为 compatible，不能用作无条件通配符。

## 7. 确定性 embedding 和索引契约

### 7.1 唯一 embedding text

建库文本按固定顺序、固定字段名、固定空白规范化拼接：

~~~text
question=<question_text>
business_constraint=<business_constraint>
skills=<skills sorted and normalized>
dimension_terms=<dimension_terms sorted and normalized>
primary_mode=<primary_mode>
compatible_modes=<compatible_modes sorted by mode policy>
~~~

这六部分是本版本唯一允许进入题目向量的语义来源。构造函数必须是纯函数，同一条记录和同一 embedding_text_version 产生完全相同的输入字符串。不得把 difficulty、expected_signals、critical_errors、follow_up_seeds、source_url、来源标题、公司标签、问题 ID、索引分数、候选人 JD/简历、近期回答或任何 PII 拼进去。

题目 vector 和 30 条标注 query 都必须经过相同的字段 allowlist 和 PII/URL 清理边界。运行时个性化文本只用于生成最终候选人问题，不回写题目向量，也不触发重新建库。

### 7.2 索引和重建

Qdrant 仍只承担可重建索引职责。索引 manifest 的指纹至少包含 provider、model、vector dimension、embedding_text_version、题库 manifest hash 和 mode_policy_version。任一项变化都禁止新旧向量混用，必须显式重建。

payload 可以保存内部审核和评分所需的最小字段，但 embedding text 和候选人 prompt 必须执行不同的投影：

- embedding 投影只含本节六段语义；
- Question Generator 只接收 question_text、business_constraint、skills 和必要的 mode/维度上下文；
- expected_signals、critical_errors、follow_up_seeds、来源 URL、source ID、score、index version 不得作为候选人可见提示或答案暗示。

## 8. 最小治理 sidecars

sidecar 是与题目 JSON 一同版本化的审核事实，不是运行时可见内容。所有 sidecar 都必须拒绝未知字段、重复主键和无法关联的 ID。

### 8.1 QuestionBankManifest

文件记录本次发布快照的全局门禁：

- schema_version、bank_id、role、role_version、manifest_version；
- question_count=30、有序 question_ids；
- 六维配额、primary mode 配额、mode_policy_version；
- min_independent_urls=12、max_questions_per_url=3；
- corpus_as_of=2026-08-27、required_signal_from=2025-01-01、dynamic_review_days=180、evergreen_review_days=365；所有来源日期必须不晚于 corpus_as_of；
- active_count、active_trust_levels、生成/复核日期、发布状态；
- 题目集合 hash、sidecar 集合 hash、embedding contract version。

Manifest 的数量和配额必须由校验器重新计算，不能只相信手填数字。question_count 不得用“至少 30”代替，目标发布快照必须恰好 30 道。

### 8.2 QuestionSourceRegistry

每个 source_id 一条记录，至少包括：

- source_id、canonical URL、publisher、title、source class；
- published_at；若官方文档或 JD 页面无明确发布日期，可使用 retrieved_at 并显式标记 date_basis=retrieved_at，但公开面经信号必须有可确认且不晚于 corpus_as_of 的公开时间；
- role_level、支持的维度、source trust、当前可访问状态；
- review_class：dynamic 或 evergreen；
- 权益状态、是否允许人工摘要、最后验证日期和下一次复核日期；
- 不含网页全文、不含原答案、不含个人信息的备注。

source class 只允许公开面试经验、官方技术文档/工程文章、当前企业 JD 三类；测试 fixture 必须明确标记为 test-only，不能进入发布 manifest。

### 8.3 review.json

每道题一条审核记录，至少关联：

- question_id、审核状态、审核者/复核者标识和时间；
- signal_source_ids：至少一条 2025—2026 公开面经信号；
- cross_validation_source_ids：至少一条官方文档或当前企业 JD，且与 signal 来源 URL 独立；
- 对能力信号、业务约束、角色包维度、primary/compatible mode 的人工判断摘要；
- 原创改写确认、PII 扫描结果、版权/权益结论、难度一致性结论；
- review_class、review_due_at、退回/停用理由（若有）。

摘要只描述判断，不粘贴来源连续句子。没有完整双重证据或任一安全检查未通过，状态不能变为 approved/active。

### 8.4 dedupe.json

记录 question_id、规范化语义 hash、比较批次、候选重复组、相似/重复判定、处理决定和审核时间。去重同时检查精确 content_hash 和人工语义近重复；任何 active 记录不得共享 content hash，也不得存在未裁决的近重复组。

### 8.5 rights.json

每个题目—来源关联记录允许的公开访问依据、人工改写范围、是否含原文/答案/PII/付费内容、审核结论和时间。只允许 public_access + paraphrase_only + no_pii + no_paid_content 的组合进入 active；权益不明必须退回，不以“来源可搜索”作为授权。

### 8.6 locator.json

记录 question_id、source_id、canonical URL、公开页面中的章节/标题/日期/页码/时间段等最小定位信息、查看日期和定位 hash。locator 不保存原文摘录；失效或无法复核的 locator 触发生命周期审计。

## 9. 生命周期和人工发布

### 9.1 复核窗口

- dynamic：公开面经信号、当前 JD 或快速变化的工程页面，next_review_at = verified_at + 180 days；
- evergreen：变化较慢的官方协议/概念文档，next_review_at = verified_at + 365 days，但若官方页面标记版本变化，提前复核；
- 每道题的 valid_until 取其必需来源和权益约束中最早的有效截止日。由于每题都必须有近期面经信号，题目发布后的复核上限受 180 天窗口约束；官方文档仍按 365 天保存自己的来源复核记录。

到达复核日、来源撤回、权益变化、岗位版本变化或题面/标签 hash 变化时，题目转为 needs_review，立即排除出运行时索引，直到重新审核。确认不再适用或不可合法使用时转为 retired；不物理删除，以保留历史报告的审计依据。

### 9.2 人工角色和发布顺序

1. 研究者只收集公开信号和元数据，生成原创情景草稿；
2. 题库审核者逐题核对角色包维度、mode policy、两类来源、原创性、PII、版权、locator、难度和去重；
3. 发布复核者根据 Manifest 重新计算数量、配额、来源分布、生命周期和安全扫描，签署发布快照；
4. 只有发布快照通过离线校验后才可进入 zero-cost dry-run；模型建库仍需额外的用户批准边界。

搜索结果或模型自动生成的题目不能绕过人工审核直接写入 active JSON。人工审核可以拒绝来源，不要求为了满足配额而降低证据或权益标准。

## 10. 数据流和运行时边界

~~~text
公开 URL（离线发现/人工查看）
        ↓
QuestionSourceRegistry + locator + rights
        ↓
面经信号摘要 + 官方文档/JD 交叉验证
        ↓
原创情景化题面 → review + dedupe → QuestionBankManifest
        ↓
确定性六段 embedding text
        ↓
fake embedding + local Qdrant dry-run
        ↓（用户再次批准后）
BGE-M3 一次性题库建库 + 30 条 query embedding
        ↓
Supervisor AskAction + 运行时事实
        ↓
同维度 exact → compatible → no_match
        ↓
Question Generator 轻量个性化 → candidate-facing question
~~~

运行时只读取已发布题目、role pack 和安全的检索 intent，不访问公开 URL、不重新解释来源、不将简历/回答原文写回题库。个性化只能替换无关紧要的场景名词或补充已存在的业务约束；不得改变能力维度、主要回答目标、primary/compatible mode、难度、评分标准或来源事实。个性化后仍只生成一个主要问题，并沿用原题的 question_id、source trace 和索引版本供内部审计。

候选人可见响应只含最终问题。source URL、source ID、content hash、trust、score、index version、expected signals、critical errors、follow-up seeds 和 sidecar 内容保持私有；公共 API 继续使用现有的题目/回答投影。

## 11. 运行时 schema 兼容和迁移

### 11.1 兼容原则

- 保留现有 question_mode 字段及六个 QuestionMode wire 值；不把 project_deep_dive 改名为 project；
- 新题库 v2 使用逻辑字段 primary_mode 和 compatible_modes。加载器将 primary_mode 投影到既有 question_mode，并在内部提供只读 primary_mode 访问；旧 v1 记录只有 question_mode 时，视为 primary mode，compatible_modes=[]；
- compatible_modes、business_constraint、dimension_terms 都是 additive 字段，旧调用方不提供时使用空列表/安全默认值；不移除当前必需字段，不改变 QuestionRetrievalResult 的 hit/no_match/unavailable/index_mismatch 语义；
- QuestionRetrievalIntent.question_mode 继续表示 Supervisor 请求的 mode，新增的 mode policy 和匹配 tier 不要求调用方重写现有 intent 构造；
- InterviewTurn.question_mode、retrieval_trace 私有字段和 QuestionGenerator 的现有降级调用保持可读旧 checkpoint 的能力；sidecar 不序列化到候选人报告。

### 11.2 兼容读取测试

必须有旧 v1 fixture 和新 v2 fixture 的双向边界测试：旧题能被加载、只参与 exact；新题能被旧字段消费者读取为 question_mode；缺少可选 additive 字段不会破坏旧报告/面试；未知 mode、未知维度、错误映射、重复兼容项和跨维度 policy 均 fail closed。

difficulty 在本规格仍是显式标注和一致性约束，不能被测试误报为已完成的 Qdrant hard filter。若未来要把它提升为硬过滤条件，必须另行变更 intent/store 契约并重建评测基线。

## 12. 错误处理和降级

### 12.1 离线发布错误

以下任一条件失败，构建停止且不写入可用索引：

- 题数不是 30，六维或 primary mode 配额不精确；
- 任一题缺少双重证据、2025—2026 面经信号、官方文档/JD 交叉验证、locator 或权益决定；
- 独立 URL 少于 12，或某 URL 支撑题数超过 3；
- active 记录 trust 为 low、来源/role/version/status/日期非法、重复 ID/hash 或未裁决近重复；
- PII、原文/答案相似复制、付费内容、来源失效或 sidecar 外键不一致；
- mode policy、维度、primary/compatible 关系或 embedding allowlist 校验失败。

校验必须在 Qdrant 写入前完成；不能用部分成功的集合伪装为 30 道发布题库。

### 12.2 运行时错误

- 没有 exact 或 compatible 题：返回真实 no_match，调用既有 Question Generator；不跨维度、不把未审核题临时拼入 prompt；
- embedding 服务缺少配置、超时、429/5xx：执行有界重试后返回 unavailable，日志只记录错误类型和阶段，不记录密钥、请求头、完整输入或响应正文；
- Qdrant 不可用、collection 缺失或 manifest 指纹不一致：返回 unavailable 或 index_mismatch，不混用旧/新 vector；
- 返回记录不满足 active、日期、角色、维度、mode policy 或排除 ID：丢弃该记录；若所有候选均无效，返回 no_match 或 unavailable，不提升为命中；
- Question Generator 失败：遵循现有生成错误/降级语义，永不把 expected signals、critical errors、来源信息或内部分数泄露给候选人。

检索 trace 必须诚实反映 hit、no_match、unavailable 或 index_mismatch。命中时 trace 中的 question/source/index/score/tier 必须与返回记录一致；非命中状态不得携带选中题目 ID。

## 13. 30 条 intent 评测集

评测集与题库一同版本化，恰好包含 30 条 labeled intents，每条至少对应一条题目：

~~~text
intent_id
role
role_version
dimension_id
requested_mode
query_text
gold_question_id
acceptable_question_ids
hard_negative_ids
label_notes
~~~

- gold_question_id 是该 intent 的首选题，30 条 gold 覆盖 30 道题；
- acceptable_question_ids 只能包含同一维度、满足 exact 或受控 compatible policy 的题，作为兼容模式的附加召回诊断，不可包含跨维度题；
- hard_negative_ids 至少覆盖相同关键词但错误维度、错误 mode、过期/retired、重复语义、错误岗位或低信任题，且这些题不得在该 intent 的 top 3 中出现；
- query_text 使用与运行时相同的确定性事实投影，不包含来源 URL、PII、答案信号或评分错误；
- intent、gold、acceptable、hard negative 的外键、维度和 mode 关系由校验器复算，不接受只写自然语言的标注。

### 13.1 验收指标

使用 deterministic fake embedding 和 local Qdrant 先完成离线指标，再进行唯一一次 BGE-M3 运行。发布门槛为：

- gold Recall@3 >= 0.90；
- gold MRR@3 >= 0.90；
- 每个维度的 Recall@3 >= 0.80；
- hard negative 命中数为 0；
- invalid record、duplicate ID、duplicate content hash、duplicate returned question 均为 0；
- 100% retrieval trace 一致：状态、题目 ID、source ID、score、index version、匹配 tier 与实际返回结果一致；
- active URL/信任/生命周期/配额门禁 100% 通过。

Recall 的主统计使用 gold，acceptable recall 作为兼容模式的附加报告；不能通过把 acceptable 扩大到跨维度或未审核题来提高主指标。失败后应修复题面、标签、来源或检索契约并重新获得批准，不启动循环自动调参，也不改变测试集迎合分数。

## 14. zero-cost dry-run 和用户批准边界

### 14.1 无外部模型阶段

先对最终的 30 条候选发布快照（以及早期仅用于形状测试的明确 test-only fixture）执行：

1. schema、Manifest、配额、source URL 分布、双重证据、rights、PII、locator、dedupe 和生命周期审计；
2. embedding allowlist 断言，验证生成文本恰好包含六段字段且不含 signals/errors/followups/source URL/PII；
3. deterministic fake embedding、local Qdrant rebuild/search、exact→compatible→no_match 和错误降级；
4. 30 labeled intents、gold/acceptable/hard negatives、指标和 trace 一致性；
5. 旧 v1/new v2 schema、公共报告投影、密钥/来源正文扫描和 git diff --check。

该阶段不得调用硅基流动、BGE-M3、外部网页或付费服务。dry-run 通过只意味着契约和流程通过，不意味着已经取得真实来源或完成 30 道题采集。

### 14.2 唯一一次真实 embedding 边界

全部 dry-run 通过后，必须暂停并再次获得用户明确批准，才能执行一次 BGE-M3 建库：

- 30 道题各生成 1 条题库 embedding text；
- 30 条 labeled intents 各生成 1 条 query embedding；
- 合计约 60 条输入，固定使用 BAAI/bge-m3 和已审核的 index fingerprint；
- 只建立一次对应发布快照并运行评测，不循环自动重试、不自动调整切分/配额/权重、不连续试验其他模型；
- 若服务失败或指标未达标，停止并报告真实状态，保留 fake/local 结果，不自动再次消耗模型调用额度。

用户批准只覆盖这一次约 60 输入的建库与评测，不等于批准联网爬取、付费墙访问、自动发布或其他岗位扩展。

## 15. 文件布局

本规格文件位于：

~~~text
docs/superpowers/specs/2026-08-27-current-ai-agent-question-corpus-design.md
~~~

后续实现应保持权威资料与生成索引分离，建议布局如下：

~~~text
profile_agent/knowledge/role_packs/
  ai_application_engineer_2026_h2.json          # 既有角色包，维度权威
  ai_application_engineer_2026_h2_sources.json  # 角色包来源，不替代题目来源

profile_agent/knowledge/question_banks/ai_agent_engineer_2026_h2/
  questions.json                  # 30 条原创题目，Git 权威源
  QuestionBankManifest.json       # 全局数量、配额、版本、索引契约
  QuestionSourceRegistry.json     # 来源元数据和生命周期
  review.json                     # 双重证据与人工审核
  dedupe.json                     # hash/语义去重决策
  rights.json                     # 公开访问、改写、版权和 PII 决策
  locator.json                    # URL 页面定位 hash
  retrieval_intents.jsonl         # 30 条 gold/acceptable/hard-negative 标注

artifacts/question_corpus/        # 生成物，可删除后重建，不是权威题库
  fake-eval-report.json
  qdrant/                          # local dry-run 或经批准的索引
~~~

文件名和目录可以在实现时保持既有项目约定，但必须一一对应上述责任；不能把来源全文、API 密钥、BGE 向量或个人信息放回 Git 权威题库。

## 16. 测试矩阵

实现验收至少覆盖以下测试，不以单个 happy path 代替：

| 测试组 | 必须证明 |
| --- | --- |
| schema/迁移 | v1 question_mode 仍可读；v2 primary/compatible 正确投影；未知字段、mode、维度和非法日期 fail closed |
| 配额/Manifest | 总数 30；六维 6/5/6/4/6/3；primary mode 4/5/8/4/3/6；policy hash 和题目 hash 一致 |
| 来源治理 | 每题一条 2025—2026 公开面经信号和一条官方文档/JD；至少 12 独立 URL；每 URL ≤3；active 无 low trust |
| 原创/安全 | embedding text 仅六段 allowlist；不存在原文/答案/PII/付费内容；sidecar 不含网页全文和秘密 |
| 生命周期 | dynamic 180 天、evergreen 365 天；到期转 needs_review 并从索引排除；retired 不物理删除 |
| mode policy | exact 优先，空 exact 才 compatible，再空才 no_match；兼容项不得跨维度；缺格不伪造结果 |
| store/retrieval | role/dimension/status/date/排除 ID 硬过滤；index fingerprint 不一致拒绝；difficulty 现状不会被误报为硬过滤 |
| 评测 | 30 labeled intents、gold/acceptable/hard negatives；Recall/MRR/分维度门槛；invalid/duplicate=0；trace 100% 一致 |
| 降级/日志 | fake/local、Qdrant 不可用、embedding 失败、无命中和生成失败均保留真实状态；错误和日志无密钥/PII/完整来源输入 |
| 集成/公共边界 | 命中题可安全交给 Generator 轻量改写；候选人看不到 source/rubric/score；旧 checkpoint 和报告投影不破坏 |
| 调用预算 | dry-run 零外部模型/网页调用；批准后的真实阶段最多 30 条题向量 + 30 条 query 向量，不自动调参或循环重建 |

## 17. 完成边界

本语料项目只有在以下条件全部满足时才可称为完成：

1. Git 权威题库恰好有 30 道原创题，严格满足六维和 primary mode 配额；
2. 每题有合格的 2025—2026 公开面经信号、官方文档/JD 交叉验证、定位、rights、review、dedupe 记录；全库至少 12 个独立 URL，单 URL 不超过 3 题，active trust 只有 medium/high；
3. QuestionBankManifest、QuestionSourceRegistry、review/dedupe/rights/locator sidecars 可独立校验，过期/撤回/重复/版权/PII 记录均能阻止发布；
4. deterministic fake/local Qdrant dry-run 全部通过，30 条 intent 的 gold/acceptable/hard-negative 标注、指标、逐维度召回和 trace 门禁通过；
5. 用户再次批准后唯一一次 BGE-M3 建库和约 60 输入评测完成，索引 fingerprint、精确/兼容/无命中路径和运行时降级均通过；
6. 旧 runtime schema、公共报告和已有无 RAG 路径保持兼容，候选人侧没有来源、答案提示、PII 或内部评分泄露。

本规格文件本身不表示上述 30 道题、来源 URL、评测结果或 BGE 索引已经存在；它只冻结后续采集、审核、实现和验收必须遵守的边界。

## 18. 非目标

- 扩展 Java、算法、产品经理或其他岗位；
- 在面试运行时联网搜索、抓取、绕过登录/验证码/付费墙或动态写入题库；
- 复制第三方网页、面经原题、完整答案、个人信息或任何受版权保护的大段内容；
- 用模型自动审核并直接发布 active 题，或用自动爬虫替代人工权益判断；
- 每月自动调度、后台审核 UI、全自动来源发现或来源正文数据库；
- 引入 reranker、多 collection、多岗位抽象或为缺失格位复制题目；
- 把 difficulty 当前未硬过滤的现状悄悄改成另一个未评审的公共契约；
- 改写 Supervisor 的维度决策、评分公式、报告结论或候选人证据规则；
- 通过循环自动调参、反复调用 BGE-M3 或改变 gold 标注来追逐指标；
- 把“代表性来源类别”误报为已经完成的 30 道题采集清单。

## 19. 规格自查

- 数量核算无冲突：六维 6+5+6+4+6+3=30，primary mode 4+5+8+4+3+6=30；
- “project”与现有 project_deep_dive 的映射已固定，未引入第二套题型枚举；
- 30 题不覆盖 36 格的现实已通过维度内 mode policy、compatible 和真实 no_match 明确处理；
- 证据、来源、版权、PII、生命周期、运行时数据流、文件责任、测试和批准边界均有独立条款；
- 代表性来源仅用于说明允许的来源类别，没有声称真实采集已完成；
- 没有把当前 difficulty、embedding 或 schema 缺口写成已经解决的事实，也没有用跨维度 fallback 掩盖缺口；
- 运行时降级、人工审核、索引重建和候选人可见/不可见字段均保持单向、可审计边界。


