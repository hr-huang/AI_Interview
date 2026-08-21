# Evidence 驱动的岗位胜任力评分与报告设计

**日期：** 2026-08-21
**状态：** 已确认设计，待用户审阅书面规格
**适用版本：** keda Profile Agent v0.5 后续报告阶段

## 1. 背景与当前基线

项目当前已经贯通：

```text
Resume + JD
→ ResumeProfile / JobProfile
→ CompetencyModel / ClaimRegistry
→ InterviewPlan
→ Supervisor 动态选题
→ QuestionGenerator 生成问题
→ interrupt / resume 等待回答
→ AnswerProcessor 提取 Evidence
→ RuntimeState 更新并继续追问或结束
```

当前缺口是面试结束后的“阅卷阶段”：系统拥有结构化 Evidence，但尚不能把 Evidence 转换为可解释的能力等级、雷达图数据、岗位匹配度、优势风险和提升建议。

本设计的目标是补齐：

```text
Evidence
→ 可审计评分
→ 结构化 AssessmentReport
→ 雷达图与详细报告
```

## 2. 产品定位与比赛范围

产品定位为：

> 面向企业招聘、同时服务学生成长的岗位胜任力评估系统。

企业是主要决策使用者，学生是直接被评估者。第一版生成一份统一报告：企业获得岗位适配、风险和后续核验建议，学生获得能力差距与提升路径。报告不替企业输出“录用”或“淘汰”结论，只输出岗位适配等级。

岗位范围采用：

```text
一个通用评估引擎
└─ 一个 AI 原生软件与应用开发岗位族
   ├─ AI Agent / AI 应用工程师：第一版深度支持
   ├─ AI 算法工程师：后续扩展岗位包
   └─ AI 原生后端工程师：后续扩展岗位包
```

不为每个岗位复制一个 Agent。统一 Planner、Supervisor、QuestionGenerator、AnswerProcessor 和 Report Engine 通过不同的版本化 Role Pack 适配岗位。

## 3. 评估形式与时长

比赛版本不建设独立笔试平台或在线 IDE。默认采用 45 分钟动态面试，并在面试中嵌入轻量实战题：

- AI 生成代码或架构审查；
- 任务规格或 Prompt 改进；
- 测试与验收设计；
- 新业务场景迁移。

30 分钟只作为快速筛选模式；20、30、45、60 分钟仍可配置。完整文件、Git 仓库提交和 AI 协作实战平台属于后续增强项。

## 4. 核心设计原则

### 4.1 无证据不等于能力差

完全没有充分考察的能力标记为 `UNVERIFIED`，不记为 0 分、不生成负向结论。报告必须同时展示分数、覆盖率和置信度。

### 4.2 不按参考答案条数机械计分

参考答案被组织为：

- 最低充分条件；
- 优秀信号；
- 关键错误；
- 可接受替代方案。

候选人不必穷举全部要点。只要抓住关键问题，并表现出正确性、具体性、推理深度、权衡意识和迁移能力，就可以获得高等级。背出全部关键词但无法应用不能获得高等级。

### 4.3 LLM 理解语义，Python 裁决分数

LLM 负责把自然语言 Evidence 匹配到版本化 Rubric，不能直接输出最终能力分或岗位匹配度。Python 校验全部 ID、聚合多轮 Evidence、处理冲突并确定等级与分数。LLM 最后只根据锁定结果生成自然语言报告。

### 4.4 每个结论必须可追溯

雷达图的每个维度必须展示：

- 为什么加分；
- 为什么受到限制；
- 哪些方面未验证；
- 哪些 Requirement 贡献了分数；
- 引用的 Evidence 和 Rubric Signal；
- 为什么没有进入更高等级。

## 5. 2026-H2 AI Agent 岗位能力标准

第一版雷达图采用六个同层级岗位能力维度：

| 能力维度 | 初始权重 | 核心内容 |
|---|---:|---|
| AI 应用与 Agent 编排 | 25% | Workflow、State、Tool、Agent 边界、人机协作点 |
| 业务理解与任务建模 | 15% | 把业务问题转为输入、决策、状态、工具和验收标准 |
| Context、RAG 与工具集成 | 15% | 上下文、检索、记忆、工具参数与结果校验 |
| AI 原生工程交付 | 15% | 规格定义、AI 代码审查、测试、调试和真实系统集成 |
| 可靠性、评测与安全 | 20% | 重试、幂等、降级、评测、权限、注入与人工接管 |
| 系统思维与持续进化 | 10% | 成本、性能、扩展性、技术取舍、迁移与持续学习 |

