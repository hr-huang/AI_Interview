# InterviewRuntimeState 冻结设计

## 1. 设计结论

`InterviewRuntimeState` 是系统从静态面试计划进入动态面试循环的边界对象，必须在实现 Supervisor 前建立。

本设计冻结以下职责划分：

- `InterviewPlan` 是考前生成的静态计划，面试过程中不修改。
- `InterviewRuntimeState` 是当前面试进度的动态投影，只保存 Supervisor 决策所需的最小运行信息。
- `InterviewTurn` 保存原始问题、回答和轮次信息。
- `Evidence` 保存完整证据事实。
- `CompetencyModel`、`ClaimRegistry` 和 `InterviewPlan` 保持为 MainState 中彼此独立、通过稳定 ID 关联的业务对象。
- Supervisor 在一轮回答的评价、证据提取和进度更新完成后运行一次，并输出稳定格式的 `InterviewAction`。

## 2. 目标与非目标

### 2.1 目标

- 让 Supervisor 稳定知道每项 Evidence Requirement 当前验证到什么程度。
- 避免 Supervisor 每轮从全部聊天历史重新判断“是否问过、是否已经充分验证”。
- 防止重复追问、单点过度深挖和超过面试预算。
- 保持运行状态足够小，避免与 Plan、Turn、Evidence 重复存储。
- 为后续 Checkpointer、上下文压缩和 Reflection 提供清晰接口。

### 2.2 当前不实现

- 长期记忆、情景记忆、语义记忆和程序性记忆存储。
- 历史摘要和 Memory Manager。
- 独立 Coverage 数据库。
- RAG、QuestionGenerator、Reflection 和最终报告逻辑。
- 让模型直接修改剩余时间、尝试次数或 Requirement 状态。

这些能力可在动态面试最小闭环跑通后迭代，不应阻塞 Supervisor 的第一版。

## 3. 方案选择

### 3.1 未采用：每轮从完整历史重新推导

优点是新增数据结构较少；缺点是成本高、上下文持续增长，并且模型对“是否已经覆盖”的判断不稳定。

### 3.2 采用：最小动态状态投影

RuntimeState 保存 Requirement 进度、当前 Target、问题数量和计时基准；原始内容仍保存在 Turn 和 Evidence 中。这一方案在稳定性、可测试性和复杂度之间最平衡。

### 3.3 未采用：巨大 RuntimeState

不把 ResumeProfile、CompetencyModel、InterviewPlan、完整 Turn 和完整 Evidence 复制进 RuntimeState。重复数据会产生多个事实来源，后续容易不一致。

## 4. 数据结构

### 4.1 RequirementStatus

```python
RequirementStatus = Literal[
    "not_started",
    "in_progress",
    "sufficient",
    "contradictory",
    "skipped",
]
```

状态语义：

- `not_started`：尚未获得相关 Evidence，也没有针对性尝试。
- `in_progress`：已经尝试或获得部分 Evidence，但尚不足以结束验证。
- `sufficient`：支持或反对判断所需的 Evidence 已经足够；不等同于候选人表现优秀。
- `contradictory`：存在尚未解决的相互矛盾 Evidence，需要 Supervisor 决定追问或保留不确定性。
- `skipped`：因时间、优先级或终止条件明确放弃继续验证。

`sufficient` 表示“证据充分”，不是“回答正确”。负面 Evidence 充分时也可以进入 `sufficient`。

### 4.2 RequirementProgress

```python
class RequirementProgress(BaseModel):
    requirement_id: str
    status: RequirementStatus = "not_started"
    attempt_count: int = 0
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
```

约束：

- `requirement_id` 必须存在于当前 `InterviewPlan`。
- `attempt_count` 由 Python 在完成一次针对该 Requirement 的面试轮次后增加。
- Evidence ID 必须引用 MainState 中真实存在的 Evidence。
- 不在这里复制 Evidence 文本或评分理由。

### 4.3 InterviewRuntimeState

```python
class InterviewRuntimeState(BaseModel):
    question_count: int = 0
    started_at: datetime
    current_target_id: str | None = None
    requirement_progress: dict[str, RequirementProgress] = Field(
        default_factory=dict
    )
    visited_target_ids: list[str] = Field(default_factory=list)
    stop_requested: bool = False
    stop_reason: str | None = None
```

字段说明：

- `question_count`：已经正式提交给候选人的问题数量，由 Python 更新。
- `started_at`：本场面试的计时起点。剩余时间由总时长与当前时间确定性计算。
- `current_target_id`：当前主要验证的 Target；尚未开始时为 `None`。
- `requirement_progress`：以 Requirement ID 为键的动态覆盖状态。
- `visited_target_ids`：保留已进入过的 Target 顺序，用于避免无意义来回切换。
- `stop_requested`：是否已经满足结束条件。
- `stop_reason`：结束原因；仅在 `stop_requested=True` 时设置。

第一版不单独保存 `target_attempts`。Target 的总尝试次数可以从其 Requirement Progress 或 InterviewTurn 推导，避免重复事实来源。

第一版也不保存可人工修改的 `remaining_minutes`。运行时通过以下公式计算：

```text
remaining_seconds
= InterviewPlan.duration_minutes × 60
  - (current_time - started_at)
```

计算结果不得小于零。

## 5. MainState 组合关系

