# 企业岗位胜任力评估 Web 设计

**日期：** 2026-08-24  
**产品工作名：** 衡鉴 · Evidence Hiring  
**当前岗位范围：** AI Agent / AI 应用工程师  
**岗位标准版本：** `ai_application_engineering / 2026-H2`

## 1. 目标

建设一个面向企业招聘方的 Web 应用，将现有 Pre-Interview、动态 Interview 和 AssessmentReport 链路组成一条真实可运行的岗位胜任力评估流程：

```text
企业输入 JD + 上传候选人简历
→ 简历与岗位解析
→ 企业审核并受约束调整评估计划
→ 冻结计划与 Scoring Blueprint
→ 候选人参加动态 AI 面试
→ 生成证据驱动的胜任力报告
→ 企业查看岗位匹配、能力雷达、原因和原始问答证据
```

产品主要用户是企业 HR、技术面试官和用人负责人。候选人是被评估者，不是报告页面的主要用户。系统辅助企业决定是否进入下一轮人工面试，不自动输出录用或淘汰结论。

## 2. 比赛定位

真实入口直接覆盖赛题三项核心任务：

1. 简历解析与画像构建；
2. 基于岗位模型的多轮情景化面试与动态追问；
3. 包含能力雷达、岗位匹配、优势风险和提升建议的自动化报告。

比赛现场必须既能展示真实模型驱动流程，也能在网络或模型异常时打开一个零 API、只读的完整演示案例。演示案例不是并列主入口，只是企业创建页底部的低强调超链接。

## 3. 明确不做

第一阶段不建设：

- Java、产品经理、算法等第二岗位包；
- 企业多租户、完整账号权限和招聘管理后台；
- 在线 IDE、独立笔试平台、代码沙箱或 Git 仓库评审；
- 视频面试、语音转写或数字人；
- 自动录用、淘汰或候选人排名；
- 运行时临时网络搜索和未经审定的动态岗位权重；
- 用视觉大模型处理所有简历；
- 分布式任务队列和生产级对象存储。

## 4. 技术架构

采用前后端分离方案：

```text
React + TypeScript + Vite
├─ 企业创建评估
├─ 评估计划审核
├─ 候选人动态面试
├─ 企业评估报告
└─ 只读演示报告

            ↓ JSON / multipart HTTP API

FastAPI
├─ 文件解析与文本质量判断
├─ PreInterviewGraph 编排
├─ 评估计划修改与护栏
├─ InterviewGraph 会话与 checkpoint
├─ AssessmentReport 构建
├─ ReportViewModel 证据组合
└─ SQLite 本地持久化
```

React 不重新计算任何分数。雷达图、岗位匹配、等级、覆盖率和置信度只读取后端已经冻结的结果。

FastAPI 复用现有 `profile_agent` package，不复制 Planner、Supervisor、QuestionGenerator、AnswerProcessor 或 ScoreEngine 逻辑。

比赛版本使用 SQLite 保存评估状态和本地持久化 LangGraph checkpoint。API 和仓储边界应允许后续替换 PostgreSQL 与生产 checkpointer，但第一阶段不增加分布式队列。

耗时的 Pre-Interview 分析由进程内后台任务执行，状态写入 SQLite，前端轮询状态接口。进程重启后，遗留在 `ANALYZING` 或 `REPORTING` 的任务标记为可重试失败，不静默丢失材料。

## 5. 页面与路由

### 5.1 企业创建评估

路由：`/assessments/new`

唯一主要操作是创建真实评估。页面包含：

- 固定显示当前支持岗位族：AI Agent / AI 应用工程师；
- 可编辑 JD 文本；
- `填入 2026-H2 示例 JD` 辅助操作；
- PDF、DOCX、TXT 简历上传，或直接粘贴简历文本；
- `创建评估` 主按钮；
- `没有材料？查看已完成的演示评估 →` 低强调文字链接。

示例链接不能占据主导航或成为与真实评估并列的入口。

### 5.2 分析进度

路由：`/assessments/:assessmentId/analyzing`

展示真实阶段：文件提取、简历画像、岗位理解、能力建模和计划生成。不得展示虚假的逐 token 进度。失败时保留已经提交的 JD 与简历文本，并提供原地重试。

### 5.3 企业评估计划

