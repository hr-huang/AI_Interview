# AI Agent 岗位面试场景模块 RAG 设计

## 1. 背景与决策

当前题库包含 30 道完整面试题，混合了 `foundation`、`project_deep_dive`、`follow_up`、`scenario`、`system_design` 和 `coding` 等不同职责的内容。现行路由只让 `scenario`、`system_design` 和 `coding` 进入 RAG，因此 30 道题中只有 15 道会进入真实检索。同时，完整问句往往同时携带大量约束和评分信号，容易产生三个问题：

1. 检索单元同时包含多个能力语义，重排容易跑题；
2. 第一问暴露过多约束与评分点，不像真人面试；
3. Question Generator 如果可以自由改写检索结果，会越界参与面试路径决策。

本设计将 RAG 从“完整面试题库”改为“企业业务场景模块库”：

- JSON 场景库保存完整、已审核的业务场景和能力模块；
- Qdrant 只索引“场景 × 能力模块”检索单元；
- Supervisor 决定当前验证什么；
- 确定性 Constraint Selector 决定当前释放哪一个场景约束；
- Question Generator 只负责将锁定的信息表达成自然问题。

## 2. 范围与非目标

### 2.1 第一版范围

- 只支持 `ai_agent_engineer` 岗位；
- 建设 6 个代表性企业场景；
- 覆盖现有 6 个正式雷达维度；
- 每个雷达维度至少有 2 个场景入口；
- `scenario`、`system_design` 和 `coding` 可以进入场景 RAG；
- `foundation`、`project_deep_dive` 和 `follow_up` 继续绕过 RAG；
- 使用版本化 JSON 作为事实来源，使用现有 Qdrant 作为派生检索索引。

### 2.2 非目标

- 第一版不引入 PostgreSQL；
- 不为每个岗位穷举大量场景；
- 不让 LLM 决定检索过滤、约束选择或 fallback；
- 不将完整场景卡全部拼接成一个向量；
- 不把评分信号和未释放约束直接暴露给候选人。

## 3. 六个场景与能力覆盖

| 场景 | 主要覆盖能力 |
|---|---|
| 电商智能客服 | Agent 架构、Context/Memory/工具、安全评测、性能 |
| 旅行推荐 Agent | 业务建模、Agent 架构、Context/工具、成本优化 |
| 企业成本监控 Agent | 业务建模、生产交付、可观测性、成本性能 |
| 企业知识助手 | Context/RAG/Memory、生产交付、安全评测 |
| 营销内容运营 Agent | 业务建模、AI 协作交付、安全评测、成本优化 |
| 招聘与面试 Agent | Agent 架构、Context/Memory、安全评测、业务建模 |

反向覆盖要求：

| 正式雷达能力 | 至少两个场景入口 |
|---|---|
| `role_dim_01` Agent 架构与任务编排 | 电商客服、旅行推荐、招聘面试 |
| `role_dim_02` 业务理解与任务建模 | 旅行推荐、成本监控、营销运营、招聘面试 |
| `role_dim_03` Context、RAG、Memory 与工具工程 | 电商客服、旅行推荐、知识助手、招聘面试 |
| `role_dim_04` AI 协作开发与生产交付 | 成本监控、知识助手、营销运营 |
| `role_dim_05` 评测、可观测性与安全治理 | 电商客服、成本监控、知识助手、营销运营、招聘面试 |
| `role_dim_06` 成本、性能与持续优化 | 电商客服、旅行推荐、成本监控、营销运营 |

每个场景只建立真正适用的能力模块，预计总共形成约 20 个检索单元，而不是 6 个大向量。

## 4. 完整场景库模型

### 4.1 ScenarioCard

```json
{
  "scenario_id": "ecommerce_service",
  "title": "电商智能客服",
  "role": "ai_agent_engineer",
  "business_goal": "支持商品咨询、订单查询、退款和人工转接",
  "actors": ["消费者", "客服人员", "订单系统"],
  "tools": ["商品搜索", "订单查询", "退款接口", "人工工单"],
  "base_constraints": ["用户隐私", "接口可能超时", "售后政策会更新", "大促流量波动"],
  "modules": ["ecommerce_agent_architecture", "ecommerce_memory_tools"],
  "source_ids": ["source_01"],
  "status": "active",
  "valid_from": "2026-08-29",
  "valid_until": "2027-02-28",
  "version": 1
}
```

