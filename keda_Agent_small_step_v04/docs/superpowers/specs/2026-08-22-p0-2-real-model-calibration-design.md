# P0-2 真实模型端到端校准设计

> 状态：设计已确认，待实施计划
> 日期：2026-08-22
> 模型：Xiaomi MiMo `mimo-v2.5`
> 岗位范围：AI Agent / AI 应用工程师（`ai_application_engineering / 2026-H2`）

## 1. 目标

P0-2 用真实模型验证并校准现有证据驱动评估链路，确保系统不会因为候选人未穷举参考答案而机械扣分，也不会把术语堆砌、未经验证的能力或缺失回答误判为高分或零分。

本阶段不调整产品岗位范围，不开发 Web 页面，不引入第二个模型，也不让模型同时扮演候选人和评估者。

## 2. 核心原则

1. 候选人输入必须固定：案例中的简历声明、回答和预期边界由人工编写并纳入版本控制。
2. 语义判断与数值评分分离：MiMo 负责语义绑定、Rubric 匹配和报告文案；确定性 ScoreEngine 负责等级与分数计算。
3. 校准检查边界而非逐字输出：真实模型文案可以变化，但结构、证据引用和等级范围必须满足预期。
4. 未验证不等于不会：缺少证据的 Requirement 或维度必须保持 `UNVERIFIED`，不得产生虚假数值。
5. 所有结论必须可追溯：评分原因、优势、风险和建议必须引用存在的 Evidence ID。
6. 先隔离语义层，再验证完整面试：第一层稳定后才运行 Supervisor 全链路，避免失败时无法定位责任模块。

## 3. 两层校准架构

### 3.1 第一层：报告边界校准

输入使用冻结的：

- InterviewPlan
- InterviewRuntimeState
- InterviewTurn
- Evidence
- ClaimRegistry
- Role Pack

真实运行：

```text
冻结结构化输入
    ↓
MiMo Scoring Blueprint Builder
    ↓
MiMo Rubric Matcher
    ↓
RequirementEvidenceAssessment
    ↓
确定性 ScoreEngine
    ↓
MiMo Report Writer（失败时允许确定性降级）
    ↓
AssessmentReport + CalibrationResult
```

这一层回答：给定相同证据，系统是否正确理解证据并形成合理评分。

### 3.2 第二层：完整面试路径校准

输入使用冻结的简历、JD 和候选人回答脚本。候选人回答脚本不按问题原文匹配，而按 Requirement 或语义场景提供回答，以允许 Supervisor 动态改变题目文本。

真实运行：

```text
冻结简历 + JD
    ↓
面试前理解与 Planner
    ↓
Supervisor / QuestionGenerator
    ↓
脚本化候选人回答
    ↓
AnswerProcessor / Evidence
    ↓
动态追问直至结束
    ↓
AssessmentReport
    ↓
路径断言 + 报告断言
```

这一层回答：系统是否问到了必要问题、发现了能力边界，并把真实面试产生的证据正确送入报告链路。

第二层复用第一层的六个候选人画像，但不要求生成完全相同的问题文字或完全相同的 Evidence 数量。

## 4. 六类黄金案例

### C01 强能力、强证据

- 能清晰说明 Agent 状态、节点、工具边界、失败恢复、评测和安全取舍。
- 关键 Requirement 应达到 L3；只有存在独立新场景迁移证据时才允许 L4。
- 门槛维度均被验证后，岗位匹配分可以发布。

### C02 术语丰富但缺少实践细节

- 回答包含 Agent、RAG、Workflow、Memory 等术语，但没有具体结构、步骤、验证方法或取舍。
- 不得仅凭关键词满足最低充分条件。
- 预期主要落在 L1，证据不足处允许 `UNVERIFIED`。

### C03 项目经验强但迁移能力弱

- 能解释已有项目，但面对新场景时机械复用原方案或无法说明适配过程。
- 已有项目证据不得被抹掉；迁移能力也不得被虚假拔高。
- 典型结果为核心 Requirement 达 L2～L3，`transferability` 为 weak 或 medium，不得达到 L4。

### C04 关键点充分但未穷举

- 参考标准可能包含五条信号，候选人稳定答出其中三至四条关键内容，并展示正确性、推理和取舍。
- 不按命中条数比例机械换算分数。
- 满足最低充分条件且质量较强时必须允许 L3。

### C05 严重安全或可靠性错误

- 候选人明确主张让模型在无授权、无人工确认或无防护情况下执行高风险操作，或给出同等级严重错误。
- 必须命中对应 Critical Error，并保留支持证据与限制证据。
- 受影响 Requirement 应按现有规则降级；错误不得被优秀术语抵消。

### C06 多个领域未验证

- 面试只覆盖部分能力维度，其余维度没有可靠证据。
- 未覆盖维度必须为 `UNVERIFIED` 且 `score=None`。
- 覆盖率不足或门槛维度未验证时，岗位匹配分不得发布。

## 5. 案例数据契约

每个案例保存以下内容：

```text
CalibrationCase
  id
  title
  description
  input
    target_role
    plan / runtime / turns / evidences / claims
    resume / jd / scripted_answers（第二层使用）
  expectation
    required_rubric_hits
    forbidden_rubric_hits
    requirement_level_ranges
    expected_unverified_requirements
    expected_unverified_dimensions
    job_match_publication
    required_claim_statuses
    required_path_coverage（第二层使用）
```