路由：`/assessments/:assessmentId/plan`

展示：

- JD 结构摘要；
- 候选人画像和技术信号；
- 待核验 Claim；
- 六维能力覆盖规划；
- Planner 生成的验证目标、推荐验证方式和预算；
- 当前是否通过确定性护栏；
- 当前计划版本和冻结状态。

本页展示 Planner “考什么”，不提前生成或展示固定题目文本。

### 5.4 候选人动态面试

路由：`/interviews/:candidateToken`

候选人只看到：

- 目标岗位；
- 面试阶段；
- 已用时间；
- 当前问题与历史问答；
- 回答输入和提交状态；
- 会话已保存或恢复提示。

候选人不能看到分数、Evidence、Rubric、Supervisor 决策、剩余固定题数或企业内部备注。动态面试不承诺固定问题总数。

第一阶段只支持文本回答。提交后的答案不可直接修改；提交失败时草稿保留，候选人可以安全重试。

### 5.5 企业评估报告

路由：`/assessments/:assessmentId/report`

采用已经确认的“数据审计版”视觉方向：深色决策摘要与浅色证据正文，使用网格、刻度、雷达、能力条形图和动态追问路径，避免玻璃效果、发光渐变、装饰性大圆角和通用 AI 模板感。

报告包含：

- 岗位匹配度、适配等级、覆盖率和置信度；
- 从数据动态渲染的能力雷达；
- 每个维度的分数、等级、覆盖率和置信度；
- 加分、限制、关键错误和未验证原因；
- Requirement 分解；
- Claim 核验；
- 动态追问路径；
- 用人决策提示与复试验证建议；
- Role Pack、评分引擎和知识基线版本；
- 评估限制。

`UNVERIFIED` 必须显示为灰色未验证，不绘制为 0 分，也不能被文案描述为能力不足。

### 5.6 演示报告

路由：`/demo/assessment`

读取固定校准案例，完全不调用模型。页面明确显示“演示数据 / 只读”。它复用真实报告组件和 ReportViewModel，不维护第二套硬编码页面。

## 6. JD 示例设计

真实 JD 输入始终可编辑，不能固定成某家公司的唯一版本。

系统提供一份版本化的 `AI Agent 应用开发工程师（校招/初级）2026-H2` 示例 JD。该示例由多份当前企业官方岗位信息规范化整理，不逐字复制 BOSS 直聘或单一企业页面，也不暗示产品只服务某家公司。

示例保存：

- 示例名称；
- 适用岗位族；
- 标准化 JD 文本；
- 来源 URL；
- 采集日期 `2026-08-24`；
- 内容摘要或快照哈希；
- 示例版本 `2026-H2`。

第一版公开依据包含百度官方的大模型应用开发工程师和 2027 Agent 应用全栈工程师岗位信息：

- `https://talent.baidu.com/jobs/detail/INTERN/d1ed3134-5bd8-4743-a937-acca2773b1e7`
- `https://talent.baidu.com/jobs/list?projectType=3&recruitType=GRADUATE`

## 7. 简历文件接入

现有 `parse_resume(clean_resume)` 已能将清洗后的自由文本解析成 `ResumeProfile`，包括简介、教育、技能、工作经历、项目、待核验声明和不确定项。本阶段新增的是文件到清洗文本的前置链路。

```text
PDF / DOCX / TXT
→ 类型、大小和内容签名校验
→ 原生文字提取
→ 按页或文档进行文本质量判断
→ 仅对失败页面执行 OCR
→ 清洗阅读顺序与版面噪声
→ parse_resume(clean_resume)
```

具体规则：

- 文本型 PDF 使用 PyMuPDF 提取文本块和坐标并恢复阅读顺序；
- DOCX 使用 python-docx 提取段落和表格；
- TXT 按受支持编码读取；
- 扫描 PDF、图片页或字体编码导致的乱码页进入 OCR；
- OCR 返回文字、坐标和置信度，不负责能力理解；
- OCR 后仍由现有文本 LLM 构建 ResumeProfile；
- 老式 `.doc` 第一阶段拒绝并提示转换为 `.docx` 或 PDF；
- 文件提取失败时允许用户粘贴简历文本继续。

第一阶段不使用通用视觉语言模型。视觉模型只可能作为以后复杂信息图简历的最后一级兜底。

