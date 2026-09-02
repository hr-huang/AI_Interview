# 最新真实面试题 RAG 基础设计

## 1. 目标

为当前唯一岗位“AI Agent 应用工程师（校招/初级）”建立可追溯、可过期、可重建的真实面试题检索能力。Supervisor 仍只决定本轮“考什么、用什么题型”；RAG 根据这个决定、JD、简历和已有证据缺口，找到最匹配的真实最新题目，再交给 Question Generator 进行有边界的个性化改写。

本设计只覆盖 RAG 基础设施和运行时检索契约。真实题目的大规模联网搜索与审核、每月自动更新调度分别作为后续阶段。

## 2. 核心概念：面试题记录

数据库中的最小单元是一条“面试题记录”：

```text
面试题记录 = 题目 + 标签 + 评分信号 + 来源 + 时间
```

候选人只看到最终题目。其他字段仅供检索、评分、审核和追溯使用。

### 2.1 必需字段

```text
question_id
question_text
role
role_version
dimension_id
skills
question_mode
difficulty
expected_signals
critical_errors
follow_up_seeds
company_tags
source_id
source_url
source_title
source_type
published_at
verified_at
valid_until
trust_level
status
version
content_hash
```

### 2.2 枚举与约束

- `role` 第一版只允许 `ai_agent_engineer`。
- `question_mode` 必须复用现有 `QuestionMode`。
- `difficulty` 只允许 `foundation`、`intermediate`、`advanced`。
- `trust_level` 只允许 `high`、`medium`、`low`。
- `status` 只允许 `active`、`needs_review`、`retired`。
- `question_text`、`skills`、`expected_signals`、`source_url` 不得为空。
- `valid_until` 不得早于 `verified_at`。
- `content_hash` 由规范化后的题目正文与核心标签确定性生成，用于防止重复入库。
- 只有 `active` 且 `valid_until >= 当前日期` 的记录可参与面试检索。

## 3. 数据所有权与存储

### 3.1 权威数据源

经过审核的面试题记录以 Git 可版本化的 JSON 文件作为权威数据源。审核、更新、停用和恢复都先修改 JSON，再重建或增量更新向量索引。

### 3.2 Qdrant 的职责

Qdrant 只是可重建的检索索引，不是题库的唯一副本。第一版只创建一个 Collection：

```text
interview_questions
```

每个 Qdrant point 对应一条面试题记录：

- vector：由题目正文、业务情景、考察能力和出题意图组合文本生成。
- payload：保存岗位、维度、题型、难度、来源、时间、状态、版本和评分信号。

Qdrant 数据丢失时，必须能仅根据 JSON 题库和当前 Embedding 配置完整重建。

## 4. Embedding

提供方使用硅基流动，默认模型：

```text
BAAI/bge-m3
```

调用官方 OpenAI 兼容接口：

```text
POST https://api.siliconflow.cn/v1/embeddings
```

配置只能从环境变量读取：

```text
SILICONFLOW_API_KEY
SILICONFLOW_EMBEDDING_MODEL=BAAI/bge-m3
SILICONFLOW_EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
```

密钥不得出现在源码、JSON 题库、测试 fixture、错误文本、日志或 Git 历史中。测试使用确定性假 Embedding 实现，不访问外网。

Embedding 配置需要持久化一个索引指纹，至少包含 provider、model 和 vector dimension。指纹变化时禁止将新旧向量混用，必须重建 Collection。

## 5. 运行时检索

### 5.1 Supervisor 边界

现有 Supervisor 保持确定性，仍输出：

```text
target_id
primary_requirement_id
question_mode
reason
```

Supervisor 不直接访问 Qdrant，也不自由生成检索关键词。

### 5.2 RetrievalIntentBuilder

新增确定性的 `QuestionRetrievalIntentBuilder`，读取：

- Supervisor 的 `AskAction`；
- 对应 Target objective 和 Evidence Requirement；
- 本轮目标维度；
- 已有 evidence summaries 和最近回答；
- JD 重点和简历项目摘要；
- 已问 `question_id` 集合。

它输出：

```text
query_text
role
dimension_id
question_mode
difficulty
excluded_question_ids
```