### 4.2 ScenarioModule

```json
{
  "module_id": "ecommerce_agent_architecture",
  "scenario_id": "ecommerce_service",
  "primary_dimension_id": "role_dim_01",
  "supported_requirement_types": ["system_design", "problem_solving"],
  "supported_modes": ["system_design", "scenario"],
  "difficulty": ["foundation", "intermediate"],
  "opening_goal": "验证整体组件划分和任务路由",
  "evidence_signals": ["任务拆分", "路由逻辑", "工具边界", "人工接管"],
  "critical_errors": ["让模型无约束直接退款", "忽略状态与幂等"],
  "constraint_ids": ["refund_timeout_after_success", "policy_index_stale"],
  "default_for_dimension": true,
  "status": "active",
  "valid_from": "2026-08-29",
  "valid_until": "2027-02-28"
}
```

### 4.3 ScenarioConstraint

```json
{
  "constraint_id": "refund_timeout_after_success",
  "scenario_id": "ecommerce_service",
  "module_id": "ecommerce_agent_architecture",
  "evidence_gap_tags": ["幂等", "失败恢复", "高风险工具"],
  "difficulty": "intermediate",
  "fact": "退款实际已经执行成功，但接口响应超时",
  "expected_signals": ["幂等键", "状态查询", "人工接管边界"],
  "status": "active"
}
```

## 5. Qdrant 检索单元

完整场景卡不整体向量化。每个 `ScenarioModule` 生成一条派生检索单元：

```json
{
  "retrieval_unit_id": "ecommerce_service::agent_architecture",
  "scenario_id": "ecommerce_service",
  "module_id": "ecommerce_agent_architecture",
  "role": "ai_agent_engineer",
  "primary_dimension_id": "role_dim_01",
  "supported_requirement_types": ["system_design", "problem_solving"],
  "supported_modes": ["system_design", "scenario"],
  "difficulty": ["foundation", "intermediate"],
  "status": "active",
  "valid_until": "2027-02-28",
  "semantic_text": "电商智能客服，整体组件划分，任务路由，工具边界，人工接管"
}
```

只对 `semantic_text` 生成向量。其他字段用于硬过滤、精确校验、定位完整 JSON 记录和审计。Qdrant 是派生索引，不是事实来源；索引可以从 JSON 完整重建。

## 6. Supervisor 决策与检索请求

### 6.1 Supervisor 决策

Supervisor 保持“决定当前验证什么”的职责。现有 `AskAction` 中的 `target_id`、`primary_requirement_id`、`question_mode` 继续作为主要决策字段，运行时还需明确：

```json
{
  "target_id": "target_01",
  "primary_requirement_id": "req_01",
  "primary_dimension_id": "role_dim_01",
  "question_mode": "system_design",
  "difficulty": "foundation",
  "evidence_gap": ["尚未验证任务路由", "尚未验证人工接管"],
  "scenario_strategy": "new"
}
```

`primary_dimension_id`、`difficulty` 和 `evidence_gap` 可以从 InterviewPlan 与 RuntimeState 确定性派生，不要求 LLM 重复生成已有事实。`scenario_strategy` 只允许 `new`、`continue` 和 `switch` 三种受控值。

此处 `difficulty=foundation` 表示入门难度，不等于 `question_mode=foundation`；入门难度的 `scenario` 或 `system_design` 仍可以进入场景 RAG。

### 6.2 RetrievalRequest Builder

Builder 从 Supervisor 决策、InterviewPlan 和 RuntimeState 构建检索请求：

```json
{
  "role": "ai_agent_engineer",
  "primary_dimension_id": "role_dim_01",
  "requirement_type": "system_design",
  "question_mode": "system_design",
  "difficulty": "foundation",
  "objective": "验证候选人能否设计 Agent 业务链路",
  "evidence_gap": ["任务路由", "人工接管"],
  "excluded_retrieval_unit_ids": [],
  "excluded_scenario_ids": []
}
```

硬过滤字段：

- `role`；
- `primary_dimension_id`；
- `requirement_type in supported_requirement_types`；
- `question_mode in supported_modes`；
- `difficulty`；
- `status=active`；
- `valid_from <= now <= valid_until`；
- 未命中排除 ID。