## 8. 评估计划的受约束修改

企业可以：

- 调整验证目标优先级；
- 补充业务关注点和具体场景；
- 调整 30、45 或 60 分钟面试预算；
- 调整最低迁移验证次数；
- 删除企业自己增加的非核心目标。

企业不能：

- 删除岗位核心或 Gating 目标；
- 修改 Role Pack 六维标准；
- 修改 Rubric、能力权重或 ScoreEngine；
- 在候选人面试开始后修改计划。

企业补充目标必须映射到现有能力维度。无法映射的目标只进入补充观察，不改变雷达分数。

修改不直接覆盖 Planner 原始结果。系统保存结构化 `PlanOverride`：目标、修改类型、修改内容、操作者和时间。最终流程为：

```text
Planner 原始计划
→ 企业 PlanOverride
→ 确定性计划护栏
→ 最终 InterviewPlan
→ Scoring Blueprint 构建与校验
→ InterviewPlan + Scoring Blueprint 一起冻结
→ 创建候选人会话
```

现有 Pre-InterviewGraph 当前会直接产出已冻结 Blueprint。实现受约束修改时必须调整冻结时点：企业审核前保存 Planner 结果和 Blueprint 草案，企业确认后再生成并冻结最终 Blueprint。不得在已冻结对象上原地修改。

## 9. 状态机

```text
DRAFT
→ ANALYZING
→ PLAN_REVIEW
→ READY
→ IN_PROGRESS
→ REPORTING
→ COMPLETE
```

允许的失败状态为 `FAILED`，同时保存 `failed_stage`、安全的用户提示和可重试标记。

- `DRAFT`：材料尚未提交；
- `ANALYZING`：文件解析和 Pre-Interview 正在运行；
- `PLAN_REVIEW`：企业可以审核和受约束修改；
- `READY`：计划和 Blueprint 已冻结，候选人链接可用；
- `IN_PROGRESS`：面试已经开始，计划不可修改；
- `REPORTING`：面试结束，正在生成报告；
- `COMPLETE`：报告可查看；
- `FAILED`：某阶段失败，按失败阶段原地重试。

## 10. API 边界

第一版提供以下接口：

```text
POST /api/assessments
GET  /api/assessments/{assessment_id}
POST /api/assessments/{assessment_id}/retry
GET  /api/assessments/{assessment_id}/plan
PUT  /api/assessments/{assessment_id}/plan-overrides
POST /api/assessments/{assessment_id}/freeze
GET  /api/assessments/{assessment_id}/report

GET  /api/interviews/{candidate_token}
POST /api/interviews/{candidate_token}/answers

GET  /api/demo/assessment
```

`POST /api/assessments` 接受目标岗位、JD、简历文本或一个简历文件，以及请求幂等键。它返回评估 ID 和 `ANALYZING` 状态。

`POST /api/assessments/{assessment_id}/freeze` 完成最终计划护栏、Blueprint 构建与共同冻结，并在成功响应中返回候选人访问 URL；冻结失败时不创建候选人 token。

`POST /api/interviews/{candidate_token}/answers` 接受当前 turn ID、答案文本和提交幂等键。后端必须拒绝过期 turn，重复幂等键只返回第一次处理结果，不重复创建 Evidence 或增加题数。

## 11. ReportViewModel

现有 `AssessmentReport` 保存分数、原因和 Evidence ID，但不重复保存全部问题、回答和 Evidence 内容。Web API 新增只读组合层：

```text
AssessmentReport
+ InterviewTurn
+ Evidence
+ Role Pack Rubric 名称
→ ReportViewModel
```

`ReportViewModel` 为每个可解释结论提供：

- 维度和 Requirement；
- 分数、等级、覆盖率和置信度；
- 原因类型和文本；
- Evidence ID 与文本；
- 来源问题；
- 候选人原始回答；
- Rubric Signal ID 和可读名称；
- 面试 turn 与 question mode。

组合层只能做查找、排序和展示字段映射，不能重新解释 Evidence 或计算分数。

## 12. 异常、恢复与降级

