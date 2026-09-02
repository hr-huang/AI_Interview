# Roadmap

当前主链已经完成：

```text
JD / Resume
→ InterviewPlan
→ Plan Review & Freeze
→ Dynamic Interview
→ Evidence
→ Deterministic Scoring
→ Enterprise Report
```

后续演进重点不是继续增加 Agent Node，而是补足长期状态、知识更新、外部证据验证、产品化与生产部署能力。

## Agent 能力

### 长期记忆（Long-term Memory）

计划在当前单次 Interview Runtime 之外增加跨场次记忆，并区分：

- **语义记忆（Semantic Memory）**：候选人的稳定事实、项目背景、已确认技能与岗位相关信息；
- **情景记忆（Episodic Memory）**：历史面试问题、回答、Evidence、矛盾点和未完成验证项；
- **程序性记忆（Procedural Memory）**：经过测试证明有效的提问、追问和验证策略。

重点处理写入时机、来源追溯、冲突更新、删除、过期和跨候选人隔离。程序性记忆可以影响提问策略，但不能自动修改 Role Pack、Rubric 或评分权重。

### 场景库持续更新（Continuous Scenario Intelligence）

定期从公开岗位 JD、官方技术文档、工程案例和面试方向中发现新的候选场景。

```text
公网检索
→ 来源归一化
→ 去重 / 时效检查
→ Candidate Scenario
→ 人工 Review
→ Retrieval Calibration
→ Versioned Scenario Bank
```

未经审核的网页内容不会直接写入正式 RAG。

### Agent 可观测性与回放（Observability & Replay）

记录每轮关键决策：

- Supervisor 当前选择的 Requirement；
- Evidence Gap；
- Scenario Retrieval；
- Constraint Selection；
- Question / Answer Processing；
- RequirementProgress 变化；
- Follow-up / Switch / Finish 原因；
- latency、Token 与模型调用费用。

目标是支持按 Turn 回放完整决策路径。

### 外部证据验证工具（Evidence Tools）

只在外部证据确实有价值时引入 Tool Calling，例如：

- Git Repository / GitHub 项目检查；
- 项目依赖、测试、文档和提交记录读取；
- 受限 Sandbox 中的代码执行；
- 运行日志和测试结果核验。

Tool 只提供外部事实，结果仍进入统一 Evidence Pipeline，不能直接决定候选人分数。

## 产品能力

### 企业工作台（Enterprise Workspace）

- 候选人与 Assessment 列表；
- 等待面试 / 面试中 / 已完成状态；
- 邀请链接管理；
- 报告归档与历史评估检索；
- 搜索、筛选和状态管理。

### Role Pack 扩展

在 `ai_application_engineering / 2026-H2` 之外扩展更多技术岗位。每个岗位独立维护：

- Competency Dimensions；
- Evidence Requirements；
- Rubric；
- Scenario Bank；
- Calibration Cases；
- Profile Version。

不同岗位复用同一 Interview Engine，不为每个岗位复制一套 Agent Graph。

### 语音与虚拟数字人面试

在现有文字面试之上增加 STT、TTS、实时字幕与 Avatar：

```text
Candidate Voice
→ STT
→ Existing Interview Runtime
→ Question
→ TTS / Avatar
```

多模态只作为交互层，不根据外貌、声音特征或表情进行能力评分。

## 部署与安全

- 企业账号、Organization / Tenant 与 Assessment Ownership；
- Candidate Token 过期、撤销、重新生成和访问控制；
- 将 BYOK Secret 从单进程内存迁移到持久化 Secret Store；
- 正式数据库、迁移机制和多实例共享状态；
- 无需 API Key 的公开冻结 Demo 与后续公开部署版本。

## 维护规则

- 尚未完成的方向留在本文件；
- 完成并经过测试后，移动到 README 的核心能力 / 工程设计或对应技术文档；
- 不在 README 首页长期堆放大量已完成或未完成的 checklist。