语义检索文本只使用：

- 当前 `objective`；
- 当前 `evidence_gap`；
- 受控、非隐私的候选人能力标签；
- 必要的场景偏好标签。

不把完整 JD、完整简历、候选人姓名、学校或原始回答发送给 Embedding 与 Reranker 服务。目标与证据缺口的权重高于一般 JD/简历标签，避免宽泛背景覆盖当前单一验证目标。

## 7. 检索、校验与回读

1. Qdrant 根据硬过滤和混合检索召回候选 `retrieval_unit_id`；
2. Reranker 只在同维度、兼容题型的候选中排序；
3. 选定一个 `retrieval_unit_id`；
4. Scenario Store 根据 `scenario_id` 和 `module_id` 从 JSON 回读完整场景与命中模块；
5. Retrieval Validator 再次确定性校验：
   - `module.primary_dimension_id == current_dimension_id`；
   - `requirement_type in module.supported_requirement_types`；
   - `question_mode in module.supported_modes`；
   - 难度与当前阶段兼容；
   - Scenario、Module 和 Constraint 都是 active 且未过期；
   - 记录的内容哈希与版本一致。

失败结果不得进入 Question Generator。

## 8. Constraint Selector

Constraint Selector 是确定性 Python 服务，不是独立 LLM Agent。它只在已选定的 ScenarioModule 内选择约束。

候选集必须同时满足：

- 约束属于当前 `module_id`；
- 约束未在 `revealed_constraint_ids` 中使用；
- `evidence_gap_tags` 与当前 Evidence Gap 匹配；
- 难度与当前面试阶段兼容；
- 约束为 active。

选择顺序为：Evidence Gap 精确匹配数、难度距离、场景卡中的审核顺序、`constraint_id` 稳定排序。第一个开放问题可以不释放隐藏约束；后续轮次只释放一个新约束。

Supervisor 决定当前 Evidence Gap；Constraint Selector 只做受控匹配，不重新定义要验证的能力。

## 9. Question Generator 合同

Question Generator 的输入已锁定：

- 当前 Requirement 和 Evidence Gap；
- 命中的 Scenario 和 Module；
- 当前 `selected_constraint_id` 及其事实；
- 允许使用的候选人背景摘要；
- 语气和长度约束。

它可以改写：

- 语气、长度和口语程度；
- 根据候选人经历增加自然引导；
- 在不改变事实的前提下表达当前锁定约束。

它不得改变：

- Requirement、正式评分维度和 Evidence Gap；
- Scenario、Module 和 Constraint ID；
- 已审核的业务事实、工具能力与风险边界；
- 当前未被 Constraint Selector 释放的其他约束。

第一问不直接列出 `evidence_signals`、`critical_errors` 或隐藏约束。

### 9.1 问题输出

```json
{
  "text": "我们要设计一个电商智能客服，需要支持商品咨询、订单查询和退款。你会怎么设计整体架构？",
  "target_requirement_id": "req_01",
  "primary_dimension_id": "role_dim_01",
  "retrieval_unit_id": "ecommerce_service::agent_architecture",
  "scenario_id": "ecommerce_service",
  "module_id": "ecommerce_agent_architecture",
  "selected_constraint_id": null,
  "revealed_constraint_ids": [],
  "retrieval_status": "hit"
}
```

`selected_constraint_id` 表示当前这道问题使用的约束；`revealed_constraint_ids` 是该场景截至当前已释放约束的累计列表。

`retrieval_status` 只允许：

- `hit`：正常检索命中；
- `fallback`：使用审核过的默认模块；
- `bypass`：当前题型按规则不走 RAG。

## 10. 运行时数据流

```text
Planner
  确定整场面试能力覆盖
    ↓
Supervisor
  选择 Requirement、Dimension、Mode、Evidence Gap
  并决定 new / continue / switch
    ↓
RetrievalRequest Builder
  构建硬过滤与受控语义查询
    ↓
Qdrant + Reranker
  选择一个“场景 × 能力模块”检索单元
    ↓
Scenario Store
  从 JSON 回读完整 Scenario 与 Module
    ↓
Retrieval Validator
  确定性阻止跑题、过期和不兼容结果
    ↓
Constraint Selector
  从当前模块选择一个匹配 Evidence Gap 的未使用约束
    ↓
Question Generator
  只把锁定信息表达成自然问题
    ↓
Candidate Answer → Evidence Processor → Supervisor
```

