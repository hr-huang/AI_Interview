# keda Profile Agent — Pre-Interview v0.4

这一版只实现到：

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
           InterviewPlan
                 ↓
                END
```

此外，项目已经提供 `InterviewRuntimeState` Schema 与确定性 Service，供下一阶段
Interview Graph 使用；它尚未接入当前 Pre-Interview Graph，也不表示 Supervisor 或
动态面试循环已经实现。计时在候选人真正进入面试时初始化，不在生成计划时启动。

**Supervisor / QuestionGenerator / Evidence 等后续阶段故意没有提前实现**，留给后续按小步学习继续写。

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

## 推荐运行方式

```bash
pip install -e .
copy .env.example .env   # Windows 也可手工复制
# 在 .env 填 DEEPSEEK_API_KEY
python check_without_llm.py
python run_pre_interview.py
```

其中 `check_without_llm.py` 不调用任何模型，建议先运行它确认本地 package/import 正常。

## 当前设计边界

- `CompetencyModel`：能力验证地图。
- `ClaimRegistry`：具体简历声明的验证生命周期。
- 两者**没有合成同一个对象**。
- 但后续 InterviewPlanner 必须把它们合流规划，不能 Competency 自己出一套题、Claim 又自己出一套题。
- Claim 不是一种题型；它应尽可能通过正常的项目深挖/场景/追问等问题顺带获得 Evidence。