“纯手工写代码速度”不作为高权重能力。AI 原生工程交付仍要求候选人理解代码、审查 AI 生成结果、发现错误、设计测试并对交付负责。

岗位标准必须版本化：

```python
RoleCompetencyProfile(
    role_family="ai_application_engineering",
    version="2026-H2",
    valid_from="2026-07-01",
    knowledge_as_of="2026-08-21",
    dimensions=[...],
    source_refs=[...],
)
```

历史报告永久保存生成时所用的 Role Pack 与评分引擎版本。

## 6. 整体架构

```text
JobProfile + CompetencyModel + InterviewPlan
+ RoleCompetencyProfile(versioned)
+ InterviewTurn + Evidence
        ↓
ScoringBlueprintBuilder
        ↓
RubricMatcher (LLM structured output)
        ↓
RubricMatchValidator (Python)
        ↓
ScoreEngine (Python only)
        ↓
ScoreSnapshot
        ↓
ReportWriter (LLM narrative only)
        ↓
ReportValidator (Python)
        ↓
AssessmentReport
```

### 6.1 ScoringBlueprintBuilder

在评分前建立稳定绑定：每个 Evidence Requirement 必须绑定一个主要能力维度和该 Requirement 在维度内的权重。

```python
RequirementScoringBinding(
    requirement_id="target_01_req_01",
    primary_dimension_id="role_dim_01",
    weight_within_dimension=0.40,
    rubric_id="rubric_workflow_state",
)
```

一条 Evidence 可以关联多个 Requirement，从而影响多个能力维度；同一个 Requirement 只在其主要能力维度中计分一次，防止重复加分。

### 6.2 RubricMatcher

RubricMatcher 不输出分数，只输出 Evidence 与 Rubric 的结构化匹配：

```python
RubricMatch(
    evidence_id="evidence_002",
    requirement_id="target_01_req_01",
    matched_minimum_criteria=["criterion_01"],
    matched_excellence_signals=["signal_02"],
    matched_critical_errors=[],
    accepted_alternative_ids=[],
    quality={
        "correctness": "strong",
        "specificity": "strong",
        "reasoning": "strong",
        "tradeoff_awareness": "medium",
        "transferability": "unverified",
    },
)
```

### 6.3 RubricMatchValidator

Python 必须验证：

- Evidence、Requirement、Criterion、Signal、Error ID 均存在；
- Evidence 的 Requirement 引用合法；
- Rubric 属于当前 Role Pack 版本；
- 不允许报告或评分引用未输入的事实；
- 重复匹配不能重复贡献分数或置信度。

非法匹配不能进入 ScoreEngine。

## 7. 等级与分数算法

### 7.1 Requirement 等级

| 等级 | 含义 | 基准展示分 |
|---|---|---:|
| `UNVERIFIED` | 没有充分 Evidence | 无分数 |
| `L0` | 存在明确、未解决的关键错误 | 20 |
| `L1` | 只有零散概念，无法应用 | 40 |
| `L2` | 满足基本岗位要求 | 65 |
| `L3` | 能处理主要边界、风险和权衡 | 82 |
| `L4` | 能在独立迁移场景中优化并形成系统方法 | 95 |

确定性等级门槛：

```text
无有效 Evidence
→ UNVERIFIED

存在强或中等可信的未解决 critical error
→ L0

Evidence 与问题相关，但最低充分条件未满足
→ L1

最低充分条件满足
→ 至少 L2

L2 + correctness=strong
   + specificity/reasoning/tradeoff 中至少两项 strong
   + 无未解决 critical error
→ L3

L3 + 至少一条独立迁移场景 Evidence
   + 展现成功适配、优化或系统方法
→ L4
```

不同但合理的替代方案可以通过 `accepted_alternative_ids` 满足最低充分条件。

### 7.2 等级内微调

Role Pack 可以为优秀信号和限制信号声明整数调整值。Requirement 展示分为等级基准分加已验证调整值，最终调整限制在 `[-5, +5]`。微调只改变同一等级内的展示位置，不得跨越等级门槛。

