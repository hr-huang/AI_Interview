# Scenario Bank v1 冻结清单

## 版本范围

```text
role_family: ai_application_engineering
role_profile_version: 2026-H2
scenario_count: 10
retrieval_module_count: 35
```

本文档是 Scenario Bank v1 的数据范围基线。通用业务代码不得写死 10 或 35；只有 `2026-H2` release test 可以断言该数量。

冻结原则：

- 一个 ScenarioCard 表示一个完整企业业务世界；
- 一个 ScenarioModule 只对应一个 `primary_dimension_id`；
- 一个 ScenarioModule 对应一个 Qdrant Retrieval Unit 和一个主向量；
- QuestionGenerator 基于命中 Module 生成问题，不存预先固定的最终问句；
- 每项雷达能力至少有 4 个不同业务场景入口。

## 正式维度

| ID | 名称 |
|---|---|
| `role_dim_01` | Agent架构与任务编排 |
| `role_dim_02` | 业务理解与任务建模 |
| `role_dim_03` | Context、RAG、Memory与工具工程 |
| `role_dim_04` | AI协作开发与生产交付 |
| `role_dim_05` | 评测、可观测性与安全治理 |
| `role_dim_06` | 成本、性能与持续优化 |

## 1. 电商智能客服 Agent

`scenario_id: ecommerce_service`

业务目标：支持商品咨询、订单查询、售后处理和退款，并在高风险操作中保证权限、安全和可靠性。

| Module | Primary dimension | 考察目标 | 约束素材 |
|---|---|---|---|
| `ecommerce_agent_architecture` | `role_dim_01` | 任务拆分、流程编排、工具路由和人工接管 | 咨询与订单走不同工具；退款为高风险动作；复杂售后转人工；部分请求需多工具协作 |
| `ecommerce_context_tools` | `role_dim_03` | 会话状态、用户偏好、商品知识、订单 Tool 与 Context | 用户中途改需求；商品知识更新；订单工具返回部分字段；用户要求删除偏好 |
| `ecommerce_safety_evaluation` | `role_dim_05` | 退款权限、Tool 风险控制、Agent 评测和人工审核边界 | 退款成功但响应超时；错判退款资格；异常高金额；自动操作越权 |
| `ecommerce_performance_cost` | `role_dim_06` | 高并发下的延迟、吞吐、成本和降级 | 大促流量十倍；峰值约 800 RPS；模型吞吐不足；部分请求进入人工复核队列 |

可追溯旧题：`q004`。

## 2. 旅行规划与推荐 Agent

`scenario_id: travel_planner`

业务目标：根据预算、时间和偏好组合交通、酒店、景点并动态调整计划。

| Module | Primary dimension | 考察目标 | 约束素材 |
|---|---|---|---|
| `travel_agent_architecture` | `role_dim_01` | 多步骤拆分、规划、工具编排与重新规划 | 交通酒店景点来自不同工具；工具有依赖；一步失败需重规划；用户中途改日期 |
| `travel_business_modeling` | `role_dim_02` | 将“帮我规划旅行”拆成目标、输入、约束和验收指标 | 预算有限；偏好模糊；多目标冲突；“推荐得好”无明确标准 |
| `travel_context_tools` | `role_dim_03` | Context、多工具结果整合和状态更新 | 酒店价格过期；景点数据缺失；用户改日期；旧工具结果需失效 |
| `travel_cost_optimization` | `role_dim_06` | 模型调用、搜索范围、质量、延迟与成本取舍 | 候选方案多；每步用大模型过贵；第三方搜索收费；速度与质量取舍 |

## 3. 企业成本监控 Agent

`scenario_id: enterprise_cost_monitor`

业务目标：监控云资源、模型调用和业务成本，识别异常并辅助处理。

| Module | Primary dimension | 考察目标 | 约束素材 |
|---|---|---|---|
| `cost_monitor_business_modeling` | `role_dim_02` | 定义成本异常、告警价值和可执行任务 | 业务正常成本不同；同比上涨不一定异常；季节性流量；误报有运营成本 |
| `cost_monitor_observability` | `role_dim_05` | 日志、指标、Trace、异常检测和降级可观测性 | 指标延迟；旧数据缓存；数据源不可用；Agent 必须说明判断依据 |
| `cost_monitor_performance` | `role_dim_06` | 成本优化、模型路由、缓存、批处理和收益取舍 | 高成本模型调用过多；部分分析可异步；缓存降低新鲜度；优化不得明显降质 |