`query_text` 只由已有事实组合，不再调用一次大模型。

### 5.3 检索步骤

1. 硬过滤 `role`、`dimension_id`、`question_mode`、`status=active`、`valid_until >= today`。
2. 排除已问题目。
3. 使用 BGE-M3 对 `query_text` 生成向量。
4. 从硬过滤结果中检索 Top 3。
5. 根据向量相似度、来源可信度、新鲜度和重复度进行确定性重排。
6. 选择第一条作为 Question Generator 的出题依据。

如果没有可用结果，第一版使用现有 Question Generator 路径降级，但必须记录 `retrieval_status=no_match`，不得伪装成题库命中。

## 6. Question Generator 集成

Question Generator 接收最多一条选中的面试题记录，并遵守：

- 保留原题的主要能力目标和业务约束。
- 只根据简历、JD 和已有对话做必要改写。
- 不得将 `expected_signals`、`critical_errors` 或标准答案泄露给候选人。
- 改写后的问题仍只能有一个主要回答目标。
- 必须在运行时记录 `question_id`、`source_id`、检索得分和索引版本，但不向候选人展示。

`follow_up` 模式优先追问最近回答的未验证缺口。可使用选中题目的 `follow_up_seeds`，但不得机械重复原问题。

## 7. 生命周期和命令

第一版提供人工执行的命令：

```text
validate  校验 Schema、时间、来源、重复和岗位范围
rebuild   根据全部 active JSON 重建 Qdrant Collection
sync      根据 content_hash 增量 upsert，并将已停用记录从可检索集合中移除
audit     输出过期、即将过期、来源缺失和疑似重复清单
```

命令默认只读。`rebuild` 和 `sync` 必须显式指定写入参数，并在写入前完成全量校验。

过期题目不物理删除，转为 `needs_review` 或 `retired`，以保留旧面试报告的审计依据。

## 8. 错误处理

- 缺少 `SILICONFLOW_API_KEY`：管理命令明确报错；实时面试可降级为无 RAG 的现有出题路径。
- Embedding 超时、429 或 5xx：有界重试，不在日志中输出密钥或完整请求。
- Qdrant 不可用：记录检索失败状态并降级，不阻断整场面试。
- 索引指纹不一致：禁止检索并要求重建，不混用新旧向量。
- 题目过期或非 active：即使向量存在也不返回。

## 9. 测试和验收

### 9.1 单元测试

- Schema 拒绝空来源、无效日期、非法岗位和非法状态。
- content hash 在空白与换行差异下保持稳定，在题目语义字段改变时变化。
- RetrievalIntentBuilder 只使用允许的运行时事实。
- 过期、停用、岗位或维度不符的题目不可召回。
- 已问 `question_id` 不可重复召回。
- 没有命中或外部服务失败时保持旧出题路径。
- 日志与错误不包含 API key。

### 9.2 集成测试

- 使用确定性假 Embedding 和本地 Qdrant 建立小型题库，验证完整的导入和检索路径。
- 同一 Supervisor 出题意图多次运行产生相同的排序。
- 题库命中时 Question Generator 收到题目依据，且不向候选人泄露评分信号。
- 题库失效时完整面试图仍可继续运行。

### 9.3 后续检索评测

基础设施完成后，单独建立真实题库和至少 30 条检索标注，评估 Recall@3、无关题召回率、过期题召回率和重复题召回率。该评测不属于本阶段代码验收条件。

## 10. 非目标

本阶段不包含：

- 扩展 Java、算法或产品经理岗位。
- 实时面试中直接联网搜索。
- 自动绕过登录、验证码或付费墙。
- 未审核搜索结果自动发布入库。
- 每月自动调度、审核网页或后台管理界面。
- 第一版引入 Reranker、多 Collection 或多岗位抽象。
- 将完整网页、第三方题库或大段受版权保护内容复制进仓库。

## 11. 分期

1. 本规格：RAG 基础设施与运行时检索契约。
2. 下一规格：基于当期联网搜索的 AI Agent 岗位真实题库。
3. 后续规格：月度来源发现、过期审计与人工发布工作流。