```python
class MainState(TypedDict, total=False):
    resume_profile: ResumeProfile
    job_profile: JobProfile
    competency_model: CompetencyModel
    claim_registry: ClaimRegistry
    interview_plan: InterviewPlan

    runtime_state: InterviewRuntimeState
    interview_turns: list[InterviewTurn]
    evidences: list[Evidence]
    next_action: InterviewAction
```

关系如下：

```text
MainState
├─ CompetencyModel        岗位能力验证地图
├─ ClaimRegistry          简历具体声明生命周期
├─ InterviewPlan          静态考前计划
├─ InterviewRuntimeState  动态进度投影
├─ InterviewTurns         原始面试历史
├─ Evidences              完整证据事实
└─ InterviewAction        Supervisor 的下一步决策
```

`InterviewPlan` 只通过 `competency_ids` 和 `related_claim_ids` 引用前置业务对象，不嵌套或复制它们。

## 6. 初始化流程

Planner 生成并通过校验的 `InterviewPlan` 后，由确定性初始化函数创建 RuntimeState：

1. 记录 `started_at`。
2. 将 `question_count` 设为零。
3. 将 `current_target_id` 设为 `None`。
4. 遍历 Plan 中所有 Evidence Requirement，为每个 ID 创建 `not_started` 状态。
5. 将停止标记设为 `False`。

初始化过程不调用 LLM。

## 7. 单轮数据流

```text
InterviewPlan + RuntimeState + 相关上下文
                    ↓
                Supervisor
                    ↓
             InterviewAction
                    ↓
            QuestionGenerator
                    ↓
              Candidate Answer
                    ↓
        Evaluation / Evidence Extraction
                    ↓
         Evidence 与 Claim/能力更新完成
                    ↓
       RuntimeState Updater（确定性更新）
                    ↓
                Supervisor
```

Supervisor 不在每次局部 State 更新后执行。并行的 Evidence、Competency、Claim 更新必须先完成 fan-in，再统一更新 RuntimeState，随后进入下一次 Supervisor 决策。

## 8. Supervisor 输入边界

Supervisor 的业务输入至少包括：

- `InterviewPlan`
- `InterviewRuntimeState`
- 当前 Target/Requirement 对应的结构化信息
- 相关 Evidence 摘要或完整对象
- 最近若干 `InterviewTurn`
- 需要核验时解析出的 Claim 内容
- 由程序计算的剩余时间

RuntimeState 中只保存 ID。进入 Supervisor 前由 Context Builder 根据 ID 解析相应 Plan、Claim 和 Evidence 内容，避免把完整对象复制进 RuntimeState。

## 9. 硬规则与错误处理

以下规则由 Python 执行，不交给 LLM：

- Question 数量不得超过 `InterviewPlan.max_questions`。
- Requirement 尝试次数不得超过后续 Policy 定义的安全上限。
- Supervisor 返回的 Target、Requirement、Claim 和 Evidence ID 必须真实存在。
- 计时到零后必须请求结束，不再生成新的普通问题。
- RuntimeState 更新必须发生在 Evidence 写入成功之后。
- 重复 Evidence ID 不得重复加入 Progress。
- `stop_requested=True` 时，必须存在非空 `stop_reason`。
- `current_target_id` 必须属于当前 Plan。

如果 LLM 输出非法引用，应拒绝该 Action，并进入可观测的重试或安全终止路径，而不是静默写入 State。

## 10. LangGraph 更新约束

- RuntimeState 由单一确定性 Updater 节点写入，避免并行节点同时覆盖整个对象。
- Evidence Extractor、Competency Update 和 Claim Verification 可以并行，但它们必须写入彼此独立的 State 字段，或使用明确 Reducer。
- 并行更新完成后 fan-in 到 RuntimeState Updater。
- Node 返回局部 State 更新，由 LangGraph Runtime 合并回 MainState。

## 11. 测试标准

### 11.1 Schema 测试

- 默认状态正确。
- 非法 RequirementStatus 被拒绝。
- 可变列表使用独立的 `default_factory`。
- `stop_requested` 与 `stop_reason` 组合满足约束。

### 11.2 初始化测试

- Plan 中每个 Requirement 都生成且只生成一个 Progress。
- 初始状态全部为 `not_started`。
- 空 Plan、重复 Requirement ID 和非法引用被拒绝。

### 11.3 Runtime 更新测试

- 一轮回答后 Question 和 Attempt 只增加一次。
- 部分 Evidence 进入 `in_progress`。
- 证据充分可进入 `sufficient`，无论结论正面还是负面。
- 矛盾 Evidence 进入 `contradictory`。
- 重复 Evidence ID 不重复累计。
- 超过题数、尝试次数或时间时触发停止。

### 11.4 Graph 测试

- Supervisor 只在初始化后或完整轮次更新后运行。
- 并行 Evidence/Claim/Competency 更新完成前不会进入下一轮 Supervisor。
- Checkpointer 未接入时，最小循环仍可在单进程内完成两轮决策。

## 12. 后续顺序

本设计获批后的实施顺序为：

1. 修正现有 Planner 的收口问题。
2. 新增 Runtime Schema 与确定性初始化函数。
3. 设计并冻结 `InterviewAction` Schema。
4. 实现最小 Supervisor Node。
5. 实现 Question → Answer → Evidence → Runtime Update 的两轮闭环。
6. 闭环稳定后再加入 RAG、Checkpointer、历史压缩与阶段性 Reflection。

