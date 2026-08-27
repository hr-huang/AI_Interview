# keda Profile Agent — Pre-Interview + Interview v0.5

当前版本已经实现两个彼此独立的 Graph：

- `PreInterviewGraph`：读取 Resume + JD，生成静态的 `InterviewPlan` 与 `ClaimRegistry`；它不启动计时，也不进入候选人互动循环。
- `InterviewGraph`：在候选人真正开始面试时读取前者的结果，初始化 Runtime，启动 Supervisor 驱动的提问、回答和结束循环。

整体流程是：

```text
Resume + JD
   ↓
Input Processing
   ↓
Resume Understanding   Job Understanding
        \               /
         \             /
          Competency Modeling
          ├─ CompetencyModel
          └─ ClaimRegistry
                 ↓
           Interview Planner
                  ↓
      InterviewPlan + ClaimRegistry
                  ↓ 候选人开始面试
           独立 InterviewGraph
                  ↓
       Supervisor → QuestionGenerator
           ↑              ↓
           └─ AnswerProcessor ← 候选人回答
                  ↓
                Finish
```

`InterviewPlan` 是静态的“考前计划”；`InterviewRuntimeState`、`InterviewTurn` 和
`Evidence` 是面试过程中的动态数据。计时在候选人真正进入 `InterviewGraph` 时初始化，
不在生成计划时启动。

## 动态面试中的职责

- **Planner = 考什么**：根据 `CompetencyModel` 和 `ClaimRegistry` 规划要验证的 Target、Evidence Requirement、优先级和推荐题型。
- **Supervisor = 下一步怎么考**：确定下一步考哪个 requirement、使用哪种 `question_mode`，以及何时结束。
- **QuestionGenerator = 生成什么**：根据 Supervisor 的 `AskAction` 生成展示给候选人的真实问题文本。
- **AnswerProcessor = 如何解释回答**：一次 LLM 调用同时提取 Evidence 并评估回答；随后由 Python 确定性地更新 `InterviewRuntimeState`。

展示问题前，Graph 会先记录 `question_count`、对应 requirement 的
`attempt_count` 和未回答的 `InterviewTurn`，再进入 interrupt。这样 checkpoint 中已经
明确记录“这道题已经正式问出”，重连或恢复时不会重复计数，也能让题数和时间预算规则
在真正展示问题之前生效。

默认的 `build_interview_graph()` 使用 `InMemorySaver`。它只在当前 Python 进程内保存
thread checkpoint，进程重启后不会保留，也不是生产环境的持久化方案；生产部署需要替换
为持久化 checkpointer。

## 面试结束后的 AssessmentReport 阶段

`InterviewGraph` 结束后，原始 `InterviewTurn` / `Evidence` 历史保持不可变，
`AssessmentReportService` 使用版本化的 `ai_application_engineering / 2026-H2`
Role Pack 编排以下链路：

```text
Role Profile
   ↓
Scoring Blueprint → Rubric Matches
   ↓
RequirementEvidenceAssessmentBuilder（确定性证据汇总）
   ↓
Claim Verification → ScoreEngine（确定性数值评分）
   ↓
Report Writer / 确定性 fallback
   ↓
AssessmentReport
```

ScoreEngine 是唯一生成 Requirement、能力维度和岗位匹配数值的组件；Radar、PDF 或
前端只读取 `AssessmentReport`，不重新计算分数。未验证维度保留 `UNVERIFIED` 和
`score=None`，岗位匹配分仅在覆盖率与 gating 条件同时满足时发布。报告文案失败时
只降级文案，不丢失 `ScoreSnapshot`。Task 9 的集成测试全部使用 Fake semantic
services，不调用真实 LLM。

报告阶段的离线验证命令（项目根目录执行）：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_assessment_report_service -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe check_without_llm.py
.\.venv\Scripts\python.exe -m compileall -q profile_agent tests run_pre_interview.py run_interview_demo.py
git diff --check
```

## 这版最重要的优化

### 1. 正式 Python package，解决导入混乱

旧版：

```python
from service.job_service import parse_job
from state.main_state import MainState
```

这依赖 IDE/工作目录刚好把项目根目录当作 import root，并且 `service`、`state` 名字过于泛化。

现在统一：

```python
from profile_agent.services.job_service import parse_job
from profile_agent.state.main_state import MainState
```

项目根目录执行一次：

```bash
pip install -e .
```

之后 `profile_agent` 会以 editable package 的方式安装，PyCharm/VS Code 与命令行都更稳定。

### 2. 实习/工作经历结构化

不再：

```python
experience: list[str]
```

而是：

```text
WorkExperience
├─ company
├─ role
├─ period
├─ responsibilities
├─ achievements
└─ technologies
```

这样不会丢掉“在什么公司、什么岗位、做了什么、用了什么、结果是什么”的关系。

### 3. 项目结构化

```text
ProjectExperience
├─ name
├─ description
├─ responsibilities
├─ achievements
└─ technologies
```

### 4. Claim 扫描整份简历

`claims_to_verify` 不只从 projects 来，可以来自：

- summary
- skills
- work_experience
- project
- achievement
- award / certification
- other

并且每条 Claim 保存：

```text
text
source_section
claim_type
```

### 5. CompetencyModel 不提前评分

不再做：

```text
target_level=4
estimated_level=2
gap=2
```

现在只保存：

```text
岗位希望什么
简历已有何种 signal
还缺什么 interview evidence
```

### 6. Competency ID 由 Python 生成

LLM 只负责语义内容，Python 生成：

```text
competency_01
competency_02
...
```

后续 InterviewPlan/Evidence 可以稳定引用。

## Windows 启动方式

以下命令在项目根目录执行。虚拟环境可选，但推荐使用：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
copy .env.example .env
# 编辑 .env，填写 DEEPSEEK_API_KEY
python check_without_llm.py
python run_pre_interview.py
python run_interview_demo.py
```