`cost_monitor_observability` 可追溯旧题 `q023`。

## 4. 企业知识助手

`scenario_id: enterprise_knowledge_assistant`

业务目标：连接企业文档、制度和内部数据，提供可信知识问答与任务支持。

| Module | Primary dimension | 考察目标 | 约束素材 |
|---|---|---|---|
| `knowledge_rag_memory` | `role_dim_03` | RAG、知识更新、Context、Memory、检索和引用 | 制度不断更新；旧版本仍存在；需长期偏好；用户要求删除个人记忆 |
| `knowledge_production_delivery` | `role_dim_04` | 从原型到生产的测试、版本、部署和验收 | Prompt 升级改结果；知识库升级需回归；多环境配置不同；线上问题难复现 |
| `knowledge_security_evaluation` | `role_dim_05` | 权限过滤、Prompt Injection、引用真实性和评测 | 文档权限不同；恶意文档注入；引用不存在制度；敏感文档不得进普通上下文 |
| `knowledge_cost_performance` | `role_dim_06` | 检索范围、Context 长度、模型、缓存和响应时间 | 知识库扩大；Context 过长；查询高峰；部分问题可用小模型 |

`knowledge_rag_memory` 可追溯旧题 `q009`、`q013`。

## 5. 营销内容运营 Agent

`scenario_id: marketing_operations`

业务目标：根据营销目标生成、审核、发布和分析多平台内容。

| Module | Primary dimension | 考察目标 | 约束素材 |
|---|---|---|---|
| `marketing_business_modeling` | `role_dim_02` | 将“帮我做营销”转为工作流和效果指标 | 平台目标不同；保持品牌调性；曝光不等于转化；活动目标不同 |
| `marketing_ai_delivery` | `role_dim_04` | AI 协作生成、审核、版本、测试和发布 | AI 多版本；人工修改留版本；多平台格式；发布前审核 |
| `marketing_safety_evaluation` | `role_dim_05` | 内容安全、品牌风险、事实验证和质量评测 | 未证实产品承诺；版权风险；敏感主题人审；高曝光更严格 |

## 6. 招聘与面试 Agent

`scenario_id: recruitment_interview`

业务目标：处理简历、岗位要求、动态面试和胜任力评估。

| Module | Primary dimension | 考察目标 | 约束素材 |
|---|---|---|---|
| `recruitment_agent_architecture` | `role_dim_01` | Planner、Supervisor、提问、Evidence 和报告协作 | 候选人路径不同；部分能力已证明；题数时间有限；动态切换目标 |
| `recruitment_business_modeling` | `role_dim_02` | 将 JD 转为能力目标、Requirement 和验收证据 | JD 宽泛；企业需求与简历词不完全对应；能力重叠；岗位重点不同 |
| `recruitment_context_memory` | `role_dim_03` | 面试上下文、历史轮次、Evidence 和长文状态 | 面试长；历史原文多；说法矛盾；摘要不得丢原始 Evidence |
| `recruitment_safety_evaluation` | `role_dim_05` | 评估可靠性、证据追溯、公平边界和自动决策限制 | 简历无法核验；能力未考；无证据负面判断；企业要求直接录用结论 |

## 7. AI 编程与代码审查 Agent

`scenario_id: coding_review_agent`

业务目标：接收开发任务、分析代码库、修改代码、运行测试并提交人工审核。

| Module | Primary dimension | 考察目标 | 约束素材 |
|---|---|---|---|
| `coding_agent_architecture` | `role_dim_01` | 任务规划、代码检索、工具执行、测试和人审编排 | 多文件；测试失败继续修复；破坏性操作；最终必须人工 Review |
| `coding_ai_delivery` | `role_dim_04` | AI 生成代码后的审查、测试、调试和交付责任 | 可运行但逻辑错；测试不足；隐藏回归；需日志定位 |
| `coding_security_evaluation` | `role_dim_05` | 执行权限、Sandbox、Secret、安全审查和人类批准 | 读取 Secret；第三方恶意指令；改生产配置；测试访外网 |
| `coding_cost_performance` | `role_dim_06` | 代码库 Context、模型调用、工具执行和开发效率 | 大代码库；全量加载过贵；测试慢；简单与复杂任务用不同模型 |

## 8. 企业数据分析 Agent

`scenario_id: enterprise_data_analysis`