`scenario_strategy=continue` 时不再搜索新场景，直接在当前 Module 内选择新 Constraint。`new` 或 `switch` 时才进行场景检索。

## 11. no-match、fallback 与错误处理

- 无符合硬过滤的结果：返回 `no_match`；
- Reranker 最佳分数低于经标注样本校准的阈值：返回 `no_match`；
- JSON 回读失败、哈希不一致或校验失败：返回 `invalid_result`；
- Embedding、Qdrant 或 Reranker 不可用：返回 `unavailable`；
- 所有失败都不将未校验数据交给 Question Generator。

Fallback 使用 `DefaultModuleRegistry`，每个正式维度至少绑定一个已审核默认 Module。Fallback 必须记录：

```json
{
  "retrieval_status": "fallback",
  "fallback_reason": "no_eligible_retrieval_result",
  "module_id": "ecommerce_agent_architecture"
}
```

LLM 不得自由创造 fallback 场景。

## 12. 旧题库迁移

现有 30 道题不直接全部删除，而是按内容职责迁移：

- 业务背景进入 `ScenarioCard`；
- 能力目标、评分信号和关键错误进入 `ScenarioModule`；
- 异常条件和追问素材进入 `ScenarioConstraint`；
- 来源、时效、信任级别和审核记录保留；
- 原完整问句不再作为 RAG 的最终输出合同。

示例：

- q004 的 800 RPS、队列和人工复核迁入“电商客服 × 成本性能”；
- q009 的 Memory 写入、删除和验证迁入“企业知识助手 × Memory”；
- q013 的工具扩展和选择准确率迁入“企业知识助手 × 工具工程”；
- q023 的旧数据和降级迁入“成本监控 × 可观测性”。

## 13. 测试与验收

### 13.1 Schema 与治理

- 6 个 ScenarioCard 都通过 schema 校验；
- 6 个正式维度均至少有 2 个 active Module；
- 每个 Module 只有一个 `primary_dimension_id`；
- 所有 Constraint 都可回溯到 active Scenario 和 Module；
- 来源、时效、哈希和版本一致。

### 13.2 检索

建立经人工标注的正、负检索样本，至少覆盖：

- Agent 架构目标不得返回 Memory 或跨区故障模块；
- Memory 目标必须返回 `role_dim_03` 的 Memory 模块；
- 成本性能目标不得被一般“工具/评测”词汇覆盖；
- 题型、Requirement Type、难度和时效的硬过滤真实生效；
- Reranker 阈值由标注样本校准，不以单次 API 分数猜测。

### 13.3 动态路径

- 开放第一问不泄露 Evidence Signals；
- 每次追问最多新释放 1 个 Constraint；
- Constraint Selector 不会重复已使用约束；
- Question Generator 不会修改 Dimension、Module 或 Constraint 事实；
- `continue` 不重新检索新场景；
- `switch` 不会重用被排除场景。

### 13.4 审计和前端演示

每道问题必须可回溯：

```text
Supervisor 决策
→ RetrievalRequest
→ retrieval_unit_id
→ scenario_id / module_id
→ selected_constraint_id
→ 最终问题
→ 候选人回答
→ Evidence
```

前端以企业用户能理解的语言展示“为什么追问”，技术 ID 只放在可展开的审计详情中。

## 14. 完成标准

本设计完成实施后，系统应满足：

1. RAG 不再直接返回完整固定面试题；
2. 检索单元是唯一正式维度下的“场景 × 能力模块”；
3. 检索不会把 Memory 目标选成跨区故障模块；
4. Supervisor 决定验证目标，Constraint Selector 决定当前约束，Question Generator 只决定措辞；
5. 第一问自然开放，后续每轮只释放一个约束；
6. 每道问题都能追溯到 Requirement、Retrieval Unit、Scenario、Module 和 Constraint；
7. RAG 失败时使用已审核默认模块，不让 LLM 自由创造备选场景；
8. 不需要 PostgreSQL 也能在比赛 Demo 中完整运行和重建索引。