- 文件解析失败：保留 JD，提示更换文件或粘贴文本；
- OCR 失败：不编造文字，允许人工粘贴；
- Pre-Interview 模型失败：保留材料并原地重试；
- 问题生成超时：显示整理下一问，不提前记录新问题；
- 回答处理失败：保留候选人草稿，不创建半条 Evidence；
- 回答重复提交：幂等返回，不重复计数；
- 刷新或短时断网：通过持久化 checkpoint 恢复同一 turn；
- 过期 turn 回答：返回冲突状态和当前有效 turn；
- 报告文案失败：使用现有确定性 fallback，不丢 ScoreSnapshot；
- 模型完全不可用：真实流程显示可重试故障，演示报告仍可访问；
- 核心覆盖不足：不发布岗位匹配分；
- `UNVERIFIED`：不转成 0，不生成负向结论。

## 13. 本地安全边界

比赛版本不是生产多租户系统，但仍执行：

- 通过内容签名而非文件扩展名校验上传类型；
- 限制文件大小和页数；
- 随机化存储名，拒绝路径穿越；
- 候选人使用不可猜测 token，不暴露 assessment ID；
- 候选人接口不返回企业报告、评分或内部 Evidence；
- 日志不记录完整简历、答案或 API key；
- `.env`、上传文件、SQLite 数据和可视化草稿不进入 Git；
- 页面明确说明评估为辅助决策，禁止自动录用或淘汰。

## 14. 视觉系统

视觉方向为“专业人才评估的数据审计报告”，不是通用 AI 控制台。

- 主色：深海军蓝、纸张灰白；
- 强调色：克制的陶土橙；
- 结构：细边框、明确网格、少量直角或小圆角；
- 字体：有辨识度的衬线标题搭配高可读中文正文；
- 图形：能力雷达、刻度条、证据时间线和覆盖状态；
- 动效：仅用于状态转换、展开证据和页面进入，不使用持续漂浮或发光；
- 禁止：紫色渐变、玻璃拟态、无意义粒子、装饰性大卡片和固定六维标签硬编码。

候选人页面比企业报告更克制，不显示任何可能影响回答的即时能力判断。

## 15. 测试策略

### Python

- PDF、DOCX、TXT 提取和按页 OCR 路由；
- 文件类型、大小、页数和空文本校验；
- 状态机合法与非法转换；
- PlanOverride 护栏和冻结时点；
- 核心目标不可删除；
- 自定义目标映射规则；
- answer 幂等和过期 turn 冲突；
- ReportViewModel 的 Evidence 溯源；
- `UNVERIFIED` 不等于 0；
- 演示接口不调用 LLM。

### React

- 页面根据服务端状态路由；
- 雷达维度由返回数据动态渲染；
- 未验证维度显示灰色未验证；
- 证据展开能展示问题、原回答和规则；
- 候选人页面不出现评分字段；
- 上传、分析、超时、失败和恢复状态；
- 演示入口保持低视觉优先级。

### 零 API 端到端

复用现有 Fake semantic services 和离线校准案例，验证：

```text
JD + 简历
→ 计划审核与冻结
→ 动态问题与固定候选人回答
→ Evidence
→ AssessmentReport
→ ReportViewModel
→ 浏览器报告
```

普通开发和回归测试不得调用真实模型。只保留一次由用户主动运行的真实模型冒烟验证。

### 浏览器验收

- 创建评估并进入计划页；
- 修改计划、校验并冻结；
- 候选人提交回答、刷新恢复、重复提交不重复处理；
- 面试完成后进入报告；
- 每个能力原因可追溯到原始问答；
- 演示案例在没有 API key 时仍可打开；
- 桌面端达到确认稿质量，移动端保持完整可用。

## 16. 完成定义

本阶段完成必须同时满足：

1. 企业能够提交岗位范围内的真实 JD 和真实候选人材料；
2. PDF、DOCX、TXT 可提取，失败页面才 OCR；
3. 评估计划可以受约束修改并在面试前冻结；
4. 候选人问题和追问来自真实 InterviewGraph，不是前端固定题库；
5. 会话刷新或短时断网后能够恢复；
6. 报告从真实 AssessmentReport 动态渲染；
7. 每个评分原因可以追溯到问题、原回答、Evidence 和 Rubric；
8. 未验证能力不会显示为 0 或负向结论；
9. 演示案例只读、低强调、零 API；
10. Python、React、零 API 端到端和浏览器验收全部通过。