业务目标：把自然语言业务问题转为数据查询、分析和可解释结论。

| Module | Primary dimension | 考察目标 | 约束素材 |
|---|---|---|---|
| `data_analysis_business_modeling` | `role_dim_02` | 指标定义、业务澄清和分析验收 | 活跃用户定义歧义；部门口径不同；相关非因果；缺时间范围 |
| `data_analysis_context_tools` | `role_dim_03` | Schema Context、SQL Tool、元数据检索和结果验证 | 表多；字段含义不明；查询权限不同；SQL 结果可能异常 |
| `data_analysis_ai_delivery` | `role_dim_04` | AI 生成 SQL/分析后的验证、测试和可复现交付 | SQL 语法对但口径错；数据缺失；分析可复现；结论追溯查询结果 |

## 9. IT 运维与故障处理 Agent

`scenario_id: it_operations`

业务目标：监控系统状态、分析故障、调用运维工具并辅助恢复。

| Module | Primary dimension | 考察目标 | 约束素材 |
|---|---|---|---|
| `itops_agent_architecture` | `role_dim_01` | 诊断流程、工具编排、操作边界和人工升级 | 多服务同时告警；先诊断后操作；高风险恢复；需人工授权 |
| `itops_observability_safety` | `role_dim_05` | Logs、Metrics、Traces、权限、误操作和审计 | 信号矛盾；自动重启可扩大故障；日志有敏感数据；判断可审计 |
| `itops_performance_cost` | `role_dim_06` | 告警量、分析成本、响应速度和模型路由 | 每分钟数千告警；全送大模型过贵；需聚合排序；大故障要低延迟 |

## 10. 销售与客户跟进 Agent

`scenario_id: sales_followup`

业务目标：管理销售线索、客户信息、跟进任务和 CRM 操作。

| Module | Primary dimension | 考察目标 | 约束素材 |
|---|---|---|---|
| `sales_agent_architecture` | `role_dim_01` | 线索处理、信息查询、任务规划和 Tool 编排 | 客户有多任务；CRM 操作条件；部分可自动；高价值客户必须人工 |
| `sales_business_modeling` | `role_dim_02` | 定义线索质量、销售目标、动作规则和效果 | 高意向定义模糊；回复率不等于成交；产品流程不同；阶段行为不同 |
| `sales_context_memory_tools` | `role_dim_03` | 客户历史、长期 Memory、CRM Tool 和状态一致性 | 信息跨系统；历史可过期；用户修改/删除个人信息；CRM 成功但 Agent 未收到确认 |

## 覆盖矩阵验收

```text
role_dim_01: 6 个场景入口
role_dim_02: 6 个场景入口
role_dim_03: 6 个场景入口
role_dim_04: 4 个场景入口
role_dim_05: 7 个场景入口
role_dim_06: 6 个场景入口
```

总计 35 个 Module，每个正式维度至少有 4 个不同业务场景入口。

## 运行时检索规则

Supervisor 给出 `primary_dimension_id`、`question_mode` 和 `evidence_gap`。`PrepareQuestionContext` 节点内部完成：

1. 按 Role Family、Primary Dimension、Mode、Requirement Type、Difficulty、Status 和时效硬过滤；
2. 对剩余同维度 Module 做语义检索和 Rerank；
3. 根据 `retrieval_unit_id` 回读完整 ScenarioCard 与 ScenarioModule；
4. 在同一节点内使用确定性规则选择最多一个未使用 Constraint；
5. 返回锁定的 QuestionContext 给 QuestionGenerator。

Qdrant 的作用是从“同一能力的多个真实业务世界”中选择最合适的 Module，不是从 35 道固定题中找题。

## 内容维护规则

- Module 数量从 35 扩展到 50 或 80 时不修改通用业务代码；
- 只有 release test 冻结 `scenario_count == 10` 和 `module_count == 35`；
- 每个 Module 后续必须补齐 `supported_modes`、`supported_requirement_types`、`difficulty`、`opening_goal`、`semantic_text`、`evidence_signals`、`critical_errors`、`constraint_ids`、`source_refs`、`status`、`valid_from`、`valid_until`；
- 每个 Constraint 单独保存 `constraint_id`、`module_id`、`description`、`gap_tags`、`difficulty`、`source_refs`；
- 旧固定题只通过 `source_question_ids` 参与迁移和追溯，不再作为运行时最终题。
