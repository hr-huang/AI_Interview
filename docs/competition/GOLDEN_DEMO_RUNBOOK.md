# Golden Demo 会话与视频彩排手册

> 用途：准备一场可复核的比赛演示。本文中的“彩排回答”不是已发生的真人面试记录；最终视频、计划书和验证附件必须优先引用真实运行后导出的 Session Artifact。

## 1. 演示目标

比赛最值得展示的不是“模型能问很多题”，而是这一条闭环：

```text
上一轮回答
→ AnswerProcessor 形成 Evidence / Evidence Gap
→ Supervisor 继续锁定同一 Requirement 或切换目标
→ Follow-up 复用已有 Scenario（如有）
→ Constraint Selector 只释放与当前 gap 对应的一条 reviewed constraint
→ QuestionGenerator 把锁定内容问自然
→ 最终 Evidence 回到六维 Rubric / ScoreEngine
```

视频中优先展示 **一个真正因为回答内容而改变下一题的片段**，不要把时间花在连续展示很多普通题。

## 2. 首选演示主题：企业知识助手的版本与引用

首选 `enterprise_knowledge_assistant / knowledge_rag_memory`。

原因：仓库已经有固定集成回归证明以下语义链成立：候选人已经说明 Memory 删除，但没有证明“版本”和“引用”时，`latest_gap_tags` 会留下 `版本 / 引用`，后续应该选择 `knowledge_policy_version_stale`，而不是重复释放 `knowledge_memory_delete`。

这个案例能同时展示：

- Context / RAG / Memory；
- Evidence Gap；
- 动态 Follow-up；
- Scenario 复用；
- reviewed constraint；
- 版本新鲜度与引用追溯；
- 最终报告中的证据边界。

## 3. 彩排回答策略（不是实测记录）

为了让动态追问有展示价值，第一轮回答应当“部分答对，但自然留下一个真实缺口”，而不是故意答错。

### 彩排回答 A：覆盖删除，不主动覆盖版本与引用

可以围绕以下事实组织自己的真人回答：

- 文档入库后做 chunk / metadata 管理；
- 用户要求删除个人 Memory 时，删除对应持久化记录，并避免后续 Context 再加载；
- 知识文档和用户长期 Memory 分开管理；
- 删除后做一次检索或会话回归，确认旧个人信息不再出现。

**本轮不要为了配合系统而主动补齐**“制度版本过滤、effective date、source version、回答引用”这一组内容。这样如果当前 InterviewPlan / Requirement 与 Scenario 选择匹配，系统有机会真实识别“版本 / 引用”仍未证明。

### 期待的下一轮语义方向

如果真实运行形成上述 gap，下一轮应该围绕：

> 制度已经更新，但知识库中仍然保留旧版本时，如何避免旧制度继续成为当前答案，并让回答能够追溯到有效来源。

**不要提前把这句话当成真实生成问题。** 最终问题文字以真实 Session Artifact 中的 `turn.question` 为准；模型措辞可以变化，真正需要稳定的是 Requirement、QuestionMode、Evidence Gap 和 `question_provenance`。

### 彩排回答 B：补齐版本与引用验证

后续真人回答可以自然包含：

- 文档 / chunk 保存 `document_id + version + effective_at / valid_until`；
- 检索阶段先过滤当前有效版本，而不是把所有版本交给 LLM 自己判断；
- 回答携带 source / version 引用；
- 更新制度时建立可复现回归集，至少比较更新前后检索与最终回答；
- 如果新旧制度冲突或有效期不清楚，停止给确定性结论并转人工核验。

这不是“标准答案脚本”。录视频时请用你真正理解的表达回答，并允许系统根据实际内容继续追问。

## 4. 好问题的比赛验收标准

一条适合视频展示的问题应同时满足：

1. **单一主目标**：不是“请分别回答 A/B/C/D”；
2. **必须做判断**：候选人需要做设计决策、取舍或故障判断，不能只背定义；
3. **能产生证据**：回答能被判断为 supporting / limiting / unverified；
4. **追问有来由**：Follow-up 能指出上一轮仍未证明的边界，或自然加入一条 reviewed business constraint；
5. **不泄露答案**：问题不能把 expected signals、critical errors 或评分标准直接告诉候选人；
6. **职业感**：像业务技术面试，而不是课程考试或审讯。

如果真实运行的问题不满足这些标准，先保存原始记录，再修 QuestionGenerator 的具体问题并补回归测试；不要手工改 Session Artifact 里的问题。

## 5. 真实会话完成后如何冻结

完成一场真实 Assessment 并生成报告后，在仓库根目录执行：

```powershell
uv run python scripts/export_competition_session.py <assessment_id>
```

默认生成：

```text
artifacts/runs/competition/<assessment_id>/session.json
```

默认**不会**导出 JD / Resume 原文。如确实需要在本地私下归档输入，再显式执行：

```powershell
uv run python scripts/export_competition_session.py <assessment_id> --include-inputs
```

`artifacts/runs/` 已作为本地运行产物管理，不应把真人简历、原始回答或私有日志直接提交到 GitHub。

## 6. Session Artifact 应该成为视频和计划书的单一事实源

视频、PPT 和计划书引用同一场会话：

```text
输入
→ Final InterviewPlan / ScoringBlueprint
→ turns.question / turns.answer
→ question_mode
→ question_provenance
→ Evidence
→ final RequirementProgress
→ ScoreSnapshot
→ Enterprise Report
```

如果视频重新实时运行时自然语言问题与 Golden Session 略有差异：

- 不需要强行追求逐字一致；
- 先看 Requirement / QuestionMode / Scenario / Constraint / Evidence 语义路径是否一致；
- 如果实际路径不同，视频应展示实际运行，不要剪成一条从未真实发生过的路径。

## 7. 推荐的视频片段选择顺序

优先级：

1. `follow_up` 且 `question_provenance.selected_constraint_id` 非空；
2. 上一轮 Evidence 明显“部分支持但仍有缺口”；
3. 下一题没有重复上一题已经证明的内容；
4. 回答后 RequirementProgress 明显变化；
5. 最终报告能够从该维度回到对应原始回答。

如果首选知识助手案例没有自然形成理想片段，可以再从以下 reviewed constraint 中选择备用案例，但必须重新真实运行：

- `refund_timeout_after_success`：退款已经成功但接口超时，适合展示幂等 / 失败恢复；
- `coding_delivery_hidden_regression`：代码能运行但隐藏路径回归，适合展示测试 / 调试；
- `knowledge_security_malicious_document`：RAG 文档含恶意指令，适合展示安全与权限；
- `itops_observability_conflicting_signals`：Logs / Metrics / Traces 矛盾，适合展示可观测性判断。

不要为了“戏剧效果”把这些 constraint 同时塞进一轮；当前架构每次只允许按缺口逐步释放 reviewed constraint。