预期值只描述稳定业务边界，不冻结自然语言报告全文，也不冻结由模型生成的解释措辞。

案例文件不得包含 API Key、真实候选人隐私或比赛外部敏感数据。

## 6. 自动断言

### 6.1 通用结构断言

- 输出能够通过现有 Pydantic Schema 校验。
- Blueprint 覆盖计划中的全部 Requirement，且只引用当前 Role Pack 中的维度。
- RubricMatch 只能引用输入中存在的 Evidence、Requirement 和 Rubric 条目。
- 报告只引用存在的 Evidence ID。
- 相同 Evidence 不产生互相矛盾且无法解释的匹配。

### 6.2 评分边界断言

- Requirement 等级处于案例允许范围内。
- `UNVERIFIED` 不产生数值分数。
- L4 必须具有独立迁移证据。
- Critical Error 必须形成 limiting evidence，不能被普通优秀信号抵消。
- 每个已评分雷达维度至少有两个可追溯原因。
- 岗位匹配分发布状态符合覆盖率和门槛规则。

### 6.3 Claim 断言

- Claim 不能因为技术能力得分较高而自动判真。
- 缺少基线、样本、周期或直接证据的量化声明不得判为完全支持。
- Claim 状态必须引用与该 Claim 关联的 Evidence。

### 6.4 完整路径断言

- must-cover Requirement 在结束前均被访问，或结束结果明确记录无法完成的原因。
- Supervisor 对已有充分证据不进行无意义重复追问。
- C03 必须出现迁移场景验证。
- C05 必须出现安全或可靠性边界验证，或已有回答已直接暴露严重错误。
- C06 不得为了凑覆盖率把没有证据的维度强行标记为已验证。

## 7. 稳定性与重复运行

真实模型具有非确定性，因此分两类检查：

- 确定性阶段：相同 Blueprint、RubricMatch 和 Evidence 输入必须生成完全相同的 ScoreSnapshot。
- 语义阶段：每个案例重复运行建议三次，三次均需通过硬性安全与证据断言；等级允许在预先冻结的相邻范围内波动。

测试默认不比较报告全文，不以单次成功替代重复校准。若同一案例频繁越界，应优先检查 Prompt、Rubric 定义和证据粒度，而不是扩大允许范围掩盖问题。

## 8. 执行入口与产物

提供独立校准命令，避免把真实 API 测试混入默认离线单元测试：

```powershell
python run_report_calibration.py
python run_report_calibration.py --case C04 --runs 3
python run_interview_calibration.py --case C05
```

默认单元测试继续保持无 API Key、无网络即可运行。真实校准命令显式读取 `MIMO_API_KEY`。

每次运行生成机器可读和人工可读结果：

```text
artifacts/calibration/<timestamp>/
  summary.json
  summary.md
  C01/run-01/input.json
  C01/run-01/blueprint.json
  C01/run-01/rubric_matches.json
  C01/run-01/report.json
  C01/run-01/assertions.json
```

产物目录默认不提交 Git；黄金案例定义和期望规则必须提交 Git。

## 9. 失败定位

校准失败按阶段报告，不只给出“结果不符合预期”：

- Blueprint 失败：Requirement 到岗位维度的语义绑定错误。
- Rubric 失败：证据误匹配、漏匹配或质量判断偏差。
- Assessment 失败：多个匹配汇总为 Requirement 等级时发生偏差。
- Score 失败：确定性等级、分数、覆盖率或发布门槛错误。
- Narrative 失败：出现无 Evidence 支持的结论或引用错误。
- Interview Path 失败：Supervisor 未验证必要能力、重复追问或过早结束。

## 10. 对 Role Pack 的调整规则

当前六维 Role Pack 在本阶段默认冻结。只有满足以下条件才允许提出修改：

1. 同类人工标注案例重复出现系统性误判；
2. 已排除案例写法、Evidence 提取和 Prompt 问题；
3. 修改能明确提升区分度，而非单纯迎合某个样例；
4. 修改后重新运行全部六类案例，且不破坏既有边界。

任何 Role Pack 修改必须记录原因、修改前后差异和受影响案例。

## 11. 验收标准

P0-2 完成需要同时满足：

1. 六类第一层案例均可通过真实 MiMo 完整运行。
2. 六类案例均有版本化输入、人工预期和自动断言。
3. C04 不因未穷举全部细节而被机械低估。
4. C02 不因术语丰富而被误判为满足最低条件。
5. C05 的严重错误被稳定识别且不能被优秀信号抵消。
6. C06 的未验证维度保持无数值，岗位匹配分按规则不发布。
7. 所有报告结论和评分原因均可回溯到 Evidence。
8. 确定性 ScoreSnapshot 稳定性测试通过。
9. 六类第二层脚本化面试均完成路径验证。
10. 默认离线测试不依赖 MiMo API，现有测试保持通过。

## 12. 明确不做

- 不让 MiMo 自动生成候选人回答后再评价自己的回答。
- 不以报告文字完全一致作为通过条件。
- 不为追求单次测试通过而放宽严重错误或 Evidence 约束。
- 不在 P0-2 扩展 Java、算法、产品经理等岗位。
- 不在本阶段开发 Web UI、RAG 或实践任务系统。