### 7.3 能力维度分数

维度内只聚合已验证 Requirement：

```text
dimension_score
= Σ(requirement_score × binding_weight)
   / Σ(verified_binding_weight)
```

覆盖率单独计算：

```text
dimension_coverage
= Σ(verified_binding_weight)
   / Σ(all_binding_weight)
```

`UNVERIFIED` 不作为 0 进入分数，但会降低覆盖率和置信度。

### 7.4 置信度

置信度只展示 `low / medium / high`，不展示虚假精确百分比。判断因素包括：

- 加权覆盖率；
- Evidence 强度；
- 独立验证方式数量；
- 多轮 Evidence 一致性；
- 是否存在未解决冲突。

同一问题的重复表述不算独立 Evidence。项目深挖、迁移场景和代码审查等不同模式可以提升验证多样性。

高置信度至少要求：覆盖率不低于 80%，核心 Requirement 有中等或强 Evidence，至少两种独立验证方式，且不存在未解决的重大冲突。覆盖率低于 60% 或只有单一弱 Evidence 时为低置信度；其余情况为中置信度。

### 7.5 岗位匹配度

只有同时满足以下门槛才发布具体岗位匹配分：

```text
所有 gating/core 能力均得到有效评估
并且
岗位加权覆盖率 >= 70%
```

否则输出“暂不计算”，同时展示已验证表现、覆盖率和缺失项。

满足门槛后，对已验证能力按岗位权重归一化聚合。分数必须与覆盖率和置信度共同展示。

| 分数 | 适配等级 |
|---|---|
| 85–100 | 高度匹配 |
| 70–84 | 较高匹配 |
| 55–69 | 有条件匹配 |
| 40–54 | 当前匹配度较低 |
| 0–39 | 存在明显岗位风险 |

门槛能力出现 `L0` 或未解决关键错误时，保留原始加权分，但适配等级最高限制为“有条件匹配”，并显示限制原因。报告仍不输出录用或淘汰建议。

## 8. 雷达图解释性

雷达图只展示岗位核心能力，不混入 Claim 可信度、面试覆盖率或表达表现。未验证维度显示为灰色“未验证”，不画成 0 分。

每个雷达维度返回：

```python
RadarDimensionResult(
    dimension_id="role_dim_01",
    name="AI应用与Agent编排",
    score=84,
    level="L3",
    coverage=0.85,
    confidence="high",
    score_reasons=[...],
    requirement_breakdown=[...],
)
```

`ScoreReason.reason_type` 至少包含：

- `strength`：为什么加分，必须引用支持 Evidence；
- `risk`：为什么受到限制，必须引用负向 Evidence；
- `unverified`：什么尚未考到，不得写成能力不足；
- `critical_error`：什么导致等级或适配上限受限。

每个已评分维度至少显示两条原因，并展示 Requirement 权重、等级、分数和 Evidence 引用。前端、PDF和 API 必须读取同一个解释对象。

## 9. 报告结构

统一 `AssessmentReport` 包含：

1. 报告摘要：岗位、标准版本、匹配度、适配等级、置信度、覆盖率；
2. 能力雷达图：六项动态能力和未验证状态；
3. 能力详情：等级、分数、原因、Evidence、风险和未验证项；
4. Claim 核验：已支持、部分支持、证据不足、存在矛盾、未核验；
5. 优势分析：必须有强 Evidence 或多条独立中等 Evidence；
6. 风险与待核验项：严格区分负向证据和无证据；
7. 动态面试路径：展示追问如何补足 Evidence 并识别边界；
8. 岗位适配场景：适合承担什么、哪些场景需要支持；
9. 个性化提升建议：关联具体能力缺口、行动和验收标准；
10. 评估限制：时长、覆盖率、未验证项、Role Pack 与评分引擎版本。

ReportWriter 不能修改 ScoreSnapshot。ReportValidator 强制检查：

- 优势必须引用支持 Evidence；
- 风险必须引用负向 Evidence；
- 未验证项不得表述成劣势；
- 所有分数与 ScoreEngine 完全一致；
- 建议必须关联具体能力；
- 禁止出现自动录用或淘汰结论。

报告文案失败时，系统使用确定性模板降级，保留全部分数、原因和 Evidence 引用。

## 10. RAG 与时效性

RAG 提供当前岗位的专业标准，不直接决定分数。知识库包含：