如果使用 `cmd.exe`，激活命令为 `.venv\Scripts\activate.bat`。其中：

- `check_without_llm.py` 是不调用模型的结构自检；
- `run_pre_interview.py` 只运行并展示 Pre-InterviewGraph 的静态结果；
- `run_interview_demo.py` 会自动先运行 Pre-InterviewGraph，再启动独立的 InterviewGraph，不需要手工复制任何 Python 对象。

离线测试命令如下，不需要真实 API：

```powershell
python -m unittest tests.test_interview_demo
python -m unittest discover -s tests -p "test_*.py"
python -m compileall profile_agent tests run_interview_demo.py
```

真实 demo 会调用模型：Pre-Interview 的结构化解析、问题生成和回答处理都可能产生 API
费用，并可能因 key、网络、限流或模型返回而启动失败。`tests/test_interview_demo.py`
使用 Fake graph 和 Fake input/output 验证 CLI 循环，不需要真实 API。

## 题库校验与 Qdrant 索引管理

版本化 JSON 题库是唯一事实源，Qdrant 只是可以随时重建的本地检索索引。下面的命令均在
项目根目录、PowerShell 中执行：

```powershell
# 只读：校验 schema、来源、生命周期和内容哈希
.\.venv\Scripts\python.exe run_question_bank.py validate --bank path\to\questions.json
.\.venv\Scripts\python.exe run_question_bank.py audit --bank path\to\questions.json

# 默认只预览，不创建 embedding 客户端，也不写 Qdrant
.\.venv\Scripts\python.exe run_question_bank.py rebuild --bank path\to\questions.json
.\.venv\Scripts\python.exe run_question_bank.py sync --bank path\to\questions.json

# 只有显式 --apply 才会调用 embedding API 并更新本地索引
.\.venv\Scripts\python.exe run_question_bank.py rebuild --bank path\to\questions.json --apply
.\.venv\Scripts\python.exe run_question_bank.py sync --bank path\to\questions.json --apply

# 脚本可消费的机器摘要
.\.venv\Scripts\python.exe run_question_bank.py audit --bank path\to\questions.json --format json

# Task4 离线题库治理（只读；报告写入 artifacts/question_corpus）
.\.venv\Scripts\python.exe run_question_bank.py audit-corpus --corpus-dir path\to\question_corpus --dry-run --format json
.\.venv\Scripts\python.exe run_question_bank.py manifest --corpus-dir path\to\question_corpus --dry-run
.\.venv\Scripts\python.exe run_question_bank.py evaluate-local --corpus-dir path\to\question_corpus --dry-run
```

`--apply` 会为题库问题调用 SiliconFlow `BAAI/bge-m3` embedding，可能产生费用并受网络、
限流和服务可用性影响；建议先运行 dry-run。校验、审计和 dry-run 不读取环境变量，也不会
调用 `load_dotenv`，因此不会把 `.env` 中的 key 带入当前进程；只有 bank、参数和路径预检
通过后的显式 `--apply` 才会发现环境配置。可用变量名如下；这里仅列出注释，表示“保持未
设置”，不要把注释改成 `NAME=` 空值：

```text
# 必须仅在本机 apply 前设置，禁止提交或复制到仓库
# SILICONFLOW_API_KEY
# 未设置时使用程序默认值；需要覆盖时再在本机设置
# QUESTION_RAG_EMBEDDING_MODEL（优先）
# SILICONFLOW_EMBEDDING_MODEL
# SILICONFLOW_EMBEDDING_BASE_URL
# QUESTION_RAG_INDEX_VERSION
# QUESTION_RAG_INDEX_PATH
# QUESTION_RAG_EMBEDDING_PROVIDER
# QUESTION_RAG_EMBEDDING_DIMENSION
```

执行 apply 前仅在本机 `.env` 或进程环境中设置 `SILICONFLOW_API_KEY`，不要把真实 key 写入
仓库、题库、日志或命令输出。未设置 model、base URL、index version、index path 时分别
使用程序默认值；若显式 CLI 提供空值或不可用路径，命令会在 embedding/store 之前以 code 2
拒绝。环境变量的空白值按未设置处理；`QUESTION_RAG_INDEX_PATH` 指向本地 Qdrant 路径，未设置时使用
`data/qdrant-question-index`。

默认索引身份与运行时一致：provider=`siliconflow`、model=`BAAI/bge-m3`、dimension=`1024`、
index version=`questions-v1`；`QUESTION_RAG_EMBEDDING_*` 覆盖项会在 CLI 与运行时使用相同的
优先级解析。模型优先读取 `QUESTION_RAG_EMBEDDING_MODEL`，未设置时兼容旧变量
`SILICONFLOW_EMBEDDING_MODEL`，两者都未设置才使用 `BAAI/bge-m3`。

命令退出码固定为：`0` 成功，`1` embedding/Qdrant 等操作失败，`2` 参数、配置或题库校验
失败。`tests/fixtures/question_rag/minimal_question_bank.json` 是明确标记的
`test_only` synthetic fixture，生产 CLI 会拒绝它；当前仓库没有真实题库，下一份规格再补
充经审阅的生产问题。

## 当前设计边界

- `CompetencyModel`：能力验证地图。
- `ClaimRegistry`：具体简历声明的验证生命周期。
- 两者**没有合成同一个对象**。
- `InterviewPlanner` 会把两者合流规划，不能 Competency 自己出一套题、Claim 又自己出一套题。
- Claim 不是一种题型；它应尽可能通过正常的项目深挖/场景/追问等问题顺带获得 Evidence。