- 岗位能力标准和版本；
- 等级锚点；
- 最低充分条件、优秀信号、关键错误和替代方案；
- 典型业务场景；
- 常见优秀与错误回答；
- 学习资料和提升路径。

RAG 支持 Role Pack 构建、专业问题生成、Rubric 语义匹配和提升建议检索。Python ScoreEngine 是唯一有权生成最终分数的组件。

第一版 Role Pack 参考公开就业和技能趋势资料，包括：

- World Economic Forum, Future of Jobs Report 2025；
- Microsoft, 2025 Work Trend Index；
- LinkedIn Economic Graph, 2026 Labor Market Report；
- 企业真实 JD 样本与人工审定标准。

运行时不得根据一次临时网络搜索随意改变评分权重。岗位标准更新必须发布新版本。

## 11. 异常与冲突处理

- RubricMatcher 输出非法 ID：校验失败，重试一次；仍失败则本次评估失败，不静默给分。
- Evidence 不足：标记 `UNVERIFIED`，不生成负向结论。
- 正反 Evidence 冲突：保留双方，降低置信度并在报告中说明熟悉场景与迁移场景的差异。
- ReportWriter 失败：使用确定性模板，不丢失 ScoreSnapshot。
- RAG 无有效结果：使用已发布 Role Pack，不根据空检索结果改变权重。
- 核心能力覆盖不足：不发布岗位匹配分。

## 12. 测试与验收

### 12.1 规则单元测试

至少证明：

- `UNVERIFIED` 不等于 0；
- 深度充分但不穷举的回答可以达到 L3；
- 关键词齐全但无法应用只能达到 L1；
- 新增无关 Evidence 不改变分数；
- Evidence 顺序变化不改变结果；
- 重复 Evidence 不提高置信度；
- 独立验证方式提高置信度；
- 关键错误限制适配等级；
- 未验证项不会被写成劣势；
- 报告与 ScoreSnapshot 完全一致。

### 12.2 黄金案例集

为 AI Agent 岗位人工标注：优秀、基本胜任、关键词堆砌、关键安全错误、熟悉项目强但迁移弱、证据不足和正反冲突等案例。每例定义预期等级、允许分数区间、必须引用的 Evidence 和禁止生成的结论。

### 12.3 稳定性

同一结构化输入重复运行时，Python 分数、等级和 Evidence 引用必须完全一致；自然语言表达允许变化，但不得改变事实和结论。

### 12.4 端到端验收

```text
简历 + JD
→ 面试计划
→ 模糊回答与动态追问
→ 强弱和负向 Evidence
→ 六项能力分数
→ 岗位匹配度
→ 每个雷达维度可解释原因
→ 完整 AssessmentReport
```

## 13. 实施优先级

当前第一优先级是评分与报告后端，而不是先画雷达图：

1. 冻结 RoleCompetencyProfile、ScoringBlueprint 和 Report Schema；
2. 建立 AI Agent 2026-H2 Role Pack；
3. 实现 RubricMatcher 与严格校验；
4. 实现纯 Python ScoreEngine；
5. 实现 ReportWriter 与 ReportValidator；
6. 产出结构化雷达数据和 AssessmentReport；
7. 再实现雷达图、Web 报告页与 PDF 展示；
8. 增加 RAG、拟人度和端到端比赛演示。

## 14. 本阶段明确不做

- 独立笔试平台、在线 IDE 或代码沙箱；
- 文件或 Git 仓库自动评审；
- 自动录用、淘汰或候选人排名；
- 一次覆盖所有 IT 岗位；
- 让 LLM 直接生成最终分数；
- 将未验证能力记为 0；
- 生产数据库和完整企业招聘后台；
- 在没有版本审定的情况下动态改变岗位权重。

## 15. 完成定义

本阶段只有在以下条件全部满足时完成：

- 一个 AI Agent 2026-H2 Role Pack 可用；
- 同一 Evidence 输入产生确定性相同分数；
- 岗位分数发布门槛和关键风险门槛生效；
- 每个雷达维度能展示至少两条评分原因；
- 每条优势和风险可追溯到 Evidence；
- 未验证项不会被误写为能力不足；
- 报告失败可以降级但不丢分数；
- 黄金案例和完整端到端测试通过；
- 报告明确保存岗位标准与评分引擎版本。
